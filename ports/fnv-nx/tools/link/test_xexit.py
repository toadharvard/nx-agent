#!/usr/bin/env python3
"""Native stand-ins of the exe's exit thunks (xexit.py; V2 X2, orchestrator: an atexit callback must not trap), on
synthetic x86 bytes and hand-written native objects (no decomp content), run under qemu-aarch64.

    python3 tools/link/test_xexit.py     # needs aarch64-linux-gnu-*, qemu-aarch64

* niptr: the thunk's bytes (a NiPointer static's release, as at 0x00fdd3b0) decode to (G, slot 1); the stub
  decrements the refcount at +4 and, at 0, calls native vtable slot 2 (DeleteThis after D1/D0); a null pointer is
  left alone;
* thiscall: `mov ecx, imm32; jmp T` decodes to (imm, T); the stub calls T's native definition with this = the static;
* anything else decodes to None (the reference keeps its trap).
"""
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_bridges  # noqa: E402
import xexit         # noqa: E402

G, IMM, T = 0x012024f8, 0x0120249c, 0x0044f4f0
NIPTR = (b'\xa1' + struct.pack('<I', G) + bytes.fromhex('85c0741c568bf083c00450ff15') + struct.pack('<I', 0xfdf1c4) +
         bytes.fromhex('85c0750a8b068b50048bce5effe25ec3cc'))
THISCALL = b'\xb9' + struct.pack('<I', IMM) + b'\xe9' + struct.pack('<i', T - (0xfdd3a0 + 10))
OTHER = bytes.fromhex('c3cccccc') + b'\0' * 44

TYPES = ''
DEFS = r'''
extern "C" int g_deleted; int g_deleted;
extern "C" void d1(void *) {} extern "C" void d0(void *) {}
extern "C" void delete_this(void *p) { g_deleted = (int)(unsigned long)p; }
extern "C" void *g_vt[3]; void *g_vt[3] = {(void *)d1, (void *)d0, (void *)delete_this};
struct Obj { void **vptr; int refs; };
Obj g_obj = {g_vt, 2};
extern "C" Obj *__fnv_012024f8; Obj *__fnv_012024f8 = &g_obj;
extern "C" int __fnv_0120249c[4];  int __fnv_0120249c[4];
extern "C" void *g_this; void *g_this;
extern "C" void __fnv_0044f4f0(void *self) { g_this = self; }
'''
CHECKS = r'''
extern "C" void __fnvnx_xexit_00fdd3b0(void);
extern "C" void __fnvnx_xexit_00fdd3a0(void);
static int t_niptr() {
  int bad = 0;
  __fnvnx_xexit_00fdd3b0();                       // 2 -> 1: nothing deleted
  bad += g_obj.refs != 1 || g_deleted != 0;
  __fnvnx_xexit_00fdd3b0();                       // 1 -> 0: DeleteThis(&g_obj)
  bad += g_obj.refs != 0 || g_deleted != (int)(unsigned long)&g_obj;
  __fnv_012024f8 = 0; g_deleted = 0;
  __fnvnx_xexit_00fdd3b0();                       // null: left alone
  bad += g_deleted != 0;
  return bad;
}
static int t_thiscall() { g_this = 0; __fnvnx_xexit_00fdd3a0(); return g_this == (void *)__fnv_0120249c ? 0 : 1; }
'''


def main():
    fails = 0
    blobs = {0xfdd3b0: NIPTR, 0xfdd3a0: THISCALL, 0xfdd3c0: OTHER}
    xexit._text = lambda exe, a, n: blobs.get(a, b'')[:n] or None
    got = {a: xexit.decode('exe', a) for a in blobs}
    want = {0xfdd3b0: ('niptr', G, 1), 0xfdd3a0: ('thiscall', IMM, T), 0xfdd3c0: None}
    for a in blobs:
        if got[a] != want[a]:
            print('FAIL decode %08x: %s (want %s)' % (a, got[a], want[a])); fails += 1
    if not fails:
        asm = ['    .text'] + [xexit.emit('__fnvnx_xexit_%08x' % a, got[a], lambda x: '__fnv_%08x' % x)
                               for a in (0xfdd3b0, 0xfdd3a0)]
        with tempfile.TemporaryDirectory() as t:
            fails += test_bridges.execute(t, TYPES, [DEFS], [CHECKS], ['t_niptr()', 't_thiscall()'], asm)
    print('ok' if not fails else 'FAILED')
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
