#!/usr/bin/env python3
"""Tests for contract/exportgen.py and bridgetable.derive(exports=): objects built with the host gcc from the real header contract/include/fnvnx_export.h.
Run: python3 -I <this file>. Needs gcc and pyelftools."""
import os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
C = os.path.dirname(HERE)
sys.path.insert(0, C)
import exportgen, bridgetable as B  # noqa: E402

fails = 0


def check(name, ok, info=''):
    global fails
    print('%s %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else '  ' + repr(info)[:300]))
    fails += 0 if ok else 1


def obj(td, rel, src):
    p = os.path.join(td, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    c = p[:-2] + '.c'
    open(c, 'w').write('#include "fnvnx_export.h"\n' + src)
    subprocess.run(['gcc', '-c', '-I', os.path.join(C, 'include'), c, '-o', p], check=True)


def collect(td):
    try:
        return exportgen.collect(td), None
    except ValueError as e:
        return None, str(e)


with tempfile.TemporaryDirectory() as td:
    obj(td, 'a/x.o', 'int my_free(void*p){return 0;}\nFNVNX_EXPORT("msvcrt.dll","free",my_free,"func");\nint my_iob[4];\nFNVNX_EXPORT("msvcrt.dll","_iob",my_iob,"data");\n')
    obj(td, 'b/y.o', 'int plain(void){return 1;}\n')
    rows, err = collect(td)
    check('records are read, sorted, from every object', rows == [('msvcrt.dll', '_iob', 'my_iob', 'data'), ('msvcrt.dll', 'free', 'my_free', 'func')], (rows, err))
    out = os.path.join(td, 'e.tsv')
    subprocess.run([sys.executable, '-I', os.path.join(C, 'exportgen.py'), '--objects', td, '--out', out], check=True, capture_output=True)
    check('exports.tsv reads back', exportgen.read(open(out).read()) == {('msvcrt.dll', 'free'): ('my_free', 'func'), ('msvcrt.dll', '_iob'): ('my_iob', 'data')})
    obj(td, 'c/z.o', 'int my_free(void*p){return 2;}\n')
    rows, err = collect(td)
    check('a symbol with two definers is an error', err and 'definers' in err, err)
    os.remove(os.path.join(td, 'c/z.o'))
    obj(td, 'c/w.o', 'int other(void){return 0;}\nFNVNX_EXPORT("msvcrt.dll","free",other,"func");\n')
    rows, err = collect(td)
    check('a (module, name) given twice is an error', err and 'given twice' in err, err)
    os.remove(os.path.join(td, 'c/w.o'))
    obj(td, 'c/v.o', 'extern int nowhere(void);\nFNVNX_EXPORT("msvcrt.dll","ghost",nowhere,"func");\n')
    rows, err = collect(td)
    check('a symbol the carrying object does not define is an error', err and 'not defined' in err, err)
    os.remove(os.path.join(td, 'c/v.o'))
    obj(td, 'c/u.o', 'int f(void){return 0;}\nFNVNX_EXPORT("MSVCRT.dll","f",f,"func");\n')
    rows, err = collect(td)
    check('an upper-case module is an error', err is not None and 'lower-case' in err, err)

names = 'msvcrt.dll\tfree\nmsvcrt.dll\t_iob\nwsock32.dll\tsocket\n'
spec = {'msvcrt': '1 cdecl free(ptr)\n2 extern _iob\n', 'wsock32': '1 stdcall socket(long long long)\n'}
nt = B.read_name_table('')
ex = {('msvcrt.dll', 'free'): ('my_free', 'func'), ('msvcrt.dll', '_iob'): ('my_iob', 'data'), ('wsock32.dll', 'socket'): ('whatever_sock', 'func')}
rows, errs, _ = B.derive(names, lambda d: spec[d], {'my_free', 'my_iob', 'whatever_sock'}, nt, [], None, ex)
got = {(r[0], r[1]): (r[6], r[7]) for r in rows}
check('derive(exports=) takes every symbol from the table (no prefix rule)', not errs and got == {('msvcrt.dll', 'free'): ('my_free', 'sym'), ('msvcrt.dll', '_iob'): ('my_iob', 'data'), ('wsock32.dll', 'socket'): ('whatever_sock', 'sym')}, (errs, got))
rows, errs, _ = B.derive(names, lambda d: spec[d], {'my_free'}, nt, [], None, ex)
check('a mapped symbol the objects lack binds none', {(r[0], r[1]): r[7] for r in rows}[('wsock32.dll', 'socket')] == 'none')
rows, errs, _ = B.derive(names, lambda d: spec[d], None, nt, [], None, {('msvcrt.dll', 'free'): ('my_free', 'data')})
check('data/function disagreement with the spec is an error', any('exports.tsv says data' in e for e in errs), errs)
print('fails: %d' % fails)
sys.exit(1 if fails else 0)
