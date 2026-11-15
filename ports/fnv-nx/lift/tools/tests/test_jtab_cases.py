#!/usr/bin/env python3
"""Cases of tools/jtab.py on raw x86-32 bytes (one function at FN, a table at TAB, a read-only byte table, a writable one): signed guard pairs, byte-table indices in a read-only section only,
`test/jne` facts through a copy, frame cells (kept only while no store through another pointer, no call, no escaping frame address), a constant held in a register, and the negative cases that must be red.
Ported from the cases of tabguard_test.py (w3).   python3 test/jtab_cases_test.py"""
import os, struct, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'tools'))
import capstone
from capstone import x86 as X
import jtab
BASE = 0x400000; FN = BASE + 0x1000; TAB = FN + 0x100; RO = BASE + 0x2000; RW = BASE + 0x3000
OUT = [0x500000 + 0x10 * i for i in range(8)]
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True
def u32(v): return struct.pack('<I', v & 0xffffffff)
def u32v(a): return TAB if a else 0
def tab(*v): return b''.join(u32(x) for x in v)
def jmp_tab(disp, reg=0x85): return b'\xff\x24' + bytes([reg]) + u32(disp)

jtab.RO = lambda lo, hi: ('.text', 0x60000020) if FN <= lo and hi <= RO else (('.rdata', 0x40000040) if RO <= lo and hi <= RW else (('.data', 0xc0000040) if RW <= lo and hi <= RW + 0x1000 else None))

class earlier:
    '''FRAME_ESCAPE_OTHER = True: the earlier rule, a frame-address form that is not proved benign turns the cells off for the cone'''
    def __enter__(s): jtab.FRAME_ESCAPE_OTHER = True
    def __exit__(s, *a): jtab.FRAME_ESCAPE_OTHER = False

def run(code, table=b'', ro=b'', rw=b'', relocs=()):
    mem = {}
    for i, b in enumerate(code): mem[FN + i] = b
    for i, b in enumerate(table): mem[TAB + i] = b
    ro_ = {RO + i: b for i, b in enumerate(ro)}; rw_ = {RW + i: b for i, b in enumerate(rw)}
    def u32_(a):
        return int.from_bytes(bytes((mem.get(a + i) if a + i in mem else ro_.get(a + i, rw_.get(a + i, 0))) for i in range(4)), 'little')
    def u8(a): return ro_.get(a) if a in ro_ else (None if a in rw_ else mem.get(a, 0))
    def ins_at(a): return next(md.disasm(bytes(mem.get(a + i, 0xcc) for i in range(15)), a, 1), None) if a in mem else None
    dec = {}; sites = []; work = [FN]; rset = set(relocs)
    while work:
        a = work.pop()
        while a not in dec and a in mem:
            i = ins_at(a); mn = i.mnemonic
            tg = (i.operands[0].imm,) if mn.startswith('j') and i.operands and i.operands[0].type == X.X86_OP_IMM else ()
            dec[a] = (i.size, mn, 0, mn not in ('jmp', 'ret'), tg)
            if mn == 'jmp' and i.operands[0].type == X.X86_OP_MEM: sites.append(a)
            work.extend(t for t in tg if t in mem)
            if mn in ('jmp', 'ret'): break
            a += i.size
    pm = {}
    for a, (sz, mn, ft, fall, tg) in dec.items():
        for t in tg: pm.setdefault(t, set()).add(a)
        if fall: pm.setdefault(a + sz, set()).add(a)
    assert len(sites) == 1, sites
    return jtab.resolve(sites[0], dec, ins_at, lambda x: pm.get(x, ()), lambda x: x == FN, rset, u32_, u8, lambda v: 0x500000 <= v < 0x500100, 0x1000)

