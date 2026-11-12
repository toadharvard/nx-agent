"""R3 refined / span / retn rules (tools/residue.py), on hand-assembled bytes (no game data). Run: python3 test/r3_refine_test.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
import capstone, residue
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True
N = 0; BAD = 0


def ck(name, got, want):
    global N, BAD
    N += 1
    if got != want:
        BAD += 1; print('FAIL', name, 'got', got, 'want', want)


def disp_case(code, site_off):
    """code at T0; mark every instruction by linear decode; -> disp_only for the 4-byte site at T0 + site_off"""
    T0 = 0x401000; cov = bytearray(len(code)); text = bytes(code)
    for i in md.disasm(text, T0):
        o = i.address - T0; cov[o] = 1
        for k in range(1, i.size): cov[o + k] = 2
    return residue.disp_only(md, text, T0, cov, T0 + site_off)


ck('jmp [eax*4+V]', disp_case(b'\xff\x24\x85\x00\x20\x40\x00', 3), True)
ck('movzx eax,[eax+V]', disp_case(b'\x0f\xb6\x80\x00\x20\x40\x00', 3), True)
ck('mov eax,[V]', disp_case(b'\xa1\x00\x20\x40\x00', 1), True)
ck('call [V]', disp_case(b'\xff\x15\x00\x20\x40\x00', 2), True)
ck('lea eax,[V] is a value', disp_case(b'\x8d\x05\x00\x20\x40\x00', 2), False)
ck('mov eax,V is a value', disp_case(b'\xb8\x00\x20\x40\x00', 1), False)
ck('push V is a value', disp_case(b'\x68\x00\x20\x40\x00', 1), False)
ck('mov [esi],V imm is a value', disp_case(b'\xc7\x06\x00\x20\x40\x00', 2), False)
ck('mov [V],imm: disp site', disp_case(b'\xc7\x05\x00\x20\x40\x00\x01\x00\x00\x00', 2), True)
ck('mov [V],V imm site is a value', disp_case(b'\xc7\x05\x00\x20\x40\x00\x00\x20\x40\x00', 6), False)
ck('not in reached flow', residue.disp_only(md, b'\xa1\x00\x20\x40\x00', 0x401000, bytearray(5), 0x401001), False)
ck('untrusted instruction (bad)', residue.disp_only(md, b'\xa1\x00\x20\x40\x00', 0x401000, bytearray([1, 2, 2, 2, 2]), 0x401001, bad={0x401000}), False)

ck('span: above the entry', residue.span(0x1000, [(0x1000, 4), (0x1010, 2), (0xff0, 0x40)]), 0x12)
ck('span: nothing above', residue.span(0x1000, []), 0)


def facts(code, tabs=(), imports=None):
    T0 = 0x401000; ins = {i.address: i for i in md.disasm(code, T0)}
    return residue.body_facts(ins, set(tabs), None, None, imports or {})


ck('ret', facts(b'\xc3')['rets'], {0})
ck('ret 8', facts(b'\xc2\x08\x00')['rets'], {8})
ck('direct jmp is no open', facts(b'\xeb\x00\xc3')['open'], [])
ck('jmp eax is open', facts(b'\xff\xe0')['open'], ['indirect-jmp'])
ck('jmp [IAT] names the import', facts(b'\xff\x25\x00\x30\x40\x00', imports={0x403000: 'k.dll!Foo'})['open'], ['import:k.dll!Foo'])
ck('jump table jmp is no open', facts(b'\xff\x24\x85\x00\x20\x40\x00', tabs=[0x401000])['open'], [])
ck('retf', facts(b'\xcb')['far'], True)


def row(**k):
    d = dict(rets=set(), far=False, open=[], tails=set(), outside=False, noreturn=False); d.update(k); return d


R = residue.retn_rows({1: row(rets={0}), 2: row(rets={8}), 3: row(rets={0, 4}), 4: row(), 5: row(tails={2}), 6: row(tails={1, 2}), 7: row(open=['indirect-jmp']), 8: row(rets={0}, outside=True),
                       9: row(rets={0}, noreturn=True), 10: row(tails={11}, rets={0}), 11: row(tails={10})})
D = {e: (p, n) for e, p, n, _ in R}
ck('caller', D[1], ('caller', '0')); ck('callee', D[2], ('callee', '8')); ck('mixed', D[3], ('mixed', '-')); ck('no ret site', D[4], ('noreturn', '-'))
ck('tail takes the value', D[5], ('callee', '8')); ck('tails merge', D[6], ('mixed', '-')); ck('open', D[7][0], 'open'); ck('leaving to a non-entry', D[8][0], 'open')
ck('NORETURN set', D[9], ('noreturn', '-')); ck('tail cycle', D[10], ('caller', '0')); ck('tail cycle 2', D[11], ('caller', '0'))
print('%d checks, %d failed' % (N, BAD)); sys.exit(1 if BAD else 0)
