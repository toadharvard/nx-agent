#!/usr/bin/env python3
"""Census over ports/fnv-nx/lift: (A) direct toolchain invocations, (B) symbols a test defines that a product unit also defines.
  census_doubles.py sites  [LIFT_DIR]            -> Part A table (markdown), decided from the Python AST + a literal scan of .sh/.json
  census_doubles.py syms   OBJ...                -> defined GLOBAL/WEAK symbols of objects/sources, one per line
Pure function of the tree: no wall clock, no environment."""
import ast, os, re, sys, json

TOOL = re.compile(r'^(?:[\w.+-]*-)?(gcc|g\+\+|cc|c\+\+|clang(?:\+\+)?(?:-\d+)?|as|ld|ld\.lld|objcopy|objdump|nm|ar|strip|ranlib|readelf|llvm-\w+)$')
SKIP = {'.git', '__pycache__'}

def basename(s): return os.path.basename(s)

def consts(tree):
    """name -> first element constant of any list/str assigned to it (file-wide, last assignment wins per scope-free map)."""
    m = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            v = first_elt(n.value, m)
            if v is not None:
                for t in n.targets:
                    if isinstance(t, ast.Name): m[t.id] = v
    return m

def first_elt(n, m):
    if isinstance(n, ast.Constant) and isinstance(n.value, str): return n.value
    if isinstance(n, (ast.List, ast.Tuple)):
        return first_elt(n.elts[0], m) if n.elts else None
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add): return first_elt(n.left, m)
    if isinstance(n, ast.Name): return m.get(n.id)
    return None

def argv_tail(n):
    """every string constant reachable in the argument expression (source files, flags)."""
    out = []
    for x in ast.walk(n):
        if isinstance(x, ast.Constant) and isinstance(x.value, str): out.append(x.value)
    return out

SUB = {'run', 'call', 'check_call', 'check_output', 'Popen'}

def scan_py(path, rel):
    src = open(path).read()
    try: tree = ast.parse(src)
    except SyntaxError as e: return [(rel, e.lineno, '<syntax-error>', '', '')], []
    m = consts(tree); rows = []; other = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call): continue
        f = n.func; kind = None
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            if f.value.id == 'subprocess' and f.attr in SUB: kind = 'subprocess.' + f.attr
            elif f.value.id == 'os' and f.attr in ('system', 'popen'): kind = 'os.' + f.attr
        if not kind or not n.args: continue
        a = n.args[0]
        shell = any(k.arg == 'shell' for k in n.keywords)
        h = first_elt(a, m)
        if h is not None and (kind.startswith('os.') or shell): h = h.split()[0] if h.split() else h
        if h is None:
            h = '<dynamic>'
        t = basename(h)
        row = (rel, n.lineno, t, kind, ' '.join(x for x in argv_tail(a) if not x.startswith('-') or x.endswith(('.c', '.cpp', '.S')))[:160])
        (rows if (TOOL.match(t) or h == '<dynamic>') else other).append(row)
    return rows, other

def scan_text(path, rel):
    rows = []
    for i, l in enumerate(open(path, errors='replace'), 1):
        s = l.strip()
        if path.endswith('.sh') and s.startswith('#'): continue
        for w in re.findall(r'(?<![\w/.-])((?:[\w.+-]+-)?(?:gcc|g\+\+|clang(?:\+\+)?|objcopy|objdump|ld|as|nm)(?:-\d+)?)(?=\s|$|")', s):
            if TOOL.match(w) and (path.endswith('.sh') or re.search(r'"(cc|cxx|compiler|toolchain|argv|cmd)"', s)):
                rows.append((rel, i, w, 'literal', s[:160]))
    return rows

def sites(root):
    rows, other, lit = [], [], []
    for d, ds, fs in os.walk(root):
        ds.sort()
        if any(x in SKIP for x in os.path.relpath(d, root).split(os.sep)): continue          # a directory of SKIP and everything below it
        for f in sorted(fs):
            p = os.path.join(d, f); rel = os.path.relpath(p, root)
            if f.endswith('.py'):
                r, o = scan_py(p, rel); rows += r; other += o
            elif f.endswith(('.sh', '.json')): lit += scan_text(p, rel)
    return rows, other, lit

