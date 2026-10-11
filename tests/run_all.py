"""Every test file of the engine, each in a process of its own, and the sum of them.

    runtime\\python.exe tests\\run_all.py

A file is started the way a person starts it - `runtime\\python.exe tests\\<file>.py` - so one
that breaks does not take the others with it, and what one leaves in a module does not reach the
next. The exit code is 0 when every file passed, 1 otherwise.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

# file name - why it is not run here
SKIPPED: dict[str, str] = {}


def run(file: Path) -> tuple[int, bool, str]:
    """One file: how many tests it ran, whether all passed, and the names of what did not."""
    done = subprocess.run([sys.executable, "-X", "utf8", str(file)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    said = done.stdout + done.stderr
    ran = re.search(r"^Ran (\d+) tests?", said, re.MULTILINE)
    count = int(ran.group(1)) if ran else 0
    good = done.returncode == 0 and count > 0 and re.search(r"^OK\b", said, re.MULTILINE) is not None
    if good:
        return count, True, ""
    broken = [line for line in said.splitlines() if line.startswith(("FAIL:", "ERROR:"))]
    # A file that never got to its tests says why in its last line.
    return count, False, " | ".join(broken) if broken else (said.strip().splitlines() or ["no output"])[-1]


def main() -> int:
    started = time.monotonic()
    total = failed = 0
    files = sorted(TESTS.glob("test_*.py"))
    for file in files:
        if file.name in SKIPPED:
            print(f"skip {file.name:34}      {SKIPPED[file.name]}")
            continue
        count, good, trouble = run(file)
        total += count
        failed += 0 if good else 1
        print(f"{'ok  ' if good else 'FAIL'} {file.name:34} {count:4}" + ("" if good else "   " + trouble[:400]))
    ran = len(files) - sum(1 for file in files if file.name in SKIPPED)
    print(f"{total} tests in {ran} files, files failed: {failed}, {time.monotonic() - started:.0f} s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
