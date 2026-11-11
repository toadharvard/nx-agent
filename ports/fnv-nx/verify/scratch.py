"""scratch: the one way a test file cleans up its temporary directories (gate "selftests" runs each test with a private TMPDIR and
makes a test that leaves an entry in it uncertified).

    import scratch; scratch.install()      # before the first tempfile.mkdtemp / TemporaryDirectory / NamedTemporaryFile(dir=None)

install() wraps tempfile.mkdtemp (TemporaryDirectory and mkdtemp-based helpers call it) so that every directory it makes in this
process is removed, synchronously, when the interpreter exits: normal end, sys.exit, uncaught exception. A kill (timeout) is cleaned by
the gate, which owns the TMPDIR. Only the directories made through the wrapper are removed, never a whole TMPDIR: a test run by hand
with TMPDIR=/tmp touches nothing else. Idempotent. Children of the test (subprocesses) clean their own scratch."""
import atexit
import os
import shutil
import tempfile

_made = []
_installed = []


def _rm():
    for d in reversed(_made):
        if os.path.lexists(d):
            shutil.rmtree(d, ignore_errors=True)
    _made.clear()


def install():
    if _installed:
        return
    real = tempfile.mkdtemp

    def mkdtemp(*a, **k):
        d = real(*a, **k)
        _made.append(d)
        return d

    tempfile.mkdtemp = mkdtemp
    _installed.append(real)
    atexit.register(_rm)
