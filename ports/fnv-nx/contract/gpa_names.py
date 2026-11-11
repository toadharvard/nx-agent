#!/usr/bin/env python3
"""gpa_names - the (module, name) pairs every GetProcAddress site of the images can ask for (L1 CONTRACT, import manifest).

    gpa_names.py --sites dynsites.tsv --image NAME=pe[,ranges.tsv] ... [--bridge bridge.tsv] [--modules modules.tsv] --out DIR

Input rows (dynsites.tsv of L5: module, site, api, operand): `site` is the address of the relocated 4-byte slot operand (at
the module's preferred base) of an instruction that reads the IAT slot of the import `api`.  Only api = GetProcAddress rows
are derived.  One rule for every row (argflow.py): the uses of the slot value are

  call [slot]                          the instruction itself;
  jmp [slot] (an import thunk)         every direct call of the thunk; the thunk's address taken by a relocated word, or a jump
                                       to it that is not a call, makes the site open; a thunk no code of the facts reaches
                                       (the facts are closed under direct targets and relocated code pointers) has no use;
  mov reg,[slot]                       every `call reg` on the forward paths (copies tracked; any other read of the register,
                                       a return of it in eax: the value escapes and the site is open).

At every use, lpProcName (stdcall argument 1) and hModule (argument 0) are derived by argflow.derive_arg: definitions D1 string,
D2 constant-indexed table, D3 ordinal, D4 own parameter (expanded to every caller, open when the function is address-taken).
hModule: result of LoadLibrary*/GetModuleHandle* with a derived string, directly or through a direct callee (parameters bound
to the arguments of the call); NULL contributes nothing (GetProcAddress(NULL, ..) fails).  A site is `closed` when every use
has a derived name set and no escape; its module set is `derived` when every use has one, else `unknown` and the pairs are
written (*, name): the platform must provide the name in every module.

Output gpa_names.tsv: module, site, api, operand, status, modules, npairs, ndlls, pairs, uses, proof, breaks.
proof = per use `call@va<-args`: name definition addresses | module definition addresses.  Gate `gpa` (gatefile.py): universe =
GetProcAddress rows with a dynamic operand, certified = closed ones.  Cross-check: rows with a constant operand are derived
too and compared with the platform's constant.  Check (--bridge): pairs the platform does not provide.
Exe-derived: container and fnv-nx-build only.
"""
import argparse
import os
import sys

import argflow
import gatefile
import iat
import memfx


def pair_key(dll):
    return iat.norm_dll(dll)


def derive_site(img, site):
    """-> dict(status, modules, pairs {(dll|'*', name): ...}, uses, proof [str], breaks [str])."""
    calls, open_ = argflow.uses_of_slot_ref(img, site)
    out = dict(uses=calls, pairs=set(), proof=[], breaks=[], mod_unknown=False, note='')
    for va, why in open_:
        out['breaks'].append('%x %s' % (va, why))
    if not calls and not open_:
        out['note'] = 'unreached'
        out['proof'].append('unreached@%x' % site)
    for u in calls:
        names, nb = argflow.name_set(img, u)
        mods, mb = argflow.module_names(img, u)
        for va, why in sorted(nb):
            out['breaks'].append('name@%x %x %s' % (u, va, why))
        if mb:
            out['mod_unknown'] = True
        dlls = sorted(pair_key(m) for m in mods) or ['*']
        if mb:
            dlls = sorted(set(dlls) | {'*'})
        for kind, nm, ch in names:
            for d in dlls:
                out['pairs'].add((d, nm))
        out['proof'].append('call@%x<-name:%s|mod:%s%s' % (
            u, ','.join(sorted({'%s:%s' % (k, '.'.join(str(c) if not isinstance(c, int) else '%x' % c for c in ch)) for k, n, ch in names}))[:160],
            ','.join('%s:%s' % (k, '.'.join('%x' % c if isinstance(c, int) else str(c) for c in v)) for k, v in sorted(mods.items()))[:160],
            '|modbreak:' + ';'.join('%x %s' % b for b in sorted(mb))[:200] if mb else ''))
    out['status'] = 'closed' if not out['breaks'] else 'open'
    return out


