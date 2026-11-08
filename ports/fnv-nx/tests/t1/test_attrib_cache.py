#!/usr/bin/env python3
"""T1: tools/difffuzz/attrib.py rules with FNVNX_ATTRIB_CACHE: the same JSON as without the cache, cold and warm; a
unit whose prepared text changed is attributed again, the others come from the cache (synthetic sources only)."""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DF = os.path.join(HERE, '..', '..', 'tools', 'difffuzz')
fails = 0


def ok(name, cond):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name)
    fails += 0 if cond else 1


def main():
    with tempfile.TemporaryDirectory() as t:
        dec, tree, port = (os.path.join(t, d) for d in ('decomp', 'tree', 'port'))
        os.makedirs(os.path.join(dec, 'build')); os.makedirs(os.path.join(port, 'patches'))
        units = []
        for i in range(4):
            src = 'src/u%d.cpp' % i
            units.append({'source': src, 'functions': []})
            body = ''.join('// @fnv 0x%08x\nint f%d_%d(int a) {\n  return a + %d;\n}\n' % (0x401000 + 0x100 * i + k, i, k, k)
                           for k in range(3))
            for root in (dec, tree):
                os.makedirs(os.path.join(root, 'src'), exist_ok=True)
            open(os.path.join(dec, src), 'w').write(body)
            prep = body.replace('return a + 1;', 'return fnvnx_lvalue(a) + 1;') if i % 2 else body
            if i == 3:
                prep = prep.replace('// @fnv 0x%08x\n' % (0x401000 + 0x300 + 2), '')   # a function gone
            open(os.path.join(tree, src), 'w', newline='').write(prep.replace('\n', '\r\n') if i == 1 else prep)
        open(os.path.join(dec, 'src', 'u2.cpp'), 'ab').write(b'// \xff\xfe not utf-8\n')
        json.dump({'units': units}, open(os.path.join(dec, 'build', 'units.json'), 'w'))
        cache = os.path.join(t, 'c', 'rules.marshal')

        def rules(out, use_cache):
            env = dict(os.environ, FNVNX_DECOMP=dec, FNVNX_TREE=tree)
            env.pop('FNVNX_ATTRIB_CACHE', None)
            if use_cache:
                env['FNVNX_ATTRIB_CACHE'] = cache
            r = subprocess.run([sys.executable, 'attrib.py', 'rules', out], cwd=DF, capture_output=True, text=True,
                               env=env)
            if r.returncode:
                raise SystemExit(r.stderr)
            return open(out, 'rb').read(), r.stdout
        plain, _ = rules(os.path.join(t, 'a.json'), False)
        cold, cs = rules(os.path.join(t, 'b.json'), True)
        warm, ws = rules(os.path.join(t, 'c.json'), True)
        ok('cold and warm cache give the uncached JSON byte for byte', plain == cold == warm)
        ok('warm: every unit from the cache', 'attrib cache: 4 of 4 units reused' in ws and
           'attrib cache: 0 of 4 units reused' in cs)
        d = json.loads(plain)
        ok('the attribution itself (changed, gone, rule family)', d['functions'].get('00401301', {}).get('rules')
           == ['RCAST1'] and d['functions'].get('00401302', {}).get('rules') == ['GONE'])
        p = os.path.join(tree, 'src', 'u2.cpp')
        open(p, 'w').write(open(p).read().replace('return a + 2;', 'return fnvnx_lvalue(a) + 2;'))
        plain2, _ = rules(os.path.join(t, 'd.json'), False)
        warm2, ws2 = rules(os.path.join(t, 'e.json'), True)
        ok('one prepared unit changed: it is attributed again, the rest reused, same JSON as uncached',
           warm2 == plain2 and plain2 != plain and 'attrib cache: 3 of 4 units reused' in ws2)
    print('test_attrib_cache: all ok' if not fails else 'test_attrib_cache: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
