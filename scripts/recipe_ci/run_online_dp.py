#!/usr/bin/env python3
"""Run the upstream external-DP launcher and propagate worker failures."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print(f"usage: {Path(sys.argv[0]).name} LAUNCHER [ARG ...]", file=sys.stderr)
        return 2

    launcher = Path(sys.argv[1]).resolve()
    if not launcher.is_file():
        print(f"external-DP launcher not found: {launcher}", file=sys.stderr)
        return 2

    # launch_online_dp.py currently joins its multiprocessing workers without
    # checking their exit codes. Execute the upstream file unchanged, then use
    # the process objects it created to make a worker failure visible to CI.
    sys.argv = [str(launcher), *sys.argv[2:]]
    namespace = runpy.run_path(str(launcher), run_name="__main__")
    processes = namespace.get("processes", [])
    failures = [
        (index, process.exitcode)
        for index, process in enumerate(processes)
        if process.exitcode not in (None, 0)
    ]
    if not failures:
        return 0

    for index, exit_code in failures:
        print(
            f"external-DP worker {index} exited with {exit_code}",
            file=sys.stderr,
        )
    first_exit_code = failures[0][1]
    return first_exit_code if first_exit_code and 0 < first_exit_code < 256 else 1


if __name__ == "__main__":
    raise SystemExit(main())
