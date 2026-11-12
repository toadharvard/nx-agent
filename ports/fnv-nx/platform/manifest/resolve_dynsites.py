#!/usr/bin/env python3
"""resolve_dynsites.py - one rule that resolves the name argument of every dynamic-name site of dynsites.tsv (L5 platform).

    resolve_dynsites.py --contract DIR --sites dynsites.tsv --image NAME=pe ... --facts DIR [--bridge bridge.tsv]
                        [--escape import_escape.tsv ...] --out DIR [--rewrite dynsites_out.tsv] | --selftest

DIR is the contract/ directory of the trunk (L1: argflow.py, memfx.py, gpa_names.py, modules.py); the images and every fact are PATH
ARGUMENTS of the private, exe-derived inputs: nothing of them is embedded here.

RULE (constant propagation of the name argument; the one rule of contract/argflow.py, applied to every name-lookup site):
  * the sites are the rows of dynsites.tsv with api LoadLibraryA/W, GetModuleHandleA/W or GetProcAddress (CoCreateInstance has no
    module name and is not a site of this rule);
  * the uses of a row are the calls that use its import slot (argflow.uses_of_slot_ref: the call itself; every direct call of a
    jmp [slot] thunk; every `call reg` after mov reg,[slot]); a thunk or slot that escapes (address taken, tail jump, returned
    value) makes the row `open`; a slot reference in bytes no entry of the ranges reaches has no use (`unreached`);
  * at every use, the name argument is derived backwards over all paths of the function (argflow.derive_arg): D1 string
    constant, D2 constant-indexed table of constants, D3 ordinal, D4 own parameter expanded to every direct caller, the result
    of an earlier resolved call, a global cell whose every writer is proven (memfx);
  * LoadLibrary*/GetModuleHandle*: the leaves are module names (GetModuleHandle(NULL) is the image of the exe); an immediate 0 of
    a LoadLibrary is the failing call and contributes nothing; GetProcAddress: gpa_names.derive_site (module set x name set);
  * hModule rule NOT_A_MODULE: an immediate below 0x10000 is no module handle (documented Win32 behaviour): GetProcAddress fails on it and
    contributes no pair, as NULL does;
  * cell rule (pemanifest's, stated in `cell`): a load of a writable global cell g whose every reference is a plain `[g]` operand
    (no lea, no stored or pushed address) has the value set image-bytes-of-g + the value at every `mov [g], src`; a row that uses a cell
    is `conditional`, never `closed`: memfx.const_range does not prove that no indexed store reaches g (array extents are not a fact of
    the images). It is reported, never rewritten into dynsites.tsv;
  * a row is `closed` when every use has a derived set and no escape. A name computed from run-time data (registry, file,
    environment) is no leaf of the rule: it stays `open`, with the break that names it, and is reported - it is never guessed.
Ranges: the pinned L3a function facts through funcfacts.py (`--facts DIR`); an image without facts has none, so its rows stay open.
Output: sites_resolved.tsv (module, site, api, old operand, status, resolved names, proof, breaks) and the global counts.
--rewrite writes dynsites.tsv with the operand of every closed `dynamic` row replaced by the resolved names (module tokens,
`.dll` form, then names, space separated, as the other rows carry them) and the hmod column of GetProcAddress rows by the module set.
Exit 1 when a row is open. """
import argparse
import os
import re
import struct
import sys

CONTRACT = None


def load_contract(d):
    global CONTRACT
    sys.path.insert(0, d)
    import argflow, gpa_names, memfx, iat  # noqa: E401
    CONTRACT = (argflow, gpa_names, memfx, iat)
    return CONTRACT


LOADERS = ('LoadLibraryA', 'LoadLibraryW', 'GetModuleHandleA', 'GetModuleHandleW')
SITE_APIS = LOADERS + ('GetProcAddress',)
EXEC = 0x20000000


