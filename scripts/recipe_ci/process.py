#!/usr/bin/env python3
"""Small process-lifecycle helpers for the Recipe CI runner."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import FrameType
from typing import BinaryIO, Callable, Iterator, Mapping, Sequence


DEFAULT_LOG_TAIL_LINES = 50
DEFAULT_LOG_TAIL_BYTES = 16 * 1024


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


@dataclass
class ManagedProcess:
    """A child started in its own session and process group."""

    name: str
    process: subprocess.Popen[bytes]
    log_path: Path
    log_file: BinaryIO
    stage: str | None = None
    node_id: str | None = None
    started_at: str = field(default_factory=_utc_now)
    process_group: int = field(init=False)

    def __post_init__(self) -> None:
        # start_new_session=True makes the child PID the stable process-group ID.
        # Store it now because getpgid() stops working after the leader exits.
        self.process_group = self.process.pid

    @property
    def pid(self) -> int:
        return self.process.pid


class ManagedProcessExited(RuntimeError):
    """A supervised process exited before its owner expected it to."""

    def __init__(self, item: ManagedProcess, return_code: int) -> None:
        self.item = item
        self.return_code = return_code
        log_tail = tail_log(item.log_path)
        message = (
            f"{item.name} exited with {return_code}; see {item.log_path}"
        )
        if log_tail:
            message += f"\nlast log lines:\n{log_tail}"
        super().__init__(message)


class CancellationRequested(RuntimeError):
    """SIGINT or SIGTERM requested an orderly runner shutdown."""


def start_process(
    name: str,
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    log_path: Path,
    stage: str | None = None,
    node_id: str | None = None,
) -> ManagedProcess:
    """Start a logged command in a new session and process group."""
    if not command:
        raise ValueError("command must not be empty")

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("wb")
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=dict(environment),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except BaseException:
        log_file.close()
        raise

    return ManagedProcess(
        name=name,
        process=process,
        log_path=log_path,
        log_file=log_file,
        stage=stage,
        node_id=node_id,
    )


def tail_log(
    path: Path,
    *,
    max_lines: int = DEFAULT_LOG_TAIL_LINES,
    max_bytes: int = DEFAULT_LOG_TAIL_BYTES,
) -> str:
    """Return a bounded, replacement-decoded tail while preserving the full log."""
    if max_lines < 1:
        raise ValueError("max_lines must be positive")
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")

    try:
        with path.open("rb") as log_file:
            log_file.seek(0, os.SEEK_END)
            size = log_file.tell()
            log_file.seek(max(0, size - max_bytes))
            data = log_file.read(max_bytes)
    except FileNotFoundError:
        return ""
    except OSError as error:
        return f"<unable to read log: {error}>"

    lines = data.decode("utf-8", errors="replace").splitlines()
    return "\n".join(lines[-max_lines:])


def poll_processes(
    processes: Sequence[ManagedProcess],
) -> tuple[ManagedProcess, int] | None:
    """Return the first exited managed process, if any."""
    for item in processes:
        return_code = item.process.poll()
        if return_code is not None:
            return item, return_code
    return None


def check_processes(processes: Sequence[ManagedProcess]) -> None:
    """Raise with a bounded log tail when a supervised process has exited."""
    exited = poll_processes(processes)
    if exited is not None:
        raise ManagedProcessExited(*exited)


def wait_for_process(
    item: ManagedProcess,
    timeout_seconds: float,
    *,
    check_runtime: Callable[[], None] | None = None,
    cancellation: threading.Event | None = None,
    poll_interval_seconds: float = 0.5,
) -> int:
    """Wait for one step while its caller supervises the rest of the run.

    ``check_runtime`` is intentionally caller-owned: the runner can keep its
    service/gateway checks and coordinator failure classification in the main
    lifecycle instead of hiding those policies in this module.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive")

    deadline = time.monotonic() + timeout_seconds
    while True:
        if cancellation is not None and cancellation.is_set():
            raise CancellationRequested("cancellation requested")

        return_code = item.process.poll()
        if return_code is not None:
            return return_code

        if check_runtime is not None:
            check_runtime()

        # Avoid sleeping if the step finished during a runtime/coordinator check.
        return_code = item.process.poll()
        if return_code is not None:
            return return_code

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(item.name, timeout_seconds)

        delay = min(poll_interval_seconds, remaining)
        if cancellation is None:
            time.sleep(delay)
        else:
            cancellation.wait(delay)