class P(unittest.TestCase):
    def ok(s, want, *a, **k):
        t, why = run(*a, **k); s.assertIsNone(why); s.assertEqual(sorted(t), sorted(want))
    def red(s, *a, **k):
        t, why = run(*a, **k); s.assertIsNone(t); return why
    def test_unsigned_guard_exact(s):
        code = b'\x83\xf8\x03\x77\x0a' + jmp_tab(TAB) + b'\xcc\xcc\xcc\xc3'
        s.ok(OUT[:4], code, tab(*OUT[:5]), relocs=[FN + 8] + [TAB + 4 * i for i in range(5)])
    def test_signed_pair_lower_bound_above_zero(s):
        code = b'\x83\xf8\x02\x7c\x0c\x83\xf8\x04\x7f\x07' + jmp_tab(TAB - 8) + b'\xc3'
        s.ok(OUT[:3], code, tab(*OUT[:3]), relocs=[FN + 13] + [TAB + 4 * i for i in range(3)])
    def test_byte_table_index(s):
        code = b'\x83\xf8\x03\x77\x0e' + b'\x0f\xb6\x88' + u32(RO) + jmp_tab(TAB, 0x8d) + b'\xc3'
        s.ok(OUT[:2], code, b'\x90' * 4 + tab(OUT[0], OUT[1]), ro=bytes([1, 2, 2, 1]), relocs=[FN + 8, FN + 15, TAB + 4, TAB + 8])
    def test_test_jne_fact_through_mov_and(s):
        code = b'\xf7\xc7\x03\x00\x00\x00\x75\x02\xc3\x90\x89\xf8\x83\xe0\x03' + jmp_tab(TAB)
        s.ok(OUT[:3], code, b'\x90' * 4 + tab(*OUT[:3]), relocs=[FN + 18] + [TAB + 4 * i for i in (1, 2, 3)])
    def test_frame_cell_guard(s):
        code = b'\x89\x55\xec\x83\x7d\xec\x04\x77\x0a\x8b\x45\xec' + jmp_tab(TAB) + b'\xc3'
        s.ok(OUT[:5], code, tab(*OUT[:5]), relocs=[FN + 15] + [TAB + 4 * i for i in range(5)])
    def test_esp_cell_guard(s):
        # mov [esp+0x14],eax / cmp eax,0xe -> the cell is refined through its alias: mov edx,[esp+0x14] / jmp [edx*4+TAB]
        code = b'\x89\x44\x24\x14\x83\xf8\x03\x77\x0b\x8b\x54\x24\x14' + jmp_tab(TAB, 0x95) + b'\xc3'
        s.ok(OUT[:4], code, tab(*OUT[:4]), relocs=[FN + 16] + [TAB + 4 * i for i in range(4)])
    def test_other_register_holds_the_constant(s):
        code = b'\xba\x03\x00\x00\x00\x39\xd0\x77\x07' + jmp_tab(TAB) + b'\xc3'
        s.ok(OUT[:4], code, tab(*OUT[:4]), relocs=[FN + 12] + [TAB + 4 * i for i in range(4)])
    def test_flags_through_neutral_store(s):
        code = b'\x83\xf9\x03\x89\x7d\xfc\x77\x08' + b'\x8b\xc1' + jmp_tab(TAB) + b'\xc3'
        s.ok(OUT[:4], code, tab(*OUT[:4]), relocs=[FN + 13] + [TAB + 4 * i for i in range(4)])
    def test_push_imm_pop_constant(s):
        code = b'\x6a\x03\x5a\x39\xd0\x77\x07' + jmp_tab(TAB) + b'\xc3'
        s.ok(OUT[:4], code, tab(*OUT[:4]), relocs=[FN + 10] + [TAB + 4 * i for i in range(4)])
    def test_no_guard(s): s.assertIn('unbounded', s.red(jmp_tab(TAB), tab(*OUT[:4]), relocs=[FN + 3] + [TAB + 4 * i for i in range(4)]))
    def test_guard_on_other_register(s):
        code = b'\x83\xf9\x03\x77\x0a' + jmp_tab(TAB) + b'\xcc\xcc\xcc\xc3'
        s.red(code, tab(*OUT[:4]), relocs=[FN + 8] + [TAB + 4 * i for i in range(4)])
    def test_join_with_unguarded_path(s):
        code = b'\x85\xc9\x74\x05\x83\xf8\x03\x77\x07' + jmp_tab(TAB) + b'\xc3'
        s.red(code, tab(*OUT[:4]), relocs=[FN + 12] + [TAB + 4 * i for i in range(4)])
    def test_call_between_guard_and_jmp(s):
        code = b'\x83\xf8\x03\x77\x0c\xe8' + u32(0x600000 - (FN + 10)) + jmp_tab(TAB) + b'\xc3'
        s.red(code, tab(*OUT[:4]), relocs=[FN + 13] + [TAB + 4 * i for i in range(4)])
    def test_signed_upper_guard_only(s):
        code = b'\x83\xf8\x03\x7f\x0a' + jmp_tab(TAB) + b'\xcc\xcc\xcc\xc3'
        s.red(code, tab(*OUT[:4]), relocs=[FN + 8] + [TAB + 4 * i for i in range(4)])
    def test_byte_table_in_writable_section(s):
        code = b'\x83\xf8\x03\x77\x0e' + b'\x0f\xb6\x88' + u32(RW) + jmp_tab(TAB, 0x8d) + b'\xc3'
        s.red(code, b'\x90' * 4 + tab(OUT[0], OUT[1]), rw=bytes([1, 2, 2, 1]), relocs=[FN + 8, FN + 15, TAB + 4, TAB + 8])
    def test_proved_slot_not_relocated(s):
        code = b'\x83\xf8\x03\x77\x0a' + jmp_tab(TAB) + b'\xcc\xcc\xcc\xc3'
        s.assertIn('not a relocated dword', s.red(code, tab(*OUT[:4]), relocs=[FN + 8] + [TAB + 4 * i for i in range(3)]))
    def test_store_through_pointer_drops_the_cell(s):
        code = b'\x89\x55\xec\x83\x7d\xec\x04\x77\x10\xc7\x01\x00\x00\x00\x00\x8b\x45\xec' + jmp_tab(TAB) + b'\xc3'
        s.red(code, tab(*OUT[:5]), relocs=[FN + 21] + [TAB + 4 * i for i in range(5)])
    def test_escaping_frame_address_drops_cells(s):
        code = b'\x8d\x4d\xec\x89\x55\xec\x83\x7d\xec\x04\x77\x0a\x8b\x45\xec' + jmp_tab(TAB) + b'\xc3'
        with earlier(): s.red(code, tab(*OUT[:5]), relocs=[FN + 18] + [TAB + 4 * i for i in range(5)])
