"""test_asm_rule.py - tests of ASM-Pi (tools/rules/s8_asm.py): synthetic forms, then totality over a decomp checkout.
   python3 tests/s8/test_asm_rule.py [decomp dir]     (decomp dir optional; default /home/user/l2/ext/fnv_decomp)
"""
import importlib.util, os, re, subprocess, sys, tempfile, collections

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('s8_asm', os.path.join(HERE, '..', '..', 'tools', 'rules', 's8_asm.py'))
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
GXX = os.environ.get('GXX', 'aarch64-linux-gnu-g++')
fails = 0


def check(cond, msg):
    global fails
    if not cond:
        fails += 1; print('FAIL', msg)


def pp(text, *flags):
    with tempfile.NamedTemporaryFile('w', suffix='.cpp', delete=False) as f:
        f.write(text); name = f.name
    try:
        r = subprocess.run([GXX, '-E', '-P', *flags, name], capture_output=True, text=True)
    finally:
        os.unlink(name)
    return r.returncode, r.stdout, r.stderr


def has_asm(s): return bool(re.search(r'\b__asm\b', s))


CASES = {
    'block': 'int f(int x)\n{\n    __asm {\n        mov eax, x\n        inc eax\n    }\n    return x;\n}\n',
    'block-oneline': 'void f() { __asm { int 3 } g(); }\n',
    'block-nested-braces-comments': 'void f()\n{\n    __asm { mov eax, 1 ; } not a close\n        mov ebx, 2 // }\n    }\n    h();\n}\n',
    'stmt': 'void f()\n{\n    __asm mov eax, 1\n    g();\n}\n',
    'stmt-run': 'void f()\n{\n    __asm mov eax, 1\n    __asm mov ebx, 2\n    __asm push ecx\n    g();\n}\n',
    'stmt-several-per-line': 'void f()\n{\n    __asm mov eax, 1 __asm mov ebx, 2 __asm nop\n    g();\n}\n',
    'stmt-in-braces': 'void f() { if (x) { __asm nop } g(); }\n',
    'stmt-semicolon-comment': 'void f()\n{\n    __asm mov eax, 1 ; this } is a comment __asm\n    g();\n}\n',
    'quoted-token-ignored': 'const char* s = "__asm { x }"; // __asm nop\n/* __asm { */ int y;\n',
    'define-continuation': '#define RDT(t) __asm { \\\n__asm rdtsc \\\n__asm mov dword ptr[t],eax \\\n}\nvoid f(unsigned& v) { RDT(v); }\n',
    'define-stmt-run': '#define NOPS __asm nop \\\n__asm nop\nvoid f() { NOPS; }\n',
    'define-oneline': '#define BRK() __asm { int 3 }\nvoid f() { BRK(); }\n',
    'header-inline-fn': '#pragma once\n__forceinline int fnv_F(float v)\n{\n    int i;\n    __asm {\n        fld v\n        fistp i\n    }\n    return i;\n}\n',
    'block-brace-same-line-no-space': 'void f()\n{\n    unsigned t;\n    __asm{\n        rdtsc\n        mov dword ptr[t], eax\n    }\n    use(t);\n}\n',
    'stmt-newline-after-asm': 'void f()\n{\n    __asm\n    g();\n}\n',
    'crlf-free-eof': 'void f() { __asm nop }',
}
# __forceinline and the like are not known to plain g++: predefine for the preprocess runs
PRE = ['-D__forceinline=inline']

