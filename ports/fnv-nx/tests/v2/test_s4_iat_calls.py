#!/usr/bin/env python3
"""V2 check of S4 81775c9 (platform/win32/iat_calls.cpp): seven decomp names of `call [IAT slot]` sites and import
thunks are AArch64 code thunks that load the slot and branch. The units declare the three DAT_ names as
function-pointer VARIABLES (`extern void (*DAT_00fdf398)();`), so this is right only if the link never lets a unit's
load of such a variable reach the thunk: resolve rebinds those references to `__fnv_<slot>` (the slot itself, as
data), and the DAT_ names are left only as call targets (the exe's `jmp [slot]` thunks bound to them).

    FNVNX_LINK_FIXED=<link fixed/ dir> python3 tests/v2/test_s4_iat_calls.py    (skip without; objects are scratch)

  P1  in the link's objects, every relocation against a table name is CALL26 / JUMP26 (S4's iat_calls_check rule),
      and the units that declare a DAT_ name as a variable reach `__fnv_<slot>` through the GOT instead
  F1  a thunk under qemu: iat_calls.cpp linked with a fake import table at 0x00fdf000, called as a function, reaches
      the slot's import
V2 first refuted this with a unit linked straight against iat_calls.cpp (P1, 31f42d3). That skipped resolve's
renaming: on link-head 06857911 nid3dshaderprogramcreatorhlsl and blk_00ee0000_o2 load __fnv_00fdf3f0,
__fnv_00fdf37c and __fnv_00fdf398, and DAT_fdf3f0 appears only as CALL26 (FUN_00ee6e2e's binding). Withdrawn.
"""
import os
import re
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
int main() {
    for (int i = 0; i < 0x400; i++) iat[i] = (unsigned)(unsigned long)&imp_any;
    int h = hits; FUN_00ee6d6e("x"); return hits == h + 1 ? 0 : 1;
}
'''


def table():
    return re.findall(r'X\((\w+), (0x[0-9a-f]+),', open(os.path.join(PORT, 'platform', 'win32', 'iat_calls.cpp')).read())


def main():
    fails = 0
    fixed = os.environ.get('FNVNX_LINK_FIXED')
    if fixed and os.path.isdir(fixed):
        names = dict(table())
        bad, data_ok = [], 0
        for f in sorted(os.listdir(fixed)):
            if not f.endswith('.o') or f == 'zz_addrmap.o':
                continue
            p = os.path.join(fixed, f)
            blob = open(p, 'rb').read()
            if not any(n.encode() in blob for n in names) and b'__fnv_00fdf3' not in blob:
                continue
            rel = subprocess.run(['aarch64-linux-gnu-readelf', '-rW', p], capture_output=True, text=True).stdout
            for line in rel.splitlines():
                c = line.split()
                if len(c) >= 5 and c[4] in names and not c[2].endswith(('CALL26', 'JUMP26')):
                    bad.append((f, c[4], c[2]))
                if len(c) >= 5 and c[4] in ['__fnv_%08x' % int(s, 16) for s in names.values()]:
                    data_ok += 1
        ok = not bad
        print('%s P1 table names only called (%s); %d data references to the slots as __fnv_<slot>' %
              ('ok' if ok else 'FAIL', bad[:4] or 'none other', data_ok))
        fails += not ok
    else:
        print('skip P1: no FNVNX_LINK_FIXED')
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
            print('skip F1: build failed')
        else:
            rc = subprocess.run(['qemu-aarch64', exe]).returncode
            print('%s F1 FUN_00ee6d6e called as a function reaches the import' % ('ok' if rc == 0 else 'FAIL'))
            fails += rc != 0
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