DEF = re.compile(r'^[0-9a-fA-F]*\s+([TDBRVWvwCS])\s+(\S+)$')
def syms(objs):
    import subprocess
    out = set()
    for o in objs:
        for l in subprocess.run(['llvm-nm', '--defined-only', '--extern-only', o], capture_output=True, text=True).stdout.splitlines():
            m = DEF.match(l.strip())
            if m: out.add(m[2])
    return sorted(out)


# ---- Part B: symbols defined by the sources a test writes vs symbols defined by the product units (lift/rt/*.c) ----
import subprocess, tempfile, shutil
CXX_UNITS = {'seh.c', 'enter.c', 'unhandled.c', 'msvcrt_eh.c', 'msvcrt_fp.c', 'msvcrt_ci.c'}   # the units the tests compile with -x c++ (test/msvcrttest.py, citest.py, imptest.py)
ASMDEF = re.compile(r'^\s*([A-Za-z_.$][\w.$]*)\s*:', re.M)
ASMGLB = re.compile(r'^\s*\.(?:globl|global|weak)\s+([\w.$]+)', re.M)
BOUNDARY = frozenset('memcpy memset memmove memcmp strlen strcmp malloc calloc realloc free printf puts putchar write read open close exit abort _exit mmap munmap clock_gettime gettimeofday time sleep usleep nanosleep sched_yield pthread_create pthread_join pthread_mutex_lock pthread_mutex_unlock fnvl_host_ticks_ns __errno_location errno getenv qsort'.split())

def sniff(src):
    if re.search(r'^\s*\.(globl|global|text|section|weak|balign|long)\b', src, re.M): return 'asm'
    return 'c'

def defined(src, kind, inc, tmp, tag):
    """global/weak symbols that `src` defines. asm: .globl/.global/.weak names that have a label. C/C++: clang's AST (host target, errors tolerated: they sit in
    function bodies): every FunctionDecl with a body and every VarDecl that is a definition, with external linkage (not static, not in an anonymous namespace,
    not a C++ const), by its mangledName. Inline functions count (they are emitted weak)."""
    if kind == 'asm':
        return sorted(set(ASMGLB.findall(src)) & set(ASMDEF.findall(src))), []
    f = os.path.join(tmp, tag + ('.cpp' if kind == 'cpp' else '.c')); open(f, 'w').write(src)
    cmd = ['clang', '-w', '-fsyntax-only', '-ferror-limit=0', '-I', inc, '-Xclang', '-ast-dump=json', '-x', 'c++' if kind == 'cpp' else 'c', f]
    if kind == 'cpp': cmd[1:1] = ['-std=gnu++17', '-fms-extensions']
    r = subprocess.run(cmd, capture_output=True, text=True)
    try: tu = json.loads(r.stdout)
    except ValueError: return None, ['no AST: ' + ' '.join(r.stderr.strip().splitlines()[:1])]
    out = set()
    def walk(nodes, anon):
        for n in nodes:
            k = n.get('kind')
            if k == 'NamespaceDecl': walk(n.get('inner', []), anon or not n.get('name')); continue
            if k == 'LinkageSpecDecl': walk(n.get('inner', []), anon); continue
            if anon or n.get('storageClass') == 'static' or n.get('isImplicit'): continue
            loc = n.get('loc', {}); incl = loc.get('includedFrom') or loc.get('spellingLoc', {}).get('includedFrom') or loc.get('expansionLoc', {}).get('includedFrom')
            if incl: continue                                     # defined in a header: not this source's own definition
            nm = n.get('mangledName') or n.get('name')
            if k == 'FunctionDecl' and any(i.get('kind') == 'CompoundStmt' for i in n.get('inner', [])): out.add(nm)
            elif k == 'VarDecl' and n.get('storageClass') != 'extern' and not (kind == 'cpp' and 'const' in n.get('type', {}).get('qualType', '').split('(')[0] and not n.get('inline')): out.add(nm)
    walk(tu.get('inner', []), False)
    return sorted(x for x in out if x), []

class NS:
    """stands for the lifter module (L.PRELUDE = the inlined rt/fnvl.h) and the instest module (T.STUBX): the includes make the declarations visible to the AST pass."""
    PRELUDE = '#include "fnvl.h"\n'
    STUBX = 'extern "C" void fnvl_call(struct cpu*, u32 target);\n'

