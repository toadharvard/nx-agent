#!/usr/bin/env python3
"""End-to-end test of `fnvlink.py resolve --override` on three hand-written units (no decomp content).

    python3 tools/link/test_override.py     # needs aarch64-linux-gnu-{g++,ld,objcopy,objdump,nm}, llvm-undname

Unit a defines f (0x00401000), the key virtual K::v (0x00401100, so a's object holds K's vtable) and a caller of f in
the same object; unit b calls f through another declaration (g_alias); unit c names f's address in units.json but its
object no longer references it (as when GCC inlines). The override table replaces f and K::v with native functions.
Checks: shadowed.tsv lists both and names c as possibly inlined; after apply + trial link, the address symbol, the
decomp's own name of f (so a's internal call) and K's vtable slot all point at the overrides; the address map entry
names the override. A variadic replacement (unit d's fixed-argument logger 0x00401500, referenced variadically and
with a double from unit e) carries its own MSVC signature in the table's 4th column: both references bind to it
directly, no bridge built against the decomp definition.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FNVLINK = os.path.join(HERE, 'fnvlink.py')
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')

SRC = {
    'src/a.cpp': 'int f(int x) { return x + 1; }\n'
                 'struct K { virtual int v(); };\nint K::v() { return 1; }\n'
                 'int user_a(int x) { return f(x) * 2; }\n',
    'src/b.cpp': 'int g_alias(int);\nint user_b(int x) { return g_alias(x); }\n',
    'src/c.cpp': 'int user_c() { return 3; }\n',
    'src/d.cpp': 'void FUN_00401500(const char *t, const char *c) { (void)t; (void)c; }\n',
    'src/e.cpp': 'int FUN_00401500(const char *, ...);\nvoid FUN_00401500(const char *, double);\n'
                 'void log_user(double v) { FUN_00401500("a %d %d", 1, 2); FUN_00401500("b", v); }\n',
    'native/over.cpp': 'extern "C" int nx_f(int x) { return x + 100; }\nextern "C" int nx_v(void *) { return 7; }\n'
                       'extern "C" int nx_log(const char *, ...) { return 0; }\n',
}
UNITS = {'units': [
    {'source': 'src/a.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        {'address': '0x00401000', 'symbol': '?f@@YAHH@Z', 'size': 8},
        {'address': '0x00401100', 'symbol': '?v@K@@UAEHXZ', 'size': 8},
        {'address': '0x00401200', 'symbol': '?user_a@@YAHH@Z', 'size': 8}],
     'symbols': {'?f@@YAHH@Z': '0x00401000'}},
    {'source': 'src/b.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        {'address': '0x00401300', 'symbol': '?user_b@@YAHH@Z', 'size': 8}],
     'symbols': {'?g_alias@@YAHH@Z': '0x00401000'}},
    {'source': 'src/c.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        {'address': '0x00401400', 'symbol': '?user_c@@YAHXZ', 'size': 8}],
     'symbols': {'?f@@YAHH@Z': '0x00401000'}},
    {'source': 'src/d.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        {'address': '0x00401500', 'symbol': '?FUN_00401500@@YAXPBD0@Z', 'size': 8}], 'symbols': {}},
    {'source': 'src/e.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        {'address': '0x00401600', 'symbol': '?log_user@@YAXN@Z', 'size': 8}],
     'symbols': {'?FUN_00401500@@YAHPBDZZ': '0x00401500', '?FUN_00401500@@YAXPBDN@Z': '0x00401500'}},
]}

ok = True


def check(cond, what):
    global ok
    if not cond:
        ok = False
        print('FAIL', what)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit('FAILED: %s\n%s%s' % (' '.join(cmd), r.stdout, r.stderr))
    return r.stdout


def main():
    with tempfile.TemporaryDirectory() as t:
        objs = {}
        for rel, text in SRC.items():
            p = os.path.join(t, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, 'w').write(text)
            objs[rel] = p[:-4] + '.o'
            run([CXX, '-mabi=ilp32', '-O1', '-fno-inline', '-c', p, '-o', objs[rel]])
        json.dump(UNITS, open(os.path.join(t, 'units.json'), 'w'))
        with open(os.path.join(t, 'objects.tsv'), 'w') as f:
            for rel in ('src/a.cpp', 'src/b.cpp', 'src/c.cpp', 'src/d.cpp', 'src/e.cpp'):
                f.write('%s\t%s\tok\n' % (rel, objs[rel]))
        with open(os.path.join(t, 'override.tsv'), 'w') as f:
            f.write('# address\tsymbol\tobject\n00401000\tnx_f\t%s\n00401100\tnx_v\t%s\n'
                    '00401500\tnx_log\t%s\t?FUN_00401500@@YAHPBDZZ\n'
                    % (objs['native/over.cpp'], objs['native/over.cpp'], objs['native/over.cpp']))
        out, fixed = os.path.join(t, 'out'), os.path.join(t, 'fixed')
        run([sys.executable, FNVLINK, 'resolve', os.path.join(t, 'units.json'), os.path.join(t, 'objects.tsv'), out,
             '--override', os.path.join(t, 'override.tsv'), '--crt-symbols', 'none', '--imports', 'none'])
        rows = [l.rstrip('\n').split('\t') for l in open(os.path.join(out, 'shadowed.tsv')) if not l.startswith('#')]
        byaddr = {r[0]: r for r in rows}
        check(set(byaddr) == {'00401000', '00401100', '00401500'}, 'shadowed rows: %s' % sorted(byaddr))
        refs = {(r[0], r[1]): r for r in (l.rstrip('\n').split('\t') for l in open(os.path.join(out, 'refs.tsv'))
                                          if not l.startswith('#'))}
        for name in ('_Z12FUN_00401500PKcz', '_Z12FUN_00401500PKcd'):
            r = refs.get(('src/e.cpp', name), [''] * 6)
            check(r[5] == '__fnv_00401500', 'variadic override, reference %s bound directly: %s' % (name, r))
        check('src/c.cpp' in byaddr.get('00401000', [''] * 9)[7].split(), 'c listed as possibly inlined: %s' % rows)
        amap = [l.split('\t') for l in open(os.path.join(out, 'addrmap.tsv')) if not l.startswith('#')]
        check(any(r[0] == '00401000' and r[3] == 'nx_f' for r in amap), 'address map names the override')
        run([sys.executable, FNVLINK, 'apply', out, fixed])
        run([sys.executable, FNVLINK, 'check', out, fixed])
        elf = os.path.join(out, 'trial.elf')
        sym = {}
        for line in run(['aarch64-linux-gnu-nm', elf]).splitlines():
            f = line.split()
            if len(f) == 3:
                sym[f[2]] = int(f[0], 16)
        nx_f, nx_v = sym.get('nx_f'), sym.get('nx_v')
        check(nx_f is not None and sym.get('__fnv_00401000') == nx_f, 'address symbol -> override')
        check(sym.get('_Z1fi') == nx_f, "decomp name of f -> override (a's own call)")
        check(sym.get('_ZN1K1vEv') == nx_v, 'K::v -> override')
        check(sym.get('nx_log') is not None and sym.get('__fnv_00401500') == sym.get('nx_log'), 'logger -> nx_log')
        ztv = sym.get('_ZTV1K')
        dump = run(['aarch64-linux-gnu-objdump', '-s', '--start-address=0x%x' % (ztv + 8),
                    '--stop-address=0x%x' % (ztv + 12), elf]) if ztv else ''
        words = [l.split()[1] for l in dump.splitlines() if l.startswith(' ') and len(l.split()) > 1]
        slot = int.from_bytes(bytes.fromhex(words[0]), 'little') if words else None
        check(slot == nx_v, 'vtable slot of K::v -> override (%s vs %s)' % (slot, nx_v))
        # a's internal call to f and b's call through g_alias both land on nx_f
        dis = run(['aarch64-linux-gnu-objdump', '-d', '--no-show-raw-insn', elf])
        calls = [l for l in dis.splitlines() if '\tbl\t' in l or '\tb\t' in l]
        check(sum(('\t%x <' % nx_f) in l for l in calls) >= 2, 'callers of f branch to nx_f: %s' % calls)
    print('ok' if ok else 'FAILED')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
