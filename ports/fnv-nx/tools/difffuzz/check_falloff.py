#!/usr/bin/env python3
"""check_falloff.py - native functions whose code runs off their end into the next function.

GCC treats flowing off the end of a non-void C++ function as unreachable (no epilogue, no `ret`); MSVC simply
returned whatever eax held. The decomp has such functions (a body reconstructed without its return statement), and on
AArch64 they execute the following function. A function falls off when its last instruction is not a terminator
(ret, b, br, brk, udf) and not a call to a known noreturn function.

    check_falloff.py <objects dir> [--list]

Prints one line per falling function (object, symbol, original address when an `__fnv_` alias names it) and totals.
"""
import collections
import os
import sys

import capstone
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

CS = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
TERM = {'ret', 'b', 'br', 'brk', 'udf', 'hlt', 'retaa', 'retab', 'braa', 'brab', 'eret'}
NORETURN = ('__cxa_throw', '__cxa_rethrow', 'abort', '_Unwind_Resume', '__stack_chk_fail', 'exit', '_exit',
            '__cxa_pure_virtual', '__cxa_bad_cast', '__cxa_bad_typeid', 'longjmp', '__assert_fail', '_ZSt9terminatev',
            '__cxa_call_unexpected', '_ZSt17__throw_bad_allocv', '__builtin_trap', 'ExitThread', 'ExitProcess',
            '_endthreadex', '_endthread')


def scan(path):
    out = []
    with open(path, 'rb') as f:
        e = ELFFile(f)
        st = e.get_section_by_name('.symtab')
        if st is None:
            return out
        syms = list(st.iter_symbols())
        relocs = collections.defaultdict(dict)   # section index -> {offset: symbol name}
        for sec in e.iter_sections():
            if isinstance(sec, RelocationSection):
                for r in sec.iter_relocations():
                    relocs[sec['sh_info']][r['r_offset']] = syms[r['r_info_sym']].name
        alias = collections.defaultdict(list)
        for s in syms:
            if s.name.startswith('__fnv_') and s['st_info']['type'] == 'STT_FUNC' and isinstance(s['st_shndx'], int):
                alias[(s['st_shndx'], s['st_value'])].append(s.name[6:])
        seen = set()
        for s in syms:
            if s['st_info']['type'] != 'STT_FUNC' or not s['st_size'] or not isinstance(s['st_shndx'], int):
                continue
            key = (s['st_shndx'], s['st_value'])
            if key in seen or s.name.startswith('__fnv'):
                continue
            seen.add(key)
            sec = e.get_section(s['st_shndx'])
            data = sec.data()
            end = s['st_value'] + s['st_size']
            if end < 4 or end > len(data):
                continue
            last = next(CS.disasm(data[end - 4:end], end - 4), None)
            if last is None:
                continue
            m = last.mnemonic
            if m in TERM or m.startswith('b.') is False and m in ('cbz', 'cbnz', 'tbz', 'tbnz') and False:
                continue
            if m == 'bl':
                tgt = relocs[s['st_shndx']].get(end - 4, '')
                if tgt.startswith(NORETURN) or 'throw' in tgt or 'terminate' in tgt:
                    continue
            out.append((s.name, ','.join(alias.get(key, [])), m, relocs[s['st_shndx']].get(end - 4, '')))
    return out


def main():
    d = sys.argv[1]
    total, objs = 0, 0
    for n in sorted(os.listdir(d)):
        if not n.endswith('.o') or n.startswith('zz_'):
            continue
        try:
            hits = scan(os.path.join(d, n))
        except Exception as ex:  # noqa: BLE001
            print('%s\terror\t%s' % (n, ex), file=sys.stderr)
            continue
        if hits:
            objs += 1
            total += len(hits)
            if '--list' in sys.argv:
                for name, addrs, m, tgt in hits:
                    print('%s\t%s\t%s\t%s\t%s' % (n, addrs or '-', name, m, tgt))
    print('falling off the end: %d functions in %d objects' % (total, objs))


if __name__ == '__main__':
    main()
