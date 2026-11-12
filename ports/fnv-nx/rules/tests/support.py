"""support.py - shared setup of the rules tests (run as `python3 -I tests/test_X.py` from the checkout root).

Puts rules/ on sys.path; `check` prints one line per case and collects failures; `legacy_units_lookup` lets a test point the
rules at a synthetic decomposition checkout in a temporary directory (the driver configures core with the real root)."""
import hashlib
import os
import sys

Ent01 = os.path.dirname(os.path.abspath(__file__))
RULES = os.path.normpath(os.path.join(Ent01, '..'))
if RULES not in sys.path:
    sys.path.insert(0, RULES)

import core  # noqa: E402

FAILS = []

# a synthetic Win32 shim header: generic Win32-looking prototypes the cases use (no text of any real header)
WIN32_HEADER = '''
typedef unsigned long DWORD;
typedef void* HANDLE;
typedef HANDLE HHOOK;
typedef long LONG;
DWORD GetLastError(void);
HANDLE CreateMutexA(void*, int, const char*);
int CloseHandle(HANDLE);
void Sleep(DWORD);
DWORD GetCurrentThreadId(void);
void EnterCriticalSection(void*);
long InterlockedIncrement(volatile long*);
HHOOK SetWindowsHookExA(int, void*, void*, DWORD);
long CallNextHookEx(HHOOK, int, unsigned, long);
HANDLE CreateThread(void*, unsigned long, void*, void*, unsigned long, unsigned long*);
void* HeapAlloc(void*, unsigned long, unsigned long);
int VirtualQuery(const void*, void*, unsigned long);
typedef DWORD (*LPTHREAD_START_ROUTINE)(void*);
'''


def check(name, got, want_in=(), want_not=()):
    ok = all(w in got for w in want_in) and not any(w in got for w in want_not)
    print('%-60s %s' % (name, 'ok' if ok else 'FAIL'))
    if not ok:
        FAILS.append(name)
        print('---- output\n' + got + '\n----')


def finish():
    print('PASS' if not FAILS else 'FAILED %d' % len(FAILS))
    sys.exit(1 if FAILS else 0)


def legacy_units_lookup():
    """core._units_json(path): the nearest build/units.json above path (tests only: the real lookup is the configured root)."""
    def look(path):
        d = os.path.dirname(os.path.abspath(path))
        while d != os.path.dirname(d):
            f = os.path.join(d, 'build', 'units.json')
            if os.path.exists(f):
                return d, f
            d = os.path.dirname(d)
        return None, None
    core._units_json = look


def tree_hash(root):
    """sha256 over (relative path, content) of every file below root, in sorted order."""
    h = hashlib.sha256()
    for dp, dn, fn in os.walk(root):
        dn.sort()
        for f in sorted(fn):
            p = os.path.join(dp, f)
            h.update(os.path.relpath(p, root).encode() + b'\0')
            with open(p, 'rb') as fh:
                h.update(fh.read())
            h.update(b'\0')
    return h.hexdigest()
