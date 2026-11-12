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


# FIXTURE ADDRESSES: a test fixture holds no address inside a guest image range (Legal: no public file pairs a guest VA with a decomp-derived
# name).  Every fixture address lies in [SYNTH_BASE, SYNTH_LIMIT), below the exe's ImageBase; the guest images are placed at or above the exe's
# ImageBase (contract/modules.py assign_bases: the exe, then every dll after the end of the exe image).  `image_ranges()` returns the real ranges
# when the runner supplies them (env FNVNX_IMAGE_RANGES = a file of `lo hi` hexadecimal lines, written by the gate from the PE headers), and
# `assert_outside` checks the synthetic window against them.
SYNTH_BASE = 0x00000000
SYNTH_LIMIT = 0x00400000      # exclusive: the lowest ImageBase of a guest image set of this port (the exe's)


def image_ranges():
    p = os.environ.get('FNVNX_IMAGE_RANGES')
    if not p:
        return []
    with open(p) as f:
        return [tuple(int(x, 16) for x in ln.split()) for ln in f if ln.strip() and not ln.startswith('#')]


def assert_outside(lo=SYNTH_BASE, hi=SYNTH_LIMIT):
    for a, b in image_ranges():
        assert hi <= a or b <= lo, 'synthetic window %#x..%#x overlaps image range %#x..%#x' % (lo, hi, a, b)


# Tests of rules that key on a fixed exe window (s2_bodies r_code_address_casts: code 0x00401000..0x00fdf000; s1_compile STATIC1: data
# 0x00fdf000..0x02100000): their fixture addresses must lie in the rule's own window, so they cannot move to the synthetic window until the
# rules read the window through an interface (reported to the L2 lead).  Their records hold synthetic names only.
RULE_WINDOW_FILES = ('test_addrfn1.py',)
