"""The one shared test-case function (class 10, gate "oracle"): a test case of a script-style test file is a call
`case("literal name", ok)` of this module's `case`, found by the oracle gate from the file's syntax tree through its
import of this module (`from testcase import case` or `import testcase` + `testcase.case(...)`), never by a name.
    case(name, ok, detail="")            ok truthy -> PASS line, else FAIL line (detail shown on FAIL)
    case(name, got=G, want=W)            ok iff G == W; a FAIL line shows both
    case(name, ok, key=K)                one static case run once per K (a loop over a table): the case is `name`, its
                                         instances are named "name [K]"
A case name is a string constant, unique in its file (a case in a loop takes `key=`); a repeated (name, key) at run
time is itself a FAIL.
    finish()                             prints the summary, returns the exit status (0 iff no case failed); callers
                                         end with `sys.exit(testcase.finish())`
    FAILED                               the list of failed case names"""
import sys

_NO = object()
FAILED = []
_SEEN = set()


def case(name, ok=_NO, detail="", *, got=_NO, want=_NO, key=_NO):
    if ok is _NO:
        if got is _NO or want is _NO:
            raise TypeError("case(%r): give ok, or got and want" % (name,))
        ok = got == want
        if not ok and not detail:
            detail = "got %r, want %r" % (got, want)
    elif got is not _NO or want is not _NO:
        raise TypeError("case(%r): ok and got/want are exclusive" % (name,))
    if key is not _NO:
        name = "%s [%s]" % (name, key)
    if name in _SEEN:
        print("FAIL %s: case name repeated" % name, flush=True)
        FAILED.append(name + " (repeated)")
        return False
    _SEEN.add(name)
    ok = bool(ok)
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, (": " + str(detail)[:600]) if detail and not ok else ""), flush=True)
    if not ok:
        FAILED.append(name)
    return ok


def finish():
    print("FAILED: %s" % FAILED if FAILED else "all passed", flush=True)
    return 1 if FAILED else 0