def load_image(argflow, spec, exe_name=None):
    """spec NAME=pe-file. The function facts are the pinned L3a ranges loaded by funcfacts.py (`--facts DIR`: tree_sha256 and image sha256
    checked, rebased to the image base); an image without facts has no ranges (its rows stay open with the reason)."""
    import pefile
    import funcfacts
    nm, pe = spec.split('=', 1)
    with open(pe, 'rb') as fh:
        data = fh.read()
    pf = pefile.PE(data=data, fast_load=True)
    ff = funcfacts.for_image(nm.lower(), pf.get_memory_mapped_image(), pf.OPTIONAL_HEADER.ImageBase, pe)
    ranges = {e: list(rs) for e, rs in ff.runs.items()} if ff.ok else {}
    return nm.lower(), argflow.Image(data, nm.lower(), ranges, exe_name), data


def derive_loader(argflow, img, site, api):
    """-> dict(status, names {module: chain}, uses, proof, breaks) for a LoadLibrary*/GetModuleHandle* row."""
    wide = api.endswith('W')
    calls, open_ = uses(argflow, img, site, api)
    out = dict(uses=calls, names={}, proof=[], breaks=['%x %s' % b for b in open_], note='', cond=False)
    if not calls and not open_:
        out['note'] = 'unreached'
        out['proof'].append('unreached@%x' % site)
    for u in calls:
        r = argflow.derive_arg(img, u, 0)
        for va, why in sorted(r.breaks):
            m = GLOBAL.search(why)
            if m:
                lv, cw = cell(argflow, img, int(m.group(1), 16), wide)
                out['breaks'] += ['name@%x cell %s: %s' % (u, m.group(1), w) for w in cw]
                if not cw:
                    out['cond'] = True
                    for kind, val in sorted(lv, key=repr):
                        if kind == 'str' and val is not None:
                            out['names'][val] = ('cell', int(m.group(1), 16))
                        elif not (kind == 'imm' and val == 0):
                            out['breaks'].append('name@%x cell %s holds %s %r' % (u, m.group(1), kind, val))
                    out['proof'].append('call@%x<-cell:%s' % (u, m.group(1)))
                continue
            out['breaks'].append('name@%x %x %s' % (u, va, why))
        for kind, val, chain in r.sorted_leaves():
            if kind == 'str' and val[1 if wide else 0] is not None:
                out['names'][val[1 if wide else 0]] = chain
                out['proof'].append('call@%x<-str:%s' % (u, '.'.join('%x' % c if isinstance(c, int) else str(c) for c in chain)))
            elif kind in ('imm', 'ord') and val == 0:
                if api.startswith('GetModuleHandle'):
                    out['names'][img.exe_name] = chain       # GetModuleHandle(NULL) = the module of the process image
                out['proof'].append('call@%x<-null' % u)
            else:
                out['breaks'].append('name@%x %s %r' % (u, kind, val))
    out['status'] = ('conditional' if out['cond'] else 'closed') if not out['breaks'] else 'open'
    return out


# RULE NOT_A_MODULE: an hModule that is an immediate below 0x10000 is no module handle (Windows documents the handle as the image base,
# a nonzero multiple of 64 KiB): GetProcAddress on it fails (ERROR_INVALID_HANDLE) and contributes no pair, as NULL does (documented Win32 behaviour, owner item 3).
NOT_A_MODULE = re.compile(r'hModule is imm (\d+)')
GLOBAL = re.compile(r'load of writable global ([0-9a-f]+)')


def cell_index(img):
    """{disp: [instruction address]}: every instruction of the ranges with a memory operand `[disp]` (no base, no index), once per
    image; a linear decode of the merged ranges that keeps no instruction object (the exe holds millions)."""
    ix = getattr(img, '_cells', None)
    if ix is None:
        ix = img._cells = {}
        merged = []
        for a, b in sorted({(a, b) for a, b, e in img.flat}):
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        for a, b in merged:
            for i in img.cs.disasm(img.pe.rd(a - img.base, b - a), a):
                for op in i.operands:
                    if op.type == 3 and not op.mem.base and not op.mem.index:
                        ix.setdefault(op.mem.disp & 0xffffffff, []).append(i.address)
    return ix