@contextmanager
def signal_cancellation_event(
    handled_signals: Sequence[int] = (signal.SIGINT, signal.SIGTERM),
) -> Iterator[threading.Event]:
    """Set an event from minimal signal handlers and restore old handlers."""
    cancellation = threading.Event()
    previous_handlers: dict[int, signal.Handlers] = {}

    def request_cancellation(_signum: int, _frame: FrameType | None) -> None:
        cancellation.set()

    try:
        for signum in handled_signals:
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, request_cancellation)
    except BaseException:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)
        raise

    try:
        yield cancellation
    finally:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)


def process_group_exists(process_group: int) -> bool:
    """Return whether a process group still has at least one member."""
    if process_group <= 0:
        raise ValueError("process_group must be positive")
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_process_groups(
    processes: Sequence[ManagedProcess], deadline: float
) -> list[ManagedProcess]:
    while True:
        alive: list[ManagedProcess] = []
        for item in processes:
            # poll() also reaps a direct child that has already exited.
            item.process.poll()
            if process_group_exists(item.process_group):
                alive.append(item)
        if not alive or time.monotonic() >= deadline:
            return alive
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))


def stop_processes(
    processes: Sequence[ManagedProcess],
    *,
    grace_period_seconds: float = 10,
    kill_timeout_seconds: float = 5,
) -> list[str]:
    """TERM/KILL process groups in reverse order, close logs, and verify cleanup.

    Cleanup diagnostics are returned instead of raised so a runner can retain its
    original execution failure as ``primary_failure``.
    """
    if grace_period_seconds < 0:
        raise ValueError("grace_period_seconds must not be negative")
    if kill_timeout_seconds < 0:
        raise ValueError("kill_timeout_seconds must not be negative")

    cleanup_errors: list[str] = []
    reversed_processes = list(reversed(processes))
    signal_targets: list[ManagedProcess] = []
    seen_groups: set[int] = set()
    current_group = os.getpgrp()

    for item in reversed_processes:
        if item.process_group in seen_groups:
            continue
        seen_groups.add(item.process_group)
        if item.process_group == current_group:
            cleanup_errors.append(
                f"refusing to signal runner process group {current_group} "
                f"for {item.name}"
            )
            continue
        signal_targets.append(item)

    try:
        for item in signal_targets:
            item.process.poll()
            if not process_group_exists(item.process_group):
                continue
            try:
                os.killpg(item.process_group, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except OSError as error:
                cleanup_errors.append(
                    f"could not SIGTERM {item.name} process group "
                    f"{item.process_group}: {error}"
                )

        alive = _wait_for_process_groups(
            signal_targets, time.monotonic() + grace_period_seconds
        )
        for item in alive:
            try:
                os.killpg(item.process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as error:
                cleanup_errors.append(
                    f"could not SIGKILL {item.name} process group "
                    f"{item.process_group}: {error}"
                )

        _wait_for_process_groups(
            alive, time.monotonic() + kill_timeout_seconds
        )
    finally:
        for item in reversed_processes:
            try:
                item.log_file.close()
            except Exception as error:
                cleanup_errors.append(f"could not close {item.log_path}: {error}")

    for item in signal_targets:
        item.process.poll()
        if process_group_exists(item.process_group):
            cleanup_errors.append(
                f"{item.name} process group {item.process_group} still exists "
                "after SIGKILL"
            )

    return cleanup_errors
