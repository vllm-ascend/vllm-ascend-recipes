from __future__ import annotations

import errno
import io
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.recipe_ci.coordinator import (  # noqa: E402
    NODE_STATUSES,
    TERMINAL_STATUSES,
    CoordinatorClient,
    CoordinatorError,
    LeaderCoordinator,
    RunState,
)


class FakeResponse:
    def __init__(self, value: object = None, *, raw: bytes | None = None) -> None:
        self.body = raw if raw is not None else json.dumps(value or {}).encode("utf-8")

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class SequenceOpener:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def open(self, request: object, timeout: float) -> FakeResponse:
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, FakeResponse)
        return outcome


def http_error(status: int, code: str = "temporary") -> urllib.error.HTTPError:
    body = json.dumps(
        {"error": {"code": code, "message": f"HTTP {status}"}}
    ).encode("utf-8")
    return urllib.error.HTTPError(
        "http://coordinator.invalid/state",
        status,
        f"HTTP {status}",
        None,
        io.BytesIO(body),
    )


def refused() -> urllib.error.URLError:
    return urllib.error.URLError(
        ConnectionRefusedError(errno.ECONNREFUSED, "connection refused")
    )


class RunStateTests(unittest.TestCase):
    def test_node_states_are_idempotent_and_failure_survives_cleanup(self) -> None:
        state = RunState(["node0", "node1"])

        self.assertEqual(
            state.snapshot()["nodes"],
            {"node0": "pending", "node1": "pending"},
        )
        self.assertEqual(
            set(state.snapshot()["nodes"].values()),
            {"pending"},
        )
        self.assertTrue(set(state.snapshot()["nodes"].values()) <= NODE_STATUSES)

        state.mark_ready("node0")
        state.mark_ready("node0")
        self.assertEqual(state.snapshot()["nodes"]["node0"], "ready")

        state.mark_failed("node0", "service stopped")
        state.mark_failed("node0", "service stopped")
        self.assertEqual(state.snapshot()["nodes"]["node0"], "failed")

        state.mark_cleaned("node0")
        state.mark_cleaned("node0")
        snapshot = state.snapshot()
        self.assertEqual(snapshot["nodes"]["node0"], "cleaned")
        self.assertEqual(snapshot["failures"], {"node0": "service stopped"})
        self.assertEqual(snapshot["cleaned"], ["node0"])

    def test_each_terminal_state_is_immutable_and_idempotent(self) -> None:
        for terminal in sorted(TERMINAL_STATUSES):
            with self.subTest(terminal=terminal):
                state = RunState(["node0", "node1"])
                state.finish(terminal, "first message")
                state.finish(terminal, "later message")

                snapshot = state.snapshot()
                self.assertEqual(snapshot["status"], terminal)
                self.assertEqual(snapshot["message"], "first message")
                for other in TERMINAL_STATUSES - {terminal}:
                    with self.assertRaises(CoordinatorError) as raised:
                        state.finish(other)
                    self.assertEqual(raised.exception.code, "invalid_transition")
                    self.assertEqual(raised.exception.http_status, 409)
                self.assertEqual(state.snapshot()["status"], terminal)

    def test_invalid_terminal_name_is_rejected(self) -> None:
        state = RunState(["node0", "node1"])

        with self.assertRaises(CoordinatorError) as raised:
            state.finish("done")

        self.assertEqual(raised.exception.code, "invalid_status")
        self.assertEqual(raised.exception.http_status, 400)
        self.assertEqual(state.snapshot()["status"], "running")

    def test_first_failure_is_preserved(self) -> None:
        state = RunState(["node0", "node1"])

        state.mark_failed("node0", "first failure")
        state.mark_failed("node1", "cleanup also failed")
        state.finish("failed", "must not replace primary failure")

        snapshot = state.snapshot()
        self.assertEqual(snapshot["message"], "node0: first failure")
        self.assertEqual(
            snapshot["primary_failure"],
            {"node_id": "node0", "message": "first failure"},
        )
        self.assertEqual(
            snapshot["failures"],
            {"node0": "first failure", "node1": "cleanup also failed"},
        )

    def test_conflicting_duplicate_failure_does_not_replace_first_report(self) -> None:
        state = RunState(["node0", "node1"])
        state.mark_failed("node0", "first failure")

        with self.assertRaises(CoordinatorError) as raised:
            state.mark_failed("node0", "different failure")

        self.assertEqual(raised.exception.code, "failure_conflict")
        self.assertEqual(raised.exception.http_status, 409)
        self.assertEqual(state.snapshot()["failures"]["node0"], "first failure")

    def test_unknown_node_has_machine_readable_error_fields(self) -> None:
        state = RunState(["node0", "node1"])

        with self.assertRaises(CoordinatorError) as raised:
            state.mark_cleaned("node9")

        self.assertEqual(raised.exception.code, "unknown_node")
        self.assertEqual(raised.exception.http_status, 400)


class CoordinatorHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.coordinator = LeaderCoordinator(
            ["node0", "node1"],
            0,
            host="127.0.0.1",
        )
        self.coordinator.start()
        self.addCleanup(self.coordinator.close)
        self.client = CoordinatorClient(
            "127.0.0.1",
            self.coordinator.port,
            retry_delays=(),
        )

    def test_http_ready_and_cleaned_requests_are_idempotent(self) -> None:
        self.client.mark_ready("node0", 1)
        self.client.mark_ready("node0", 1)
        self.client.mark_ready("node1", 1)
        self.coordinator.wait_ready(1, lambda: None)
        self.coordinator.state.finish("passed")

        self.client.mark_cleaned("node0")
        self.client.mark_cleaned("node0")
        self.client.mark_cleaned("node1")
        self.coordinator.wait_cleaned(1)

        snapshot = self.client.wait_terminal(1, lambda: None)
        self.assertEqual(snapshot["status"], "passed")
        self.assertEqual(snapshot["cleaned"], ["node0", "node1"])

    def test_http_errors_have_structured_code_message_and_status(self) -> None:
        with self.assertRaises(CoordinatorError) as unknown:
            self.client.mark_cleaned("node9")
        self.assertEqual(unknown.exception.code, "unknown_node")
        self.assertEqual(unknown.exception.http_status, 400)
        self.assertIn("unknown node", str(unknown.exception))

        self.coordinator.state.finish("passed")
        with self.assertRaises(CoordinatorError) as transition:
            self.client.mark_failed("node0", "late failure")
        self.assertEqual(transition.exception.code, "invalid_transition")
        self.assertEqual(transition.exception.http_status, 409)
        self.assertEqual(self.coordinator.state.snapshot()["status"], "passed")

        with self.assertRaises(CoordinatorError) as missing:
            self.client._request("/missing", None, 1)
        self.assertEqual(missing.exception.code, "not_found")
        self.assertEqual(missing.exception.http_status, 404)

    def test_optional_coordinator_log_records_lifecycle_and_progress(self) -> None:
        self.coordinator.close()
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "node0/coordinator.log"
            coordinator = LeaderCoordinator(
                ["node0", "node1"],
                0,
                log_path,
                host="127.0.0.1",
            )
            coordinator.start()
            client = CoordinatorClient(
                "127.0.0.1",
                coordinator.port,
                retry_delays=(),
            )
            client.mark_ready("node0", 1)
            coordinator.state.finish("cancelled", "requested cancellation")
            client.mark_cleaned("node0")
            coordinator.close()

            contents = log_path.read_text(encoding="utf-8")

        self.assertIn("server=start", contents)
        self.assertIn("node=node0 action=ready", contents)
        self.assertIn("action=finish status=cancelled", contents)
        self.assertIn("node=node0 action=cleaned progress=1/2", contents)
        self.assertIn("server=stop", contents)


class CoordinatorClientRetryTests(unittest.TestCase):
    def make_client(
        self,
        outcomes: list[object],
    ) -> tuple[CoordinatorClient, SequenceOpener]:
        client = CoordinatorClient(
            "coordinator.invalid",
            1,
            retry_delays=(0, 0),
            request_timeout=0.1,
            unreachable_timeout=1,
        )
        opener = SequenceOpener(outcomes)
        client.opener = opener  # type: ignore[assignment]
        return client, opener

    def test_ordinary_4xx_is_not_retried(self) -> None:
        client, opener = self.make_client(
            [http_error(409, "invalid_transition"), FakeResponse()]
        )

        with self.assertRaises(CoordinatorError) as raised:
            client.mark_ready("node0", 1)

        self.assertEqual(opener.calls, 1)
        self.assertEqual(raised.exception.code, "invalid_transition")
        self.assertEqual(raised.exception.http_status, 409)

    def test_408_429_and_5xx_are_retried(self) -> None:
        for status in (408, 429, 500, 503):
            with self.subTest(status=status):
                client, opener = self.make_client(
                    [http_error(status), http_error(status), FakeResponse()]
                )

                client.mark_ready("node0", 1)

                self.assertEqual(opener.calls, 3)

    def test_retryable_http_errors_have_a_finite_attempt_limit(self) -> None:
        client, opener = self.make_client(
            [http_error(503), http_error(503), http_error(503)]
        )

        with self.assertRaises(CoordinatorError) as raised:
            client.mark_ready("node0", 1)

        self.assertEqual(opener.calls, 3)
        self.assertEqual(raised.exception.http_status, 503)

    def test_connection_failures_report_coordinator_unreachable(self) -> None:
        client, opener = self.make_client([refused(), refused(), refused()])

        with self.assertRaises(CoordinatorError) as raised:
            client.wait_terminal(1, lambda: None)

        self.assertEqual(opener.calls, 3)
        self.assertEqual(raised.exception.code, "coordinator_unreachable")

    def test_connection_failure_can_recover_within_retry_limit(self) -> None:
        client, opener = self.make_client(
            [
                refused(),
                FakeResponse(
                    {
                        "status": "failed",
                        "message": "node0: service stopped",
                    }
                ),
            ]
        )

        state = client.wait_terminal(1, lambda: None)

        self.assertEqual(opener.calls, 2)
        self.assertEqual(state["status"], "failed")

    def test_wait_available_recovers_after_a_full_retry_window(self) -> None:
        client, opener = self.make_client(
            [refused(), refused(), refused(), FakeResponse({"status": "running"})]
        )

        client.wait_available(3, lambda: None)

        self.assertEqual(opener.calls, 4)

    def test_invalid_json_response_is_not_retried(self) -> None:
        client, opener = self.make_client(
            [FakeResponse(raw=b"not-json"), FakeResponse({"status": "passed"})]
        )

        with self.assertRaises(CoordinatorError) as raised:
            client.wait_terminal(1, lambda: None)

        self.assertEqual(opener.calls, 1)
        self.assertEqual(raised.exception.code, "protocol_error")


if __name__ == "__main__":
    unittest.main()