def cell(argflow, img, g, wide=False):
    """The values of the writable 4-byte global cell g under the cell rule (pemanifest's rule, stated): every relocated word
    that holds g's address is the displacement of a plain `[g]` operand in code (never a stored or pushed address, never `lea`), and
    every instruction with that operand either reads the cell or is `mov [g], reg|imm`; then the value set is the image bytes of g
    plus the value of the source at every such store. -> (leaves {('str', s) | ('imm', n) | ('ret', call_va)}, why-not [str]).
    CONDITION, not proved here: no indexed store of the image reaches g (memfx.const_range lists them: the extent of an array
    is not a fact of the images), so a row that uses a cell is `conditional`, never `closed`."""
    why, leaves = [], set()
    ix = cell_index(img)
    uses_ = [img.insn(a) for a in ix.get(g, [])]
    for r in img.relocs:
        if img.in_raw(r, 4) and img.word(r) == g and not any(i.address < r < i.address + i.size for i in uses_):
            why.append('address of %x held at %x (not a plain [g] operand)' % (g, r))
    init = img.word(g) if img.in_raw(g, 4) else 0
    if g in img.relocs:
        t = img.cstr(init, wide)
        leaves.add(('str', t) if t is not None else ('imm', init))
    else:
        leaves.add(('imm', init))
    for i in uses_:
        m, ops = i.mnemonic, i.operands
        dst = bool(ops) and ops[0].type == 3 and ops[0].mem.disp & 0xffffffff == g and not ops[0].mem.base and not ops[0].mem.index
        if m == 'lea':
            why.append('%x lea %s' % (i.address, i.op_str))
        elif dst and m == 'mov' and ops[1].type == 2:
            leaves.add(('imm', ops[1].imm & 0xffffffff))
        elif dst and m == 'mov' and ops[1].type == 1:
            for e in argflow.entries_for(img, i.address):
                r = argflow.Flow(img, img.func(e)).run(i.address, ('reg', argflow.rn(i, ops[1].reg)))
                why += ['store at %x: %x %s' % (i.address, va, w) for va, w in sorted(r.breaks)]
                for kind, val, ch in r.sorted_leaves():
                    leaves.add(('ret', val) if kind == 'ret' else ('str', val[1 if wide else 0]) if kind == 'str' else ('imm', val))
        elif dst and m not in ('cmp', 'test', 'push'):
            why.append('%x %s %s' % (i.address, m, i.op_str))
    return leaves, why


def cell_modules(argflow, img, g):
    """Module names a handle cell can hold: ({name}, [why-not])."""
    leaves, why = cell(argflow, img, g)
    names = set()
    for kind, val in leaves:
        if kind == 'ret':
            n, b, z = argflow.call_result_modules(img, val)
            names |= set(n)
            why += ['%x %s' % x for x in sorted(b)]
        elif kind == 'imm' and val < 0x10000:
            pass            # NOT_A_MODULE (rule below)
        else:
            why.append('cell %x holds %s %r' % (g, kind, val))
    return names, why


def uses(argflow, img, site, api):
    """([call va], [(va, why)]) of the row: a row of the flow rule is a call of the slot's value (`call [slot]`, or `call reg` after
    mov reg,[slot] - the call itself is the use), a row of the window rule the operand of a slot reference (argflow's own rule)."""
    i = img.insn(site)
    if i and i.mnemonic == 'call' and i.operands and i.operands[0].type == 1 and slot_ref(img, site, api) == site:     # call reg
        return [site], []
    return argflow.uses_of_slot_ref(img, slot_ref(img, site, api))