class F(unittest.TestCase):
    """R-B (a join kills only esp cells; flag records of one (kind, loc, imm) join their intervals) and R-A(i) (precise frame-address escapes)"""
    def rel(s, code_prefix, tail_at):
        return code_prefix
    def ok(s, want, *a, **k):
        t, why = run(*a, **k); s.assertIsNone(why); s.assertEqual(sorted(t), sorted(want))
    def red(s, *a, **k):
        t, why = run(*a, **k); s.assertIsNone(t); return why
    R5 = [TAB + 4 * i for i in range(5)]
    def test_join_with_different_esp_depth_keeps_ebp_cells(s):
        code = (b'\x89\x55\xec\x83\x7d\xec\x04\x77\x0f' + b'\x85\xc9\x74\x01\x50' + b'\x8b\x45\xec' + jmp_tab(TAB) + b'\xc3')
        s.ok(OUT[:5], code, tab(*OUT[:5]), relocs=[FN + 20] + s.R5)
    def test_join_with_different_esp_depth_drops_esp_cells(s):
        code = (b'\x89\x54\x24\x14\x83\x7c\x24\x14\x04\x77\x10' + b'\x85\xc9\x74\x01\x50' + b'\x8b\x44\x24\x14' + jmp_tab(TAB) + b'\xc3')
        s.red(code, tab(*OUT[:5]), relocs=[FN + 26] + s.R5)
    def test_flag_records_of_one_compare_join(s):
        code = (b'\x85\xc9\x74\x08\x83\xe0\x07\x83\xf8\x03\xeb\x03\x83\xf8\x03' + b'\x77\x07' + jmp_tab(TAB) + b'\xc3')
        s.ok(OUT[:4], code, tab(*OUT[:4]), relocs=[FN + 20] + [TAB + 4 * i for i in range(4)])
    def seh(s, cell):
        # lea eax,[ebp-0xc] / mov fs:[0],eax / mov eax,edx : the registration of an 8-byte EXCEPTION_REGISTRATION_RECORD at [ebp-0xc, ebp-4)
        return (b'\x8d\x45\xf4\x64\xa3\x00\x00\x00\x00\x89\xd0' + b'\x89\x55' + bytes([cell]) + b'\x83\x7d' + bytes([cell]) + b'\x04\x77\x0a' +
                b'\x8b\x45' + bytes([cell]) + jmp_tab(TAB) + b'\xc3')
    def test_seh_registration_escapes_only_the_record(s):
        s.ok(OUT[:5], s.seh(0xec), tab(*OUT[:5]), relocs=[FN + 26] + s.R5)
    def test_seh_registration_cell_inside_the_record_is_not_tracked(s):
        s.red(s.seh(0xf8), tab(*OUT[:5]), relocs=[FN + 26] + s.R5)
    def test_seh_address_used_elsewhere_stays_whole_cone(s):
        code = b'\x8d\x45\xf4\x64\xa3\x00\x00\x00\x00\x89\xc1\x89\xd0' + b'\x89\x55\xec\x83\x7d\xec\x04\x77\x0a\x8b\x45\xec' + jmp_tab(TAB) + b'\xc3'
        with earlier(): s.red(code, tab(*OUT[:5]), relocs=[FN + 28] + s.R5)
    def cookie(s, load):
        return load + b'\x31\xe8\x89\x55\xec\x83\x7d\xec\x04\x77\x0a\x8b\x45\xec' + jmp_tab(TAB) + b'\xc3'
    def test_cookie_xor_escapes_nothing(s):
        s.ok(OUT[:5], s.cookie(b'\xa1' + u32(RW)), tab(*OUT[:5]), relocs=[FN + 22] + s.R5)
    def test_xor_with_a_pointer_loaded_value_stays_whole_cone(s):
        with earlier(): s.red(s.cookie(b'\x8b\x03\x90\x90\x90'), tab(*OUT[:5]), relocs=[FN + 22] + s.R5)
    def test_other_frame_address_forms_stay_whole_cone(s):
        code = b'\x89\xe9\x89\x55\xec\x83\x7d\xec\x04\x77\x0a\x8b\x45\xec' + jmp_tab(TAB) + b'\xc3'
        with earlier(): s.red(code, tab(*OUT[:5]), relocs=[FN + 17] + s.R5)
    def cert(s, code, **k):
        t, why = run(code, tab(*OUT[:5]), **k); s.assertIsNone(why); return t.cert
    def test_owner6_certificate_for_an_escaped_frame_address(s):
        code = b'\x8d\x4d\xec\x89\x55\xec\x83\x7d\xec\x04\x77\x0a\x8b\x45\xec' + jmp_tab(TAB) + b'\xc3'
        c = s.cert(code, relocs=[FN + 18] + s.R5)
        s.assertEqual((c['kind'], c['jmp_va'], c['guard_va'], c['reload_va'], c['cell'], c['jcc'], c['jcc_bytes'], c['edge'], c['N'], c['extent_rule'], c['guard_extent'], c['extent'], c['B'], c['assumption']),
                      ('jtab-guard', FN + 15, FN + 10, FN + 12, {'base': 'ebp', 'disp': -0x14, 'width': 4}, 'ja', '770a', 'not-taken', 4, 'N+1 inclusive bound', 5, 5, None, 'ASM-006'))
        with earlier(): s.red(code, tab(*OUT[:5]), relocs=[FN + 18] + s.R5)
    def test_certificate_names_its_sections_and_is_written_with_a_dispatcher_check(s):
        import tempfile, json
        code = b'\x8d\x4d\xec\x89\x55\xec\x83\x7d\xec\x04\x77\x0a\x8b\x45\xec' + jmp_tab(TAB) + b'\xc3'
        t, why = run(code, tab(*OUT[:5]), relocs=[FN + 18] + s.R5); c = t.cert
        s.assertEqual(c['table_section'], {'name': '.text', 'flags': 0x60000020})
        d = tempfile.mkdtemp(); ok = {FN + 15: t}
        s.assertEqual(jtab.write_certs(d, 'exe', 'x' * 64, ok, {}, set(OUT)), (1, 0))
        s.assertEqual(json.load(open(d + '/jtabcert.json'))['certificates'][0]['entries_dispatch'], 5)
        s.assertEqual(jtab.write_certs(d, 'exe', 'x' * 64, ok, {}, set(OUT[:4])), (0, 1))          # one entry without a dispatcher entry: residue, no certificate
    def test_store_into_another_cell_after_the_guard_is_residue(s):
        code = b'\x8d\x4d\xec\x89\x55\xec\x83\x7d\xec\x04\x77\x10\x8b\x45\xec\x89\x45\xe8\x8b\x45\xe8' + jmp_tab(TAB) + b'\xc3'
        t, why = run(code, tab(*OUT[:5]), relocs=[FN + 24] + s.R5)
        s.assertTrue(t is None or t.cert is None)
    def test_certificate_through_a_byte_table(s):
        code = b'\x8d\x4d\xec\x89\x55\xec\x83\x7d\xec\x03\x77\x11\x8b\x45\xec' + b'\x0f\xb6\x88' + u32(RO) + jmp_tab(TAB, 0x8d) + b'\xc3'
        t, why = run(code, b'\x90' * 4 + tab(OUT[0], OUT[1]), ro=bytes([1, 2, 2, 1]), relocs=[FN + 25, TAB + 4, TAB + 8])
        s.assertIsNone(why); c = t.cert
        s.assertEqual((c['B'], c['b_extent'], c['extent'], c['steps']), (RO, 4, 2, ((FN + 12, 'load', -0x14), (FN + 15, 'byte', RO))))
    def test_no_guard_on_the_cell_has_no_certificate(s):
        code = b'\x8d\x4d\xec\x89\x55\xec\x83\xfa\x04\x77\x0b\x8b\x45\xec' + jmp_tab(TAB) + b'\xc3'     # the guard is on edx, the reload is of the cell
        s.red(code, tab(*OUT[:5]), relocs=[FN + 17] + s.R5)
    def test_store_to_the_cell_after_the_guard_drops_the_certificate(s):
        code = b'\x8d\x4d\xec\x89\x55\xec\x83\x7d\xec\x04\x77\x11\xc7\x45\xec\x09\x00\x00\x00\x8b\x45\xec' + jmp_tab(TAB) + b'\xc3'
        t, why = run(code, tab(*OUT[:10]), relocs=[FN + 25] + [TAB + 4 * i for i in range(10)])
        s.assertTrue(t is None or t.cert is None or t.cert['extent'] == 1)
    def test_table_in_a_writable_section_has_no_certificate(s):
        code = b'\x8d\x4d\xec\x89\x55\xec\x83\x7d\xec\x04\x77\x0a\x8b\x45\xec' + jmp_tab(RW) + b'\xc3'
        t, why = run(code, b'', rw=tab(*OUT[:5]), relocs=[FN + 18] + [RW + 4 * i for i in range(5)])
        s.assertIsNone(t)
        s.assertTrue(any('C4' in w for _, w in jtab.STATS.get('guard_residue', [])))
    def test_signed_guard_has_no_certificate(s):
        code = b'\x8d\x4d\xec\x89\x55\xec\x83\x7d\xec\x04\x7f\x0a\x8b\x45\xec' + jmp_tab(TAB) + b'\xc3'
        s.red(code, tab(*OUT[:5]), relocs=[FN + 18] + s.R5)
class E(unittest.TestCase):
    def test_second_reference_into_the_table_keeps_targets_seeded(s):
        code = b'\x83\xf8\x03\x77\x0a' + jmp_tab(TAB) + b'\xcc\xcc\xcc\xc3'
        t, why = run(code, tab(*OUT[:5]), relocs=[FN + 8] + [TAB + 4 * i for i in range(5)])
        ok = {FN + 5: t}
        sv = sorted([(u32v(FN + 8), FN + 8)] + [(OUT[i], TAB + 4 * i) for i in range(5)])
        s.assertEqual(jtab.exclusive(ok, sv), set(t.addrs))                                  # the only reference into the words is the jump's displacement
        sv2 = sorted(sv + [(TAB + 8, FN + 0x80)])                                              # a second relocated reference to the middle of the table
        s.assertEqual(jtab.exclusive(ok, sv2), set())
if __name__ == '__main__': unittest.main()
