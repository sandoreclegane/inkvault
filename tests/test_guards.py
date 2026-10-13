"""Tests can start this Python and nothing else, through run *or* Popen [P12]."""
import subprocess
import sys

import pytest


def test_popen_of_another_program_fails_loudly():
    # a program that doesn't exist: before the guard this fails with FileNotFoundError, so the red phase never
    # launches anything real (R8)
    with pytest.raises(AssertionError, match="real program"):
        subprocess.Popen(["inkvault-no-such-program-xyz", "/Query"])


def test_popen_of_this_python_works():
    p = subprocess.Popen([sys.executable, "-c", "print('ok')"], stdout=subprocess.PIPE, text=True)
    assert p.communicate(timeout=30)[0].strip() == "ok"
