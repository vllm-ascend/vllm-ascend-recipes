from __future__ import annotations

import json
import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.recipe_ci.process import (  # noqa: E402
    process_group_exists,
    signal_cancellation_event,
    start_process,
    stop_processes,
    tail_log,
)
from scripts.recipe_ci.result import (  # noqa: E402
    RunFailure,
    build_final_result,
    utc_now,
    write_json_atomic,
)


class ProcessTests(unittest.TestCase):
    def test_sigterm_handler_only_sets_the_cancellation_event(self) -> None:
        previous = signal.getsignal(signal.SIGTERM)
        with signal_cancellation_event() as cancellation:
            os.kill(os.getpid(), signal.SIGTERM)
            self.assertTrue(cancellation.wait(1))
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_log_tail_replaces_invalid_utf8_and_cleanup_kills_the_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_path = root / "child.log"
            child = start_process(
                "stubborn child",
                ["bash", "-c", "printf '\\377tail\\n'; trap '' TERM; sleep 30"],
                cwd=root,
                environment=os.environ,
                log_path=log_path,
            )
            deadline = time.monotonic() + 2
            while log_path.stat().st_size == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            errors = stop_processes(
                [child], grace_period_seconds=0.1, kill_timeout_seconds=1
            )

            self.assertEqual(errors, [])
            self.assertFalse(process_group_exists(child.process_group))
            self.assertIn("tail", tail_log(log_path))
            self.assertIn("�", tail_log(log_path))


class ResultTests(unittest.TestCase):
    def test_primary_failure_is_not_replaced_by_cleanup_failure(self) -> None:
        primary = RunFailure(category="check_failed", message="request failed")
        cleanup = RunFailure(category="cleanup_failed", message="group survived")

        result = build_final_result(
            plan="fixture",
            status="failed",
            started_at=utc_now(),
            primary_failure=primary,
            cleanup_errors=[cleanup],
        )

        self.assertEqual(result["failure"]["category"], "check_failed")
        self.assertEqual(result["cleanup_errors"][0]["category"], "cleanup_failed")

    def test_atomic_json_replaces_an_existing_complete_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            write_json_atomic(path, {"status": "running"})
            write_json_atomic(path, {"status": "passed"})

            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")), {"status": "passed"}
            )
            self.assertEqual(list(path.parent.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