def evalstr(node, env):
    """evaluate a string-valued expression built from constants, names bound to constants/lists, +, %, join, comprehension; None if anything else occurs."""
    try:
        code = compile(ast.Expression(node), '<e>', 'eval')
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        scope = {k: env[k] for k in names if k in env}
        for k in names:
            if k in ('L', 'T') and k not in scope: scope[k] = NS
        v = eval(code, dict(scope, __builtins__={'str': str, 'len': len}))
        return v if isinstance(v, (str, list)) else None
    except Exception: return None

def test_sources(path):
    """[(label, text, how)] : the module-level UPPER string constants and every constant-evaluable .write() argument of one test program."""
    tree = ast.parse(open(path).read()); env = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            v = evalstr(n.value, env)
            if v is not None: env[n.targets[0].id] = v
    out = []; seen = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'write' and n.args:
            v = evalstr(n.args[0], env)
            if not isinstance(v, str): v = None
            if v is None:
                names = [x.id for x in ast.walk(n.args[0]) if isinstance(x, ast.Name)]
                nm = [k for k in names if isinstance(env.get(k), str) and len(env[k]) > 40]
                for k in nm:
                    if env[k] not in seen: seen.add(env[k]); out.append((k, env[k], 'name@%d' % n.lineno))
                continue
            if v not in seen and len(v) > 20: seen.add(v); out.append(('write@%d' % n.lineno, v, 'literal'))
    for k, v in env.items():
        if isinstance(v, str) and len(v) > 80 and v not in seen and (sniff(v) == 'asm' or re.search(r'\b(int|void|unsigned|static|extern)\b[^;{]*\(', v)): seen.add(v); out.append((k, v, 'const'))
    return out

def declared(rt, tmp):
    """function/variable declarations without a definition that the product's own headers and units (lift/rt) make: the names the product expects from its boundary."""
    out = {'fnvl_call'}        # the lifter's CALLX target (test/instest.py STUBX): emitted by the lifter, supplied by the runtime owner
    hdr = os.path.join(tmp, 'allhdr.cpp')
    open(hdr, 'w').write('#include "fnvl.h"\n' + ''.join('#include "%s"\n' % h for h in sorted(os.listdir(rt)) if h.endswith('.h') and h != 'fnvl.h'))
    for f, fn, cpp in [(f, os.path.join(rt, f), f in CXX_UNITS) for f in sorted(os.listdir(rt)) if f.endswith('.c')] + [('allhdr', hdr, True)]:
        cmd = ['clang', '-w', '-fsyntax-only', '-ferror-limit=0', '-I', rt, '-Xclang', '-ast-dump=json', '-x', 'c++' if cpp else 'c', fn]
        if cpp: cmd[1:1] = ['-std=gnu++17', '-fms-extensions']
        try: tu = json.loads(subprocess.run(cmd, capture_output=True, text=True).stdout)
        except ValueError: continue
        cur = [None]
        def walk(nodes):
            for n in nodes:
                loc = n.get('loc', {}); sp = loc.get('spellingLoc', loc)
                if sp.get('file'): cur[0] = sp['file']
                if n.get('kind') in ('LinkageSpecDecl', 'NamespaceDecl'): walk(n.get('inner', [])); continue
                if cur[0] and os.path.abspath(cur[0]).startswith(os.path.abspath(rt)) and n.get('kind') in ('FunctionDecl', 'VarDecl') and not n.get('isImplicit'):
                    if n['kind'] == 'FunctionDecl' and not any(i.get('kind') == 'CompoundStmt' for i in n.get('inner', [])) or n['kind'] == 'VarDecl' and n.get('storageClass') == 'extern':
                        out.add(n.get('mangledName') or n['name'])
        walk(tu.get('inner', []))
    return out