def handle_report(img, call_va):
    """Is hFile (argument 0) of the CreateFileMapping call at call_va the result of a CreateFileA/W call of the same function whose
    dwShareMode (argument 2) is the immediate FILE_SHARE_READ (1) on all paths?  The handle is read from memory through a pointer
    register, so the walk follows the store through that register; the call between the store and the load decides:
      proven   no call lies between store and load on any path (mem 'strict' closed);
      open     a call lies between them (its memory effect is not bounded by a rule): the breaking call is reported, and the
               origin set is given as conditional on A7 (the call does not write that memory)."""
    out = {}
    for mode in ('strict', 'rule', 'open'):
        ok, origins, brk = argflow.handle_origin(img, call_va, 0, mode)
        shares = []
        for c, va, chain in origins:
            r = argflow.derive_arg(img, va, 2)
            shares.append((c[2] if c[0] == 'import' else str(c), '%x' % va,
                           sorted(v for k, v, _ in r.sorted_leaves() if k == 'ord') if r.closed else 'open'))
        out[mode] = (ok, shares, brk)
    return out


def read_sites(text):
    rows = []
    for line in text.splitlines():
        f = line.split('\t')
        if len(f) < 4 or f[0] == 'module':
            continue
        rows.append((f[0], int(f[1], 16), f[2], f[3]))
    return rows


def import_stacks(bridge_text):
    out = {}
    for line in bridge_text.splitlines()[1:]:
        f = line.split('\t')
        if len(f) > 5:
            n = argflow.stack_bytes(f[3], f[5])
            if n is not None:
                out[(pair_key(f[0]), f[1])] = n
    return out


def platform_pairs(bridge_text, module_names):
    have, names = set(), set()
    for line in bridge_text.splitlines()[1:]:
        f = line.split('\t')
        if len(f) > 1:
            have.add((pair_key(f[0]), f[1]))
            names.add(f[1])
    return have, names, {pair_key(m) for m in module_names}


def run(rows, images):
    """images: {module name (lower): Image}.  -> [(row, derivation)]"""
    out = []
    for mod, site, api, operand in rows:
        if api != 'GetProcAddress':
            continue
        out.append(((mod, site, api, operand), derive_site(images[mod.lower()], site)))
    return out


def cross_check(results):
    """Constant rows: the platform's `dll name` / `name` operand must be one of the derived pairs (the platform reads the first
    constants of a slot reference (a token that names a derived dll, up to .dll, is the dll, the others are names; names of another slot reference of the same image count as `adjacent`); the derivation covers every use of it).  -> (match, wider, unreached, adjacent, [mismatch])
    match: exactly the platform's pair; wider: the platform's pair is among more derived pairs; unreached: the row lies in
    bytes no entry of the facts reaches; mismatch: the platform's pair is not derived, or the derivation is open."""
    match, wider, unr, adj, bad = 0, 0, 0, 0, []
    per_image = {}
    for (mod, site, api, operand), d in results:
        per_image.setdefault(mod, set()).update(nm for _, nm in d['pairs'])
    for (mod, site, api, operand), d in results:
        if operand == 'dynamic':
            continue
        if d['note'] == 'unreached':
            unr += 1
            continue
        t = operand.split()
        got = d['pairs']
        gnames, gdlls = {nm for _, nm in got}, {dl for dl, _ in got}
        wdll = {pair_key(x) for x in t if pair_key(x) in gdlls or ('*' in gdlls and x.lower().endswith('.dll'))}
        wnames = {x for x in t if pair_key(x) not in wdll}
        hit = wnames <= gnames and (not wdll or wdll <= gdlls or '*' in gdlls)
        if d['status'] != 'closed' or not (hit or wnames <= per_image[mod]):
            bad.append((mod, '%x' % site, operand, sorted(got)[:4], d['status']))
        elif not hit:
            adj += 1
        elif wnames == gnames:
            match += 1
        else:
            wider += 1
    return match, wider, unr, adj, bad


