#!/usr/bin/env python3
"""Tiny HTTP coordination protocol used by the local multi-node runner."""

from __future__ import annotations

import errno
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, TextIO


RUNNING_STATUS = "running"
TERMINAL_STATUSES = frozenset({"passed", "failed", "cancelled"})
NODE_STATUSES = frozenset({"pending", "ready", "failed", "cleaned"})
RETRYABLE_HTTP_STATUSES = frozenset({408, 429})
DEFAULT_RETRY_DELAYS = (0.2, 0.5, 1.0, 2.0)


class CoordinatorError(RuntimeError):
    """A coordinator operation failed with a machine-readable reason."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "coordinator_error",
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class RunState:
    def __init__(
        self,
        node_ids: list[str],
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.node_ids = set(node_ids)
        self.nodes = {node_id: "pending" for node_id in node_ids}
        self.ready: set[str] = set()
        self.failed: set[str] = set()
        self.cleaned: set[str] = set()
        self.failures: dict[str, str] = {}
        self.status = RUNNING_STATUS
        self.message = ""
        self.primary_failure: dict[str, str | None] | None = None
        self.condition = threading.Condition()
        self._log = logger or (lambda _message: None)

    def mark_ready(self, node_id: str) -> None:
        with self.condition:
            self._check_node(node_id)
            current = self.nodes[node_id]
            if current == "ready":
                self._log(f"node={node_id} action=ready result=idempotent")
                return
            if self.status != RUNNING_STATUS or current != "pending":
                self._invalid_node_transition(node_id, current, "ready")

            self.nodes[node_id] = "ready"
            self.ready.add(node_id)
            self._log(f"node={node_id} action=ready state=ready")
            self.condition.notify_all()

    def mark_failed(self, node_id: str, message: str) -> None:
        with self.condition:
            self._check_node(node_id)
            existing = self.failures.get(node_id)
            if existing is not None:
                if existing != message:
                    raise CoordinatorError(
                        f"node {node_id} already reported a different failure",
                        code="failure_conflict",
                        http_status=409,
                    )
                self._log(f"node={node_id} action=failed result=idempotent")
                return

            if self.status in {"passed", "cancelled"}:
                self._invalid_terminal_transition("failed")
            current = self.nodes[node_id]
            if current == "cleaned":
                self._invalid_node_transition(node_id, current, "failed")

            self.nodes[node_id] = "failed"
            self.failed.add(node_id)
            self.failures[node_id] = message
            if self.status == RUNNING_STATUS:
                self.status = "failed"
                self.message = f"{node_id}: {message}"
                self.primary_failure = {"node_id": node_id, "message": message}
            self._log(
                f"node={node_id} action=failed state=failed run_status={self.status}"
            )
            self.condition.notify_all()

    def mark_cleaned(self, node_id: str) -> None:
        with self.condition:
            self._check_node(node_id)
            if self.nodes[node_id] == "cleaned":
                self._log(f"node={node_id} action=cleaned result=idempotent")
                return

            self.nodes[node_id] = "cleaned"
            self.cleaned.add(node_id)
            self._log(
                f"node={node_id} action=cleaned progress="
                f"{len(self.cleaned)}/{len(self.node_ids)}"
            )
            self.condition.notify_all()

    def finish(self, status: str, message: str = "") -> None:
        with self.condition:
            if status not in TERMINAL_STATUSES:
                raise CoordinatorError(
                    f"invalid terminal status: {status}",
                    code="invalid_status",
                    http_status=400,
                )
            if self.status == status:
                self._log(f"action=finish status={status} result=idempotent")
                return
            if self.status != RUNNING_STATUS:
                self._invalid_terminal_transition(status)

            self.status = status
            self.message = message
            if status == "failed" and self.primary_failure is None:
                self.primary_failure = {"node_id": None, "message": message}
            self._log(f"action=finish status={status}")
            self.condition.notify_all()

    def snapshot(self) -> dict[str, object]:
        with self.condition:
            return {
                "status": self.status,
                "message": self.message,
                "nodes": {
                    node_id: self.nodes[node_id] for node_id in sorted(self.nodes)
                },
                "ready": sorted(self.ready),
                "failed": sorted(self.failed),
                "cleaned": sorted(self.cleaned),
                "failures": {
                    node_id: self.failures[node_id] for node_id in sorted(self.failures)
                },
                "primary_failure": (
                    dict(self.primary_failure) if self.primary_failure else None
                ),
                "expected": sorted(self.node_ids),
            }

    def _check_node(self, node_id: str) -> None:
        if node_id not in self.node_ids:
            raise CoordinatorError(
                f"unknown node: {node_id}",
                code="unknown_node",
                http_status=400,
            )

    def _invalid_node_transition(
        self,
        node_id: str,
        current: str,
        requested: str,
    ) -> None:
        raise CoordinatorError(
            f"cannot change node {node_id} state {current} to {requested}",
            code="invalid_transition",
            http_status=409,
        )

    def _invalid_terminal_transition(self, requested: str) -> None:
        raise CoordinatorError(
            f"cannot change terminal state {self.status} to {requested}",
            code="invalid_transition",
            http_status=409,
        )


def _handler(
    state: RunState,
    logger: Callable[[str], None] | None = None,
) -> type[BaseHTTPRequestHandler]:
    log = logger or (lambda _message: None)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/state":
                self._send_error_json(404, "not_found", "endpoint not found")
                return
            log("method=GET path=/state status=200")
            self._send_json(200, state.snapshot())

        def do_POST(self) -> None:  # noqa: N802
            parts = self.path.strip("/").split("/")
            if len(parts) != 3 or parts[0] != "nodes":
                self._send_error_json(404, "not_found", "endpoint not found")
                return

            node_id, action = parts[1], parts[2]
            try:
                if action == "ready":
                    state.mark_ready(node_id)
                elif action == "failed":
                    payload = self._read_json()
                    message = payload.get("message", "failed")
                    if not isinstance(message, str):
                        raise CoordinatorError(
                            "failed message must be a string",
                            code="invalid_request",
                            http_status=400,
                        )
                    state.mark_failed(node_id, message)
                elif action == "cleaned":
                    state.mark_cleaned(node_id)
                else:
                    self._send_error_json(404, "not_found", "endpoint not found")
                    return
            except CoordinatorError as error:
                self._send_coordinator_error(error)
                return
            log(f"method=POST node={node_id} action={action} status=200")
            self._send_json(200, state.snapshot())

        def log_message(self, format: str, *args: object) -> None:
            return

        def _read_json(self) -> dict[str, object]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                raise CoordinatorError(
                    "invalid Content-Length header",
                    code="invalid_request",
                    http_status=400,
                ) from error
            if length == 0:
                return {}
            try:
                value = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise CoordinatorError(
                    "request body is not valid JSON",
                    code="invalid_json",
                    http_status=400,
                ) from error
            if not isinstance(value, dict):
                raise CoordinatorError(
                    "request JSON must be an object",
                    code="invalid_request",
                    http_status=400,
                )
            return value

        def _send_coordinator_error(self, error: CoordinatorError) -> None:
            status = error.http_status or 400
            self._send_error_json(status, error.code, str(error))

        def _send_error_json(self, status: int, code: str, message: str) -> None:
            log(
                f"method={self.command} path={self.path} status={status} "
                f"error={code}"
            )
            self._send_json(
                status,
                {"error": {"code": code, "message": message}},
            )

        def _send_json(self, status: int, value: object) -> None:
            body = json.dumps(value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


class LeaderCoordinator:
    def __init__(
        self,
        node_ids: list[str],
        port: int,
        log_path: Path | None = None,
        *,
        host: str = "0.0.0.0",
    ) -> None:
        self._log_lock = threading.Lock()
        self._log_file: TextIO | None = None
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = log_path.open("a", encoding="utf-8", buffering=1)
        self.state = RunState(node_ids, self._log)
        try:
            self.server = ThreadingHTTPServer(
                (host, port),
                _handler(self.state, self._log),
            )
        except Exception:
            if self._log_file is not None:
                self._log_file.close()
            raise
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._started = False
        self._closed = False

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def start(self) -> None:
        self._log(f"server=start port={self.port} status={self.state.status}")
        self.thread.start()
        self._started = True

    def close(self) -> None:
        if self._closed:
            return
        if self._started:
            self.server.shutdown()
        self.server.server_close()
        if self._started:
            self.thread.join()
        self._log(f"server=stop port={self.port} status={self.state.status}")
        self._closed = True
        if self._log_file is not None:
            self._log_file.close()

    def wait_ready(self, timeout: int, check_processes: Callable[[], None]) -> None:
        self._wait_for(
            lambda: self.state.ready == self.state.node_ids,
            timeout,
            "waiting for all nodes to become ready",
            check_processes,
        )

    def wait_cleaned(self, timeout: int) -> None:
        self._wait_for(
            lambda: self.state.cleaned == self.state.node_ids,
            timeout,
            "waiting for nodes to report cleanup",
            lambda: None,
            fail_on_terminal=False,
        )

    def raise_if_failed(self) -> None:
        snapshot = self.state.snapshot()
        if snapshot["status"] == "failed":
            raise CoordinatorError(
                str(snapshot["message"]),
                code="run_failed",
            )

    def _wait_for(
        self,
        complete: Callable[[], bool],
        timeout: int,
        description: str,
        check_processes: Callable[[], None],
        fail_on_terminal: bool = True,
    ) -> None:
        deadline = time.monotonic() + timeout
        with self.state.condition:
            while not complete():
                check_processes()
                if fail_on_terminal and self.state.status != RUNNING_STATUS:
                    raise CoordinatorError(
                        self.state.message or f"coordinated run is {self.state.status}",
                        code=f"run_{self.state.status}",
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CoordinatorError(
                        f"timed out {description}",
                        code="coordinator_timeout",
                    )
                self.state.condition.wait(min(1, remaining))

    def _log(self, message: str) -> None:
        if self._log_file is None:
            return
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with self._log_lock:
            self._log_file.write(f"{timestamp} {message}\n")


class CoordinatorClient:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        retry_delays: tuple[float, ...] | None = None,
        request_timeout: float = 2.0,
        unreachable_timeout: float = 5.0,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.base_url = f"http://{host}:{port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.retry_delays = (
            DEFAULT_RETRY_DELAYS if retry_delays is None else retry_delays
        )
        self.request_timeout = request_timeout
        self.unreachable_timeout = unreachable_timeout
        self._log = logger or (lambda _message: None)

    def mark_ready(self, node_id: str, timeout: int) -> None:
        self._post(f"/nodes/{node_id}/ready", {}, timeout)

    def mark_failed(self, node_id: str, message: str, timeout: int = 5) -> None:
        self._post(f"/nodes/{node_id}/failed", {"message": message}, timeout)

    def mark_cleaned(self, node_id: str, timeout: int = 5) -> None:
        self._post(f"/nodes/{node_id}/cleaned", {}, timeout)

    def wait_available(
        self,
        timeout: int,
        check_processes: Callable[[], None],
    ) -> None:
        deadline = time.monotonic() + timeout
        while True:
            check_processes()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CoordinatorError(
                    "timed out waiting for the coordinator",
                    code="coordinator_unreachable",
                )
            try:
                self._get_state(min(remaining, self.unreachable_timeout))
                return
            except CoordinatorError as error:
                if error.code != "coordinator_unreachable":
                    raise
            time.sleep(min(1, max(0, deadline - time.monotonic())))

    def wait_terminal(
        self,
        timeout: int,
        check_processes: Callable[[], None],
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while True:
            check_processes()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CoordinatorError(
                    "timed out waiting for the leader result",
                    code="coordinator_timeout",
                )
            state = self._get_state(min(remaining, self.unreachable_timeout))
            if state["status"] != RUNNING_STATUS:
                return state
            time.sleep(min(1, max(0, deadline - time.monotonic())))

    def _get_state(self, timeout: float) -> dict[str, object]:
        state = self._request("/state", None, timeout)
        status = state.get("status")
        if status != RUNNING_STATUS and status not in TERMINAL_STATUSES:
            raise CoordinatorError(
                "coordinator returned an invalid run status",
                code="protocol_error",
            )
        return state

    def _post(self, path: str, value: object, timeout: int) -> None:
        self._request(path, value, timeout)

    def _request(
        self,
        path: str,
        value: object | None,
        timeout: float,
    ) -> dict[str, object]:
        body = None if value is None else json.dumps(value).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers={"Content-Type": "application/json"} if body is not None else {},
            method="POST" if body is not None else "GET",
        )
        deadline = time.monotonic() + timeout
        retry_index = 0

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CoordinatorError(
                    f"cannot call coordinator endpoint {path}",
                    code="coordinator_unreachable",
                )
            try:
                with self.opener.open(
                    request,
                    timeout=min(self.request_timeout, remaining),
                ) as response:
                    return self._decode_response(response.read())
            except urllib.error.HTTPError as error:
                retryable = (
                    error.code in RETRYABLE_HTTP_STATUSES or error.code >= 500
                )
                if retryable and retry_index < len(self.retry_delays):
                    delay = self.retry_delays[retry_index]
                    retry_index += 1
                    error.close()
                    if not self._sleep_before_retry(path, delay, deadline, error.code):
                        raise CoordinatorError(
                            f"coordinator returned HTTP {error.code}",
                            code="coordinator_http_error",
                            http_status=error.code,
                        ) from error
                    continue
                raise self._coordinator_http_error(error) from error
            except (OSError, urllib.error.URLError) as error:
                if not self._is_retryable_connection_error(error):
                    raise CoordinatorError(
                        f"cannot call coordinator endpoint {path}: {error}",
                        code="request_failed",
                    ) from error
                if retry_index >= len(self.retry_delays):
                    raise CoordinatorError(
                        f"cannot call coordinator endpoint {path}",
                        code="coordinator_unreachable",
                    ) from error
                delay = self.retry_delays[retry_index]
                retry_index += 1
                if not self._sleep_before_retry(path, delay, deadline, None):
                    raise CoordinatorError(
                        f"cannot call coordinator endpoint {path}",
                        code="coordinator_unreachable",
                    ) from error

    def _sleep_before_retry(
        self,
        path: str,
        delay: float,
        deadline: float,
        http_status: int | None,
    ) -> bool:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or delay >= remaining:
            return False
        detail = (
            f"http_status={http_status}" if http_status is not None else "connection"
        )
        self._log(f"request={path} retry_in={delay} reason={detail}")
        time.sleep(delay)
        return True

    @staticmethod
    def _decode_response(body: bytes) -> dict[str, object]:
        try:
            value = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise CoordinatorError(
                "coordinator response is not valid JSON",
                code="protocol_error",
            ) from error
        if not isinstance(value, dict):
            raise CoordinatorError(
                "coordinator response JSON must be an object",
                code="protocol_error",
            )
        return value

    @staticmethod
    def _coordinator_http_error(error: urllib.error.HTTPError) -> CoordinatorError:
        status = error.code
        try:
            value = CoordinatorClient._decode_response(error.read())
        except CoordinatorError:
            value = {}
        finally:
            error.close()
        error_value = value.get("error")
        if isinstance(error_value, dict):
            code = error_value.get("code")
            message = error_value.get("message")
            if isinstance(code, str) and isinstance(message, str):
                return CoordinatorError(message, code=code, http_status=status)
        return CoordinatorError(
            f"coordinator returned HTTP {status}",
            code="coordinator_http_error",
            http_status=status,
        )

    @staticmethod
    def _is_retryable_connection_error(error: BaseException) -> bool:
        reason: BaseException | object = error
        if isinstance(error, urllib.error.URLError):
            reason = error.reason
        if isinstance(
            reason,
            (
                ConnectionRefusedError,
                ConnectionResetError,
                ConnectionAbortedError,
                TimeoutError,
                socket.timeout,
            ),
        ):
            return True
        return isinstance(reason, OSError) and reason.errno in {
            errno.ECONNREFUSED,
            errno.ECONNRESET,
            errno.ECONNABORTED,
            errno.ETIMEDOUT,
        }