def doubles(root):
    tmp = tempfile.mkdtemp(); rt = os.path.join(root, 'rt'); prod = {}
    for f in sorted(os.listdir(rt)):
        if f.endswith('.c'):
            prod[f] = defined(open(os.path.join(rt, f)).read(), 'cpp' if f in CXX_UNITS else 'c', rt, tmp, 'p_' + f)
    productsyms = set().union(*[set(s) for s, e in prod.values() if s])
    expects = declared(rt, tmp) - productsyms          # declared by the product, defined by no product unit: the boundary (libc, OS, runtime-owner contract)
    res = []
    progs = [os.path.join(d, f) for d in ('test', 'bench', 'proof/tests') for f in sorted(os.listdir(os.path.join(root, d))) if f.endswith('.py') and f != '__init__.py']
    for p in progs:
        rel = os.path.relpath(p, root); rows = []
        for i, (lab, text, how) in enumerate(test_sources(p)):
            if re.match(r'^[A-Za-z_]\w*$', text.strip()) or 'shapeprove' in text[:40]: continue
            kind = sniff(text)
            if kind == 'c' and re.search(r'extern "C"|#include "fnvl|\bnamespace\b|\bclass\b', text): kind = 'cpp'     # the units the tests compile with -x c++ / g++
            s, err = defined(text, kind, rt, tmp, 't%d' % i)
            rows.append((lab, kind, s, err))
        if rel == 'bench/chain.py':
            rows.append(('bench/harness.S', 'asm', *defined(open(os.path.join(root, 'bench', 'harness.S')).read(), 'asm', rt, tmp, 'h')))
        res.append((rel, rows))
    shutil.rmtree(tmp)
    out = []
    for rel, rows in res:
        d = sorted({x for lab, k, s, e in rows for x in (s or [])})
        inter = [x for x in d if x in productsyms]
        bnd = [x for x in d if x not in productsyms and (x in expects or x in BOUNDARY)]
        own = [x for x in d if x not in inter and x not in bnd]
        out.append(dict(file=rel, defined=d, product=inter, boundary=bnd, own=own, errors=[e for lab, k, s, e in rows if e]))
    return dict(product={k: v[0] for k, v in prod.items()}, expects=sorted(expects), files=out)

if __name__ == '__main__':
    cmd = sys.argv[1]
    if cmd == 'sites':
        root = sys.argv[2] if len(sys.argv) > 2 else '.'
        rows, other, lit = sites(root)
        for r in sorted(rows): print('| %s:%d | %s | %s | %s |' % (r[0], r[1], r[2], r[3], r[4]))
        print('LITERAL'); [print(r) for r in sorted(lit)]
        print('NONTOOL', len(other))
        for r in sorted(other): print(r[:4])
    elif cmd == 'doubles':
        root = sys.argv[2] if len(sys.argv) > 2 else '.'
        print(json.dumps(doubles(root), indent=1, sort_keys=True))
    elif cmd == 'report':
        root = sys.argv[2] if len(sys.argv) > 2 else '.'
        D = doubles(root); F = {f['file']: f for f in D['files']}
        INHERIT = {'test/mmxtest.py': 'test/instest.py', 'test/x87dyn.py': 'test/instest.py'}      # they call instest.runtime_objects(): the same rt.S + hostticks.c
        print('| file | defined by test AND by a product unit | boundary doubles | test-own (not doubles) |\n|---|---|---|---|')
        tot = set(); ntot = 0; nf = 0; nb = set(); npd = set()
        for k, f in F.items():
            if not f['defined'] and k not in INHERIT: continue
            g = F[INHERIT[k]] if k in INHERIT else f
            pr, bd, ow = g['product'], g['boundary'], g['own'] if k not in INHERIT else []
            d = bool(pr or bd); nf += d; npd |= set(pr); nb |= set(bd); tot |= set(pr) | set(bd)
            ntot += len(pr) + len(bd)
            print('| %s%s | %s | %s | %s |' % (k, ' (inherits %s)' % INHERIT[k] if k in INHERIT else '', ', '.join(pr) or '-', ', '.join(bd) or '-', ', '.join(ow) or '-'))
        print('\nfiles with doubles %d; distinct product-collision symbols %d; distinct boundary symbols %d; (file,symbol) pairs %d; distinct symbols %d' % (nf, len(npd), len(nb), ntot, len(tot)))
        print('\nproduct units (rt/*.c) define: ' + '; '.join('%s: %d' % (k, len(v or [])) for k, v in D['product'].items()))
        print('product expects from its boundary (declared, defined by no unit): ' + ', '.join(D['expects']))
    elif cmd == 'syms':
        print('\n'.join(syms(sys.argv[2:])))