def check_platform(results, have, names, mods):
    """Pairs (closed or not) that neither the bridge nor a lifted module provides."""
    missing = set()
    for _, d in results:
        for dl, nm in d['pairs']:
            if dl == '*':
                if nm not in names:
                    missing.add(('*', nm))
            elif dl not in mods and (dl, nm) not in have:
                missing.add((dl, nm))
    return sorted(missing)


def tsv(results):
    lines = ['module\tsite\tapi\toperand\tstatus\tmodules\tnpairs\tndlls\tpairs\tuses\tproof\tbreaks']
    for (mod, site, api, operand), d in results:
        dl = {p[0] for p in d['pairs']}
        lines.append('\t'.join([mod, '0x%x' % site, api, operand, d['status'], 'unknown' if d['mod_unknown'] else 'derived',
                                str(len(d['pairs'])), str(len(dl)), ';'.join('%s!%s' % p for p in sorted(d['pairs'])),
                                ','.join('%x' % u for u in d['uses']), ' '.join(d['proof']) or '-',
                                ' / '.join(d['breaks']) or '-']))
    return '\n'.join(lines) + '\n'


def gate(results):
    dyn = [(r, d) for r, d in results if r[3] == 'dynamic']
    unc = ['%s@%x' % (r[0], r[1]) for r, d in dyn if d['status'] != 'closed']
    return gatefile.write('gpa', len(dyn), len(dyn) - len(unc), unc, 'GetProcAddress dynamic sites with a derived name set')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--sites', required=True)
    ap.add_argument('--image', action='append', required=True, help='name=pe_path,ranges_path')
    ap.add_argument('--handle', action='append', default=[], help='name:0xVA of a CreateFileMapping call')
    ap.add_argument('--bridge')
    ap.add_argument('--escape', action='append', default=[], help='import_escape.tsv of a platform backend (repeat; the worst letter counts)')
    ap.add_argument('--writes', help='tsv dll, name, letters (n = never written through) per import argument; default: all may be written')
    ap.add_argument('--no-memfx', action='store_true', help='derive without the may-write rule')
    ap.add_argument('--modules', help='modules.tsv (lifted modules provide their own names)')
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    stacks = {}
    if a.bridge:
        with open(a.bridge) as fh:
            stacks = import_stacks(fh.read())
    images = {}
    for spec in a.image:
        nm, rest = spec.split('=', 1)
        pe, rg = rest.split(',')
        with open(pe, 'rb') as fh, open(rg) as fr:
            images[nm.lower()] = argflow.Image(fh.read(), nm.lower(), argflow.read_ranges(fr.read()))
        images[nm.lower()].import_stack = stacks
    if not a.no_memfx:
        bt = open(a.bridge).read() if a.bridge else ''
        info = memfx.ImportInfo(bt, [open(p).read() for p in a.escape], open(a.writes).read() if a.writes else '')
        for im in images.values():
            memfx.Memfx(im, info)
    with open(a.sites) as fh:
        rows = read_sites(fh.read())
    results = run(rows, images)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, 'gpa_names.tsv'), 'w', newline='\n') as fh:
        fh.write(tsv(results))
    gate(results)
    ok, wide, unr, adj, bad = cross_check(results)
    print('cross-check constant rows: %d match, %d wider, %d platform-names-of-adjacent-site, %d unreached, %d mismatch %s' % (ok, wide, adj, unr, len(bad), bad[:6]))
    if a.bridge:
        mods = []
        if a.modules:
            with open(a.modules) as fh:
                mods = [l.split('\t')[0] for l in fh.read().splitlines() if l and l[0] != '#']
        with open(a.bridge) as fh:
            have, names, mset = platform_pairs(fh.read(), mods)
        miss = check_platform(results, have, names, mset)
        print('not provided by the platform: %d %s' % (len(miss), miss[:20]))
    for h in a.handle:
        nm, va = h.split(':')
        r = handle_report(images[nm.lower()], int(va, 16))
        print('handle %s %s: %s' % (nm, va, r))
    return 0


if __name__ == '__main__':
    sys.exit(main())
