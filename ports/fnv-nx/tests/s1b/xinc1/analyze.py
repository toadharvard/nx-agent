"""analyze.py ENUM_TSV OUT_TSV [SITES_DIR]: per open caller row, the caller's convention, the instruction at the
reference and where ecx comes from there (the caller's incoming ecx on all paths / some / none); writes a context
file per caller (source, x86 code, callee definition). Output is decomp-derived (scratch only)."""
import collections, json, os, re, subprocess, sys
D = os.environ['DECOMP'] + '/'
S11 = os.environ['S11']
PORT = os.environ['PORT']
sys.path.insert(0, PORT + '/tools/verify/v1_link')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coff_x86
import ecxflow

units = json.load(open(D + 'build/units.json'))['units']
fdef = collections.defaultdict(list)
for i, u in enumerate(units):
    for f in u['functions']:
        fdef[int(f['address'], 16)].append((i, u['source'], f['symbol']))


def kind(sym):
    if sym.startswith('??0'): return 'ctor'
    if sym.startswith('??1'): return 'dtor'
    if sym.startswith(('??_G', '??_E')): return 'deldtor'
    m = re.match(r'^\?[^@]+@(?:[^@]*@)*@([A-Z])', sym)
    if sym.startswith('?') and '@@' in sym:
        rest = sym.split('@@', 1)[1]
        if rest.startswith('Y'):
            return {'A': 'cdecl', 'G': 'stdcall', 'I': 'fastcall', 'E': 'thiscall?'}.get(rest[1:2], 'free?')
        if rest[:1] in 'QUAEIMRBJ' and rest[1:2] in 'AB' and rest[2:3] == 'E':
            return 'member' + ('-virt' if rest[:1] in 'UEM' else '')
        if rest[:1] in 'SKC':
            return 'static-' + {'A': 'cdecl', 'G': 'stdcall', 'I': 'fastcall'}.get(rest[1:2], '?')
        return 'other:' + rest[:3]
    if re.match(r'^@\w+@\d+$', sym): return 'fastcall-c'
    if re.match(r'^_\w+@\d+$', sym): return 'stdcall-c'
    return 'cdecl-c'


def fn_text(path, faddr, limit=200):
    try:
        text = open(D + path, errors='replace').read()
    except OSError:
        return None
    m = re.search(r'//\s*@fnv\s+0x%08x\b' % faddr, text)
    if not m:
        return None
    nm = re.search(r'//\s*@fnv\s+0x[0-9a-fA-F]+', text[m.end():])
    end = m.end() + nm.start() if nm else len(text)
    seg = text[m.start():end].split('\n')
    line = text.count('\n', 0, m.start()) + 1
    return '%s:%d\n' % (path, line) + '\n'.join(seg[:limit])


rows = [l.rstrip('\n').split('\t') for l in open(sys.argv[1]) if not l.startswith('#')]
out = open(sys.argv[2], 'w')
sdir = sys.argv[3] if len(sys.argv) > 3 else None
if sdir: os.makedirs(sdir, exist_ok=True)
out.write('#src\tunit\tcallee\tclasses\tcaller\tckind\tinsn\tecx\tecx_def\tdkind\tv1\tref\tdef\tcaller_sym\n')
objs = {}
for r in rows:
    src, unit, callee, classes, caller, csym, offs, status, v1, v1ecx, refms, defms, ref, dfn = r
    if status != 'open':
        continue
    i = int(unit)
    if i not in objs:
        objs[i] = coff_x86.Coff('%s/obj/pristine/u%d.obj' % (S11, i))
    co = objs[i]
    code, rel = co.func(csym)
    insns, st = ecxflow.flow(code)
    res = []
    for off in map(int, offs.split(',')):
        ins, must, may = ecxflow.at_reloc(insns, st, off)
        if ins is None:
            res.append(('?', '?', '')); continue
        e = 'this' if must and 'ecx' in must else ('some' if may and 'ecx' in may else 'no')
        # last linear writer of ecx before the reference
        w = ''
        for a in sorted(x for x in insns if x < ins.address):
            if 'ecx' in ecxflow.written(insns[a]) or insns[a].mnemonic == 'call':
                w = '%x:%s %s' % (a, insns[a].mnemonic, insns[a].op_str)
        res.append((ins.mnemonic, e, w))
    ck = kind(csym)
    dk = kind(defms) if defms else '?'
    out.write('\t'.join([src, unit, callee, classes, caller, ck, ','.join(x[0] for x in res), ','.join(x[1] for x in res),
                         ' | '.join(x[2] for x in res), dk, v1 + ':' + v1ecx, ref, dfn, csym]) + '\n')
    if sdir:
        with open('%s/%s_%s.txt' % (sdir, caller[2:], callee), 'w') as f:
            f.write('PAIR %s callee %s caller %s %s\nREF %s\nDEF %s\nCLASSES %s V1 %s %s\nCKIND %s ECX %s\n' % (
                src, callee, caller, csym, ref, dfn, classes, v1, v1ecx, ck, res))
            t = fn_text(src, int(caller, 16))
            f.write((t or '(caller text not found)') + '\n')
            rr = subprocess.run(['objdump', '-d', '-r', '-M', 'intel', '--no-show-raw-insn', '--disassemble=' + csym,
                                 '%s/obj/pristine/u%d.obj' % (S11, i)], capture_output=True, text=True)
            f.write('\n'.join(l for l in rr.stdout.splitlines() if re.match(r'\s+[0-9a-f]+:', l)) + '\n')
            f.write('=== callee 0x%s definitions: %s\n' % (callee, [(x[1], x[2]) for x in fdef.get(int(callee, 16), [])][:3]))
            for (di, dsrc, dsym) in fdef.get(int(callee, 16), [])[:1]:
                t = fn_text(dsrc, int(callee, 16), 25)
                f.write((t or '') + '\n')
out.close()