def slot_ref(img, site, api):
    """The address of the relocated slot operand of the site: a row of the flow rule carries the address of the call instruction
    itself (`call [slot]`, the slot being the import of `api`), a row of the window rule the address of the operand."""
    i = img.insn(site)
    if i and i.size >= 5 and i.mnemonic in ('call', 'jmp', 'mov'):
        for op in i.operands:
            if op.type == 3 and not op.mem.base and not op.mem.index and img.imports.get(op.mem.disp & 0xffffffff, (0, 0))[1] == api:
                return site + i.size - 4       # the displacement is the last four bytes of `call [m]`, `jmp [m]`, `mov r,[m]`
    return site


def derive_gpa(argflow, img, site):
    """gpa_names.derive_site (L1) over the uses of this module's rule: names x modules of every call."""
    calls, open_ = uses(argflow, img, site, 'GetProcAddress')
    out = dict(uses=calls, pairs=set(), proof=[], breaks=['%x %s' % b for b in open_], mod_unknown=False, note='', cond=False)
    if not calls and not open_:
        out['note'] = 'unreached'
        out['proof'].append('unreached@%x' % site)
    for u in calls:
        names, nb = argflow.name_set(img, u)
        mods, mb = argflow.module_names(img, u)
        rest = set()
        for va, why in mb:           # a handle read from a global cell: the cell rule (conditional)
            nm_ = NOT_A_MODULE.fullmatch(why)
            if nm_ and int(nm_.group(1)) < 0x10000:
                continue
            m = GLOBAL.search(why)
            cm, cw = cell_modules(argflow, img, int(m.group(1), 16)) if m else (set(), ['x'])
            if m and not cw:
                mods.update({n: ('cell', int(m.group(1), 16)) for n in cm})
                out['cond'] = True
            else:
                rest.add((va, why))
        mb = rest
        out['breaks'] += ['name@%x %x %s' % (u, va, why) for va, why in sorted(nb)]
        out['mod_unknown'] |= bool(mb)
        dlls = sorted(m.lower() if m.lower().endswith('.dll') else m.lower() + '.dll' for m in mods) or ['*']
        if mb:
            dlls = sorted(set(dlls) | {'*'})
        for kind, nm, ch in names:
            for d in dlls:
                out['pairs'].add((d, nm))
        out['proof'].append('call@%x<-name:%s|mod:%s%s' % (u, ','.join(sorted({'%s:%s' % (k, '.'.join('%x' % c if isinstance(c, int) else str(c) for c in ch)) for k, n, ch in names}))[:120],
                            ','.join(sorted(mods)), '|modbreak:' + ';'.join('%x %s' % b for b in sorted(mb))[:160] if mb else ''))
    out['status'] = ('conditional' if out['cond'] else 'closed') if not out['breaks'] else 'open'
    return out


def resolve(images, rows):
    argflow, gpa_names, _, _ = CONTRACT
    res = []
    for mod, site, api, operand, hmod in rows:
        if api not in SITE_APIS:
            continue
        img = images[mod.lower()]
        if api == 'GetProcAddress':
            d = derive_gpa(argflow, img, site)
            mods = sorted({p[0] for p in d['pairs'] if p[0] != '*'})
            names = sorted({p[1] for p in d['pairs']})
            name_open = [b for b in d['breaks'] if b.split(' ')[0].startswith('name@')]
            other_open = [b for b in d['breaks'] if b not in name_open]
            need_name, need_mod = operand == 'dynamic', hmod == 'dynamic'
            ok = not other_open and not (need_name and name_open) and not (need_mod and d['mod_unknown'])
            res.append(dict(row=(mod, site, api, operand, hmod), status=('conditional' if d['cond'] else 'closed') if ok else 'open', mods=mods, names=names,
                            pairs=sorted(d['pairs']), proof=d['proof'], breaks=d['breaks'] if not ok else [], note=d['note'],
                            star=d['mod_unknown'], need_name=need_name, need_mod=need_mod))
        else:
            d = derive_loader(argflow, img, site, api)
            res.append(dict(row=(mod, site, api, operand, hmod), status=d['status'], mods=sorted(d['names']), names=[], pairs=[],
                            proof=d['proof'], breaks=d['breaks'], note=d['note'], star=False, need_name=operand == 'dynamic', need_mod=False))
    return res


