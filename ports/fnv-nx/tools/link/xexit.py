"""xexit.py - native stand-ins for the exe's exit thunks that a unit hands to atexit by address (stream S2c-abi).

A decomp unit can register an exit callback that exists only in the exe (no unit defines it): 0x00c1d8b0 calls
`_atexit(&UNK_00fdd3b0)`, the static's MSVC exit thunk at 0x00fdd3b0. Bound as an absolute value the native atexit
table would jump into x86 code; bound to nothing it traps on every orderly exit (V2 X2, NX1 check 7). The thunks
MSVC emits have a few fixed shapes; the two the decomp needs are decoded from the player's exe and emitted as
AArch64 stubs that do the same with native objects:

  niptr     `mov eax, [G]; test eax, eax; je ret; push esi; mov esi, eax; add eax, 4; push eax;
             call [InterlockedDecrement]; test eax, eax; jne pop; mov eax, [esi]; mov edx, [eax + 4k]; mov ecx, esi;
             pop esi; jmp edx` - a NiPointer static's release: decrement the refcount at +4 and, at 0, call the
             object's virtual slot k (DeleteThis). MSVC's first slot is the one deleting destructor, Itanium has two
             (D1, D0), so MSVC slot k >= 1 is native slot k + 1 (ILP32: 4 bytes a slot);
  thiscall  `mov ecx, imm32; jmp T` - a member call on a static at imm32 (its destructor): x0 = the static's address
             symbol, tail call of T's native definition.

    decode(exe, address) -> ('niptr', G, k) | ('thiscall', imm, T) | None
    emit(name, shape, addr_sym) -> AArch64 assembly (bridges.S); addr_sym(a) names image address a
"""
import struct


def _text(exe, a, n):
    import fnvnx_pe   # loader/tools (thisadj puts it on sys.path)
    if not hasattr(_text, 'pe') or _text.path != exe:
        _text.pe, _text.path = fnvnx_pe.PE(open(exe, 'rb').read()), exe
    try:
        b = _text.pe.read_rva(a - 0x400000, n)
    except Exception:  # noqa: BLE001
        return None
    return bytes(b) if b is not None else None


def decode(exe, a):
    b = _text(exe, a, 48)
    if not b:
        return None
    if b[0] == 0xb9 and b[5] == 0xe9:                       # mov ecx, imm32; jmp rel32
        imm, rel = struct.unpack_from('<Ii', b, 1)[0], struct.unpack_from('<i', b, 6)[0]
        return ('thiscall', imm, (a + 10 + rel) & 0xffffffff)
    head = bytes.fromhex('85c074') + b'?' + bytes.fromhex('568bf083c00450ff15')
    if b[0] == 0xa1 and len(b) >= 34 and b[5:8] == head[:3] and b[9:18] == head[4:]:
        g = struct.unpack_from('<I', b, 1)[0]
        tail = b[22:]
        # test eax, eax; jne +x; mov eax, [esi]; mov edx, [eax + disp8]; mov ecx, esi; pop esi; jmp edx
        if tail[0:3] == bytes.fromhex('85c075') and tail[4:7] == bytes.fromhex('8b068b') and tail[7] == 0x50 and \
                tail[9:14] == bytes.fromhex('8bce5effe2'):
            disp = tail[8]
            if disp % 4 == 0 and disp >= 4:
                return ('niptr', g, disp // 4)
    return None


def emit(name, shape, addr_sym):
    head = ['', '    // native stand-in for the exe exit thunk %s (%s)' % (name, shape[0]), '    .p2align 2',
            '    .globl %s' % name, '    .type %s, %%function' % name, '%s:' % name]
    if shape[0] == 'thiscall':
        _, imm, t = shape
        body = ['    ldr w0, 1f', '    b %s' % addr_sym(t), '    .p2align 2', '1:  .word %s' % addr_sym(imm)]
    else:
        _, g, k = shape
        body = ['    ldr w9, 1f', '    ldr w0, [x9]', '    cbz w0, 2f', '    add x10, x0, #4',
                '3:  ldaxr w11, [x10]', '    sub w11, w11, #1', '    stlxr w12, w11, [x10]', '    cbnz w12, 3b',
                '    dmb ish', '    cbnz w11, 2f',
                '    ldr w9, [x0]', '    ldr w9, [x9, #%d]' % (4 * (k + 1)), '    br x9',
                '2:  ret', '    .p2align 2', '1:  .word %s' % addr_sym(g)]
    return '\n'.join(head + body + ['    .size %s, .-%s' % (name, name)]) + '\n'
