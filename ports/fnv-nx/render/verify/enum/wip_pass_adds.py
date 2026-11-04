# V6n-render WIP (exploration, not the final enumerator): list every BSShaderProperty::ShaderPassList::Add
# (FUN_00ba9ee0) call site in the decomp with its enclosing @fnv function and the pass-id argument (literal ->
# header enum name, else EXPR). Output goes to stdout; it is decomp-derived, keep it in scratch, never commit it.
#   python3 -I wip_pass_adds.py <fnv_decomp>/src > <scratch>/adds.txt
# Known noise: the bare `Add(` alternative also matches unrelated Add calls (ids >= 0x10000000).
import re, sys, os, collections
root = sys.argv[1]
enum = {}
for l in open(os.path.join(root, 'bsshader/bsshader_shader_layout.h')):
    m = re.match(r'\s*BSShaderManager__eBSSMPassEnum_BSSM_(\w+) = (\d+),?', l)
    if m and not re.search(r'FIRST|LAST|START|END|COUNT', m.group(1)):
        enum.setdefault(int(m.group(2)), m.group(1))
call = re.compile(r'(?:FUN_00ba9ee0|ShaderPassList::Add|\bAdd)\s*\(\s*([^,]+),\s*([^,]+),')
fnre = re.compile(r'^// @fnv (0x[0-9a-fA-F]+)')
defre = re.compile(r'^[\w:<>\*&\s]+?([\w:~]+)\s*\(')
for dp, dn, fn in os.walk(root):
    for f in sorted(fn):
        if not f.endswith('.cpp'):
            continue
        p = os.path.join(dp, f)
        L = open(p, errors='replace').read().split('\n')
        cur, name = None, None
        for i, l in enumerate(L):
            m = fnre.match(l)
            if m:
                cur = m.group(1)
                for j in range(i + 1, min(i + 4, len(L))):
                    d = defre.match(L[j])
                    if d:
                        name = d.group(1)
                        break
            if 'void' in l and 'FUN_00ba9ee0(' in l:
                continue
            for m in call.finditer(l):
                a = m.group(2).strip()
                try:
                    v = int(a, 0)
                    print("%-40s %s %-40s %4d %s" % (os.path.relpath(p, root)[:40], cur, (name or '')[:40], v, enum.get(v, '?')))
                except ValueError:
                    print("%-40s %s %-40s EXPR %s" % (os.path.relpath(p, root)[:40], cur, (name or '')[:40], a))