def read_rows(text):
    rows = []
    for line in text.splitlines():
        f = line.split('\t')
        if len(f) < 5 or f[0] == 'module':
            continue
        rows.append((f[0], int(f[1], 16), f[2], f[3], f[4]))
    return rows


def write_sites(res, out):
    lines = ['module\tsite\tapi\told_operand\tstatus\tresolved\tproof\tbreaks']
    for r in res:
        mod, site, api, operand, hmod = r['row']
        got = ' '.join(r['mods'] + r['names']) if r['status'] in ('closed', 'conditional') else ''
        lines.append('\t'.join([mod, '0x%x' % site, api, operand, 'unreached' if r['note'] == 'unreached' else r['status'], got or '-',
                                ' '.join(r['proof']) or '-', ' / '.join(r['breaks']) or '-']))
    with open(os.path.join(out, 'sites_resolved.tsv'), 'w', newline='\n') as fh:
        fh.write('\n'.join(lines) + '\n')


def rewrite(text, res):
    """dynsites.tsv with every closed `dynamic` operand replaced by the derived names (a GetProcAddress operand: the names; a
    LoadLibrary/GetModuleHandle operand: the module names) and every closed `dynamic` hmod of a GetProcAddress row by the modules."""
    by = {(r['row'][0], r['row'][1]): r for r in res}
    out = []
    for line in text.splitlines():
        f = line.split('\t')
        if len(f) >= 5 and f[0] != 'module':
            r = by.get((f[0], int(f[1], 16)))
            if r and r['status'] == 'closed' and r['note'] != 'unreached':
                mods = [m if m.endswith('.dll') or '.' in m else m + '.dll' for m in r['mods']]
                if r['need_name'] and f[3] == 'dynamic':
                    f[3] = ' '.join(r['names'] if f[2] == 'GetProcAddress' else mods) or f[3]
                if r['need_mod'] and f[4] == 'dynamic' and mods:
                    f[4] = ','.join(mods)
        out.append('\t'.join(f))
    return '\n'.join(out) + '\n'


def counts(res):
    """Global numbers: every row that needs a derivation (a dynamic operand, or a dynamic hmod of a GetProcAddress row)."""
    c = dict(total=len(res), dyn=0, resolved=0, conditional=0, unreached=0, open=0, open_rows=[])
    for r in res:
        if not (r['need_name'] or r['need_mod']):
            continue
        c['dyn'] += 1
        if r['status'] == 'closed' and r['note'] == 'unreached':
            c['unreached'] += 1
        elif r['status'] == 'conditional':
            c['conditional'] += 1
        elif r['status'] == 'closed':
            c['resolved'] += 1
        else:
            c['open'] += 1
            c['open_rows'].append('%s@%x %s %s' % (r['row'][0], r['row'][1], r['row'][2], '; '.join(r['breaks'])[:160]))
    return c


