#!/usr/bin/env python3
"""link stage `milestones` - milestones.tsv for the emulator stage: addresses of the guest's boot milestones, each from a fact.

    milestones.py --exe FalloutNV.exe --modules modules.tsv --n1 n1.json --keys keys.tsv --entries entries.tsv --out milestones.tsv

Rows `name<TAB>kind<TAB>value<TAB>source` (value: 8 hex digits, the address in the guest's address space or the import key); the last
line is `# table_id <id>`, the id of keys.tsv, which must equal the id of entries.tsv.  The rows, in this order:
  exe_entry              exe_entry  PE ImageBase + AddressOfEntryPoint of the exe file.
  module_entry:<name>    module_entry  assigned_base + entry rva of every modules.tsv row with a non-zero entry rva (sorted by name);
                         a 5th column holds the row's `static` flag (1 = in the exe's import closure).
  WinMain                winmain  the address of the N1 function whose decorated name is the global function WinMain
                         (`?WinMain@@...` or `_WinMain@N`).  N1 (n1.py) holds a function only when its MSVC object bytes equal the exe's
                         bytes at that address, so the name -> address binding is proven; the certificate must carry the sha256 of this
                         exe.  No such function: no row (nothing is substituted).  Two with different addresses: stage error.
  import:<dll>!<name>    import  the import key of keys.tsv for the D3D9 calls that mark the first frame: dll `!idirect3ddevice9.dll` and
                         `!idirect3dswapchain9.dll`, name `Present`; a missing row is no row.
A pure function of its inputs; a violated precondition is a stage error."""
import argparse
import hashlib
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'contract'))
import modules
import store

PRESENT = (('!idirect3ddevice9.dll', 'Present'), ('!idirect3dswapchain9.dll', 'Present'))
WINMAIN = re.compile(r'^(\?WinMain@@|_WinMain@[0-9]+$)')


def table_id(text, what):
    ids = [l.split()[2] for l in text.split('\n') if l.startswith('# table_id ')]
    if len(ids) != 1:
        raise ValueError('%s: want exactly one `# table_id` line, found %d' % (what, len(ids)))
    return ids[0]


def rows(text):
    return [l.split('\t') for l in text.split('\n') if l and not l.startswith('#')]


def build(exe, modules_tsv, n1, keys_tsv, entries_tsv):
    tid = table_id(keys_tsv, 'keys.tsv')
    if table_id(entries_tsv, 'entries.tsv') != tid:
        raise ValueError('keys.tsv and entries.tsv have different table ids')
    if n1['exe_sha256'] != hashlib.sha256(exe).hexdigest():
        raise ValueError('n1.json is a certificate of another exe')
    pe = modules.PE(exe, 'exe')
    out = [('exe_entry', 'exe_entry', pe.base + pe.entry_rva, 'exe PE ImageBase+AddressOfEntryPoint', '')]
    for r in sorted(rows(modules_tsv), key=lambda r: r[0]):
        base, entry = int(r[4], 16), int(r[7], 16)
        if entry:
            out.append(('module_entry:' + r[0], 'module_entry', base + entry,
                        'modules.tsv assigned_base+entry_rva', r[8]))
    wm = sorted({a for a, f in n1['fn'].items() if f['n1'] and WINMAIN.match(f['sym'])})
    if len(wm) > 1:
        raise ValueError('n1.json: WinMain at several addresses')
    if wm:
        out.append(('WinMain', 'winmain', wm[0], 'n1.json N1 function %s' % n1['fn'][wm[0]]['sym'], ''))
    keys = {(r[1], r[2]): int(r[0], 16) for r in rows(keys_tsv)}
    for dll, name in PRESENT:
        if (dll, name) in keys:
            out.append(('import:%s!%s' % (dll, name), 'import', keys[(dll, name)], 'keys.tsv import key', ''))
    lines = []
    for name, kind, v, src, extra in out:
        lines.append('\t'.join([name, kind, '%08x' % v, src] + ([extra] if extra else [])) + '\n')
    return ''.join(lines) + '# table_id %s\n' % tid


def main():
    ap = argparse.ArgumentParser()
    for a in ('exe', 'modules', 'n1', 'keys', 'entries', 'out'):
        ap.add_argument('--' + a, required=True)
    a = ap.parse_args()
    rd = lambda p: open(p, encoding='ascii').read()
    try:
        text = build(open(a.exe, 'rb').read(), rd(a.modules), store.load(a.n1, 'n1'), rd(a.keys), rd(a.entries))
    except (ValueError, OSError) as e:
        sys.exit('milestones: %s' % e)
    with open(a.out, 'w', newline='\n') as fh:
        fh.write(text)


if __name__ == '__main__':
    main()
