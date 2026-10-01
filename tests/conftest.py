"""Helpers shared by the test files."""
import contextlib
import subprocess
import sys

HOLD = """
import os, sys
os.environ["INKVAULT_HOME"] = sys.argv[1]
from inkvault import nightly
with nightly.lock():
    print("held", flush=True)
    sys.stdin.read()  # hold until the test closes our stdin
"""


@contextlib.contextmanager
def holder(home):
    """Another process holding the nightly lock. A separate process, because that's what the lock is for, and
    it behaves the same on Windows, macOS and Linux (flock and msvcrt locks can be ambiguous within one process)."""
    p = subprocess.Popen([sys.executable, "-c", HOLD, str(home)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "held", p.stderr.read()
        yield p
    finally:
        p.stdin.close()
        try:
            p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
        err = p.stderr.read()
        assert not err, f"lock holder crashed: {err}"