def selftest():
    """The pure parts: a closed row is rewritten; conditional, unreached and open rows are left `dynamic` and counted apart."""
    def row(site, api, st, note='', mods=(), names=(), nn=True, nm=False):
        return dict(row=('m.dll', site, api, 'dynamic', 'dynamic' if api == 'GetProcAddress' else '-'), status=st, note=note, mods=list(mods),
                    names=list(names), pairs=[], proof=[], breaks=['b'] if st == 'open' else [], star=False, need_name=nn, need_mod=nm)
    res = [row(1, 'LoadLibraryA', 'closed', mods=['dbghelp.dll']), row(2, 'LoadLibraryA', 'conditional', mods=['x.dll']),
           row(3, 'LoadLibraryA', 'closed', note='unreached'), row(4, 'LoadLibraryA', 'open'),
           row(5, 'GetProcAddress', 'closed', mods=['kernel32'], names=['FlsAlloc'], nn=True, nm=True)]
    text = 'module\tsite\tapi\toperand\thmod\n' + ''.join('m.dll\t0x%x\t%s\t%s\t%s\n' % (r['row'][1], r['row'][2], r['row'][3], r['row'][4]) for r in res)
    out = rewrite(text, res).splitlines()
    assert out[1].split('\t')[3] == 'dbghelp.dll', out
    assert [o.split('\t')[3] for o in out[2:5]] == ['dynamic'] * 3, out
    assert out[5].split('\t')[3:5] == ['FlsAlloc', 'kernel32.dll'], out
    c = counts(res)
    assert (c['dyn'], c['resolved'], c['conditional'], c['unreached'], c['open']) == (5, 2, 1, 1, 1), c
    assert NOT_A_MODULE.fullmatch('hModule is imm 1') and not NOT_A_MODULE.fullmatch('hModule is str 1')
    assert GLOBAL.search('load of writable global 11b76d0').group(1) == '11b76d0'
    print('selftest ok')
    return 0


def main(argv=None):
    if argv is None and '--selftest' in sys.argv:
        return selftest()
    import funcfacts
    ap = argparse.ArgumentParser()
    ap.add_argument('--contract', required=True)
    ap.add_argument('--sites', required=True)
    ap.add_argument('--image', action='append', required=True)
    ap.add_argument('--bridge')
    ap.add_argument('--escape', action='append', default=[])
    ap.add_argument('--out', required=True)
    ap.add_argument('--rewrite')
    funcfacts.facts_dir_from_argv(sys.argv)
    a = ap.parse_args(argv)
    argflow, gpa_names, memfx, iat = load_contract(a.contract)
    images, bridge = {}, open(a.bridge).read() if a.bridge else ''
    exe = a.image[0].split('=', 1)[0].lower()      # the first image is the process image: GetModuleHandle(NULL) is its module in every image
    for spec in a.image:
        nm, img, _ = load_image(argflow, spec, exe)
        img.import_stack = gpa_names.import_stacks(bridge)
        images[nm] = img
    info = memfx.ImportInfo(bridge, [open(p).read() for p in a.escape], '')
    for im in images.values():
        memfx.Memfx(im, info)
    text = open(a.sites).read()
    rows = read_rows(text)
    miss = sorted({r[0] for r in rows if r[2] in SITE_APIS and r[0].lower() not in images})
    if miss:
        print('no image for: %s (their rows stay open)' % ', '.join(miss))
    res = resolve(images, [r for r in rows if r[0].lower() in images])
    for r in rows:
        if r[0].lower() not in images and r[2] in SITE_APIS:
            res.append(dict(row=r, status='open', mods=[], names=[], pairs=[], proof=[], breaks=['no image %s' % r[0]], note='', star=False,
                            need_name=r[3] == 'dynamic', need_mod=r[2] == 'GetProcAddress' and r[4] == 'dynamic'))
    os.makedirs(a.out, exist_ok=True)
    write_sites(res, a.out)
    if a.rewrite:
        with open(a.rewrite, 'w', newline='\n') as fh:
            fh.write(rewrite(text, res))
    c = counts(res)
    print('name-lookup rows: %d; rows needing a derivation (dynamic operand or dynamic hmod): %d; resolved: %d; conditional on the cell rule: %d; unreached (no use): %d; open: %d' %
          (c['total'], c['dyn'], c['resolved'], c['conditional'], c['unreached'], c['open']))
    for o in c['open_rows']:
        print('OPEN', o)
    return 1 if c['open'] else 0


if __name__ == '__main__':
    sys.exit(main())