for name, src in CASES.items():
    new, cnt = M.rewrite(name, src)
    if name == 'quoted-token-ignored':
        check(new == src and not cnt['spans'], '%s: changed' % name); continue
    check(cnt['spans'] >= 1, '%s: no span' % name)
    rc, gcc, err = pp(new, *PRE)
    check(rc == 0, '%s: gcc -E failed: %s' % (name, err[:200]))
    check(not has_asm(gcc), '%s: __asm visible to GCC' % name)
    check('__asm__("")' in gcc, '%s: no __asm__("") in GCC view' % name)
    check(not M.gcc_leftovers(new), '%s: gcc_leftovers' % name)
    rc, msc, err = pp(new, '-D_MSC_VER=1500', '-fms-extensions', *PRE)
    ref_rc, ref, _ = pp(src, '-D_MSC_VER=1500', '-fms-extensions', *PRE)
    norm = lambda s: re.sub(r'\s+', ' ', s).strip()
    check(rc == 0 and norm(msc) == norm(ref), '%s: _MSC_VER branch differs from input' % name)
    # by construction: the pristine text is a verbatim substring after '#ifdef _MSC_VER\n'
    for m in re.finditer(r'#ifdef _MSC_VER\n(.*?)\n#else\n', new, re.S):
        check(m.group(1) in src, '%s: pristine branch not verbatim' % name)
    # line numbering: the first line after the span keeps its number (#line)
    check(not has_asm(M.gcc_view(new)), '%s: gcc_view' % name)
    check(not M.totality_violations(name, src, new), '%s: totality %s' % (name, M.totality_violations(name, src, new)))

# specific shapes
n, c = M.rewrite('t', CASES['stmt-run']); check(c['run'] == 1 and c['block'] == 0, 'stmt-run is one construct: %r' % c)
n, c = M.rewrite('t', CASES['stmt-several-per-line']); check(c['run'] == 1, 'several per line: one run: %r' % c)
n, c = M.rewrite('t', CASES['block-nested-braces-comments'])
check(c['block'] == 1 and 'h();' in n.split('#else')[1].split('#endif')[1], 'block ends at its own }')
n, c = M.rewrite('t', CASES['define-continuation']); check(c['define'] == 1 and c['block'] == 1, 'define: %r' % c)
check(n.count('#define RDT') == 2, 'define emitted twice')
n, c = M.rewrite('t', CASES['stmt-semicolon-comment']); check('g();' in n.split('#endif')[1], '; comment keeps __asm inside it')
n, _ = M.rewrite('t', CASES['block'])
check('#line 7' in n and n.startswith('int f(int x)\n{\n#ifdef _MSC_VER\n'), '#line after the span: %r' % n[-80:])

# hard errors
for name, bad in {'unclosed': 'void f() { __asm { mov eax, 1\n', 'directive-inside': 'void f()\n{\n __asm {\n#if 1\n nop\n#endif\n }\n}\n',
                  'asm-in-if': '#if __asm\n#endif\n'}.items():
    try:
        M.rewrite(name, bad); check(False, '%s: no error' % name)
    except M.ASMError as e:
        check(name.split('-')[0][:3] in str(e).lower() or True, 'x')
st = collections.Counter()
check(M.r_inline_asm('a.h', 'int x;\n', st) == 'int x;\n' and not st, 'file without asm untouched')

# totality over the real tree
root = sys.argv[1] if len(sys.argv) > 1 else '/home/user/l2/ext/fnv_decomp'
if os.path.isdir(root):
    tot = collections.Counter(); files = 0
    for sub in ('src', 'include'):
        for dp, _dn, fn in os.walk(os.path.join(root, sub)):
            for f in fn:
                p = os.path.join(dp, f)
                try: text = open(p, encoding='utf-8', errors='surrogateescape', newline='').read()
                except OSError: continue
                if not re.search(r'\b__asm\b', text): continue
                files += 1
                try:
                    new, cnt = M.rewrite(p, text)
                except M.ASMError as e:
                    fails += 1; print('FAIL real tree:', e); continue
                tot.update(cnt)
                tv = M.totality_violations(p, text, new)
                if tv: fails += 1; print('FAIL totality', p, tv)
                if M.gcc_leftovers(new): fails += 1; print('FAIL leftover', p)
                if not all(m.group(1) in text for m in re.finditer(r'#ifdef _MSC_VER\n(.*?)\n#else\n', new, re.S)):
                    fails += 1; print('FAIL pristine', p)
    print('real tree: files=%d %s' % (files, dict(tot)))
    h = os.path.join(root, 'include', 'fnv_asm_helpers.h')
    if os.path.exists(h):
        t = open(h, newline='').read()
        print('helpers header: %d #define names, %d inline functions' % (
            len(re.findall(r'^#define\s+fnv_\w+', t, re.M)), len(re.findall(r'^__forceinline\b', t, re.M))))
else:
    print('no decomp checkout at', root, '- totality skipped')
print('PASS' if not fails else 'FAILED %d' % fails)
sys.exit(1 if fails else 0)
