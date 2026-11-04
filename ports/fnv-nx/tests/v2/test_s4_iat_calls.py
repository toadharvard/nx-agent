#!/usr/bin/env python3
"""V2 adversarial case for S4 81775c9 (platform/win32/iat_calls.cpp): seven decomp names of `call [IAT slot]` sites and
import thunks become AArch64 thunks that load the slot and branch. That is right for the FUN_ names, which the units
declare as functions. The DAT_ names are declared as pointer VARIABLES and called through:
    extern void(__cdecl* DAT_fdf3f0)();   extern "C" void (*DAT_00fdf398)();   extern "C" void(__cdecl* DAT_00fdf37c)();
so the unit loads the pointer stored at the symbol, i.e. the thunk's first instruction word, and calls that.

    python3 tests/v2/test_s4_iat_calls.py   # aarch64-linux-gnu-g++ (static), qemu-aarch64; our own synthetic units

The test links iat_calls.cpp with a fake import table at 0x00fdf000 (the slots hold a native function) and runs:
  P1  a unit declaring `extern "C" void (*DAT_00fdf398)();` and calling it reaches the import (fails: it jumps to the
      thunk's instruction bits)
  F1  control: `extern "C" int FUN_00ee6d6e(const char*)` called as a function reaches the import
"""
import os
import shutil
import subprocess
import sys
import tempfile

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
UNIT = r'''
extern "C" {
unsigned int iat[0x400] __attribute__((section(".fakeiat"), used, aligned(4096)));
static int hits;
static int imp_any(...) { return ++hits; }
}
extern "C" int FUN_00ee6d6e(const char *);
extern "C" void (*DAT_00fdf398)();
#include <signal.h>
#include <unistd.h>
static void bad(int) { _exit(2); }
int main(int argc, char **) {
    for (int i = 0; i < 0x400; i++) iat[i] = (unsigned)(unsigned long)&imp_any;
    signal(SIGSEGV, bad); signal(SIGILL, bad); signal(SIGBUS, bad);
    if (argc > 1) { int h = hits; FUN_00ee6d6e("x"); return hits == h + 1 ? 0 : 1; }
    int h = hits; DAT_00fdf398(); return hits == h + 1 ? 0 : 1;
}
'''


def main():
    work = tempfile.mkdtemp(prefix='v2-iatc-')   # raises when it cannot create the directory
    try:
        u, exe = os.path.join(work, 'u.cpp'), os.path.join(work, 't')
        open(u, 'w').write(UNIT)
        inc = ['-I' + os.path.join(PORT, 'platform', d) for d in ('include', 'win32', 'crt', '')] + \
              ['-iquote', os.path.join(PORT, 'shim', 'include')]
        r = subprocess.run(['aarch64-linux-gnu-g++', '-static', '-O1', '-no-pie', '-std=gnu++17', *inc, u,
                            os.path.join(PORT, 'platform', 'win32', 'iat_calls.cpp'), '-o', exe,
                            '-Wl,--section-start=.fakeiat=0x00fdf000'], capture_output=True, text=True)
        if r.returncode:
            print('skip: build failed (%s)' % (r.stderr.strip().splitlines() or ['?'])[-1][:200])
            return 0
        fails = 0
        for case, args, what in (('P1', [], 'DAT_00fdf398 (a pointer variable) called through'),
                                 ('F1', ['f'], 'FUN_00ee6d6e (a function) called')):
            rc = subprocess.run(['qemu-aarch64', exe] + args).returncode
            print('%s %s %s: %s' % ('ok' if rc == 0 else 'FAIL', case, what,
                                    'reaches the import' if rc == 0 else 'crash' if rc == 2 else 'exit %d' % rc))
            fails += rc != 0
        print('ok' if not fails else 'FAILED')
        return 1 if fails else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
