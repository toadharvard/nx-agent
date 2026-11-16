#!/usr/bin/env python3
"""Tests for contract/exportgen.py and bridgetable.derive(exports=): objects built with the host gcc from the real header contract/include/fnvnx_export.h.
Run: python3 -I <this file>. Needs gcc and pyelftools."""
import os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
C = os.path.dirname(HERE)
sys.path.insert(0, C)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))   # ports/fnv-nx: contract.X by its qualified name
from contract import exportgen, bridgetable as B  # noqa: E402

with open(os.path.join(C, '..', 'toolchain', 'layout', 'decltype_spell.inc')) as _f:
    REASONS = exportgen.ctypes_compat.decltype_reasons(_f.read())
fails = 0


def _ok(f):
    try:
        f()
        return True
    except ValueError:
        return False


def check(name, ok, info=''):
    global fails
    print('%s %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else '  ' + repr(info)[:300]))
    fails += 0 if ok else 1


def decl(sym, typ):
    """a hand-assembled `.fnvnx.decltype` row (the layout plugin writes it in the product build; grammar link/ctypes_compat.py)"""
    return '__asm__(".pushsection .fnvnx.decltype,\\"\\",@progbits\\n\\t.ascii \\"%s\\\\t%s\\\\n\\"\\n\\t.popsection");\n' % (sym, typ)


def obj(td, rel, src):
    p = os.path.join(td, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    c = p[:-2] + '.c'
    open(c, 'w').write('#include "fnvnx_export.h"\n' + src)
    subprocess.run(['gcc', '-c', '-I', os.path.join(C, 'include'), c, '-o', p], check=True)


def collect(td):
    try:
        return exportgen.collect(td, REASONS), None
    except ValueError as e:
        return None, str(e)


with tempfile.TemporaryDirectory() as td:
    obj(td, 'a/x.o', 'int my_free(void*p){return 0;}\n' + decl('my_free', '(*void)int') + 'FNVNX_EXPORT("msvcrt.dll","free",my_free,"func");\nint my_iob[4];\nFNVNX_EXPORT("msvcrt.dll","_iob",my_iob,"data");\n')
    obj(td, 'b/y.o', 'int plain(void){return 1;}\n')
    rows, err = collect(td)
    check('records are read, sorted, from every object', rows == [('msvcrt.dll', '_iob', 'my_iob', 'data', '-', '-'), ('msvcrt.dll', 'free', 'my_free', 'func', 'i', 'i')], (rows, err))
    out = os.path.join(td, 'e.tsv')
    subprocess.run([sys.executable, '-I', os.path.join(C, 'exportgen.py'), '--objects', td, '--out', out, '--reasons', os.path.join(C, '..', 'toolchain', 'layout', 'decltype_spell.inc')], check=True, capture_output=True)
    check('exports.tsv reads back', exportgen.read(open(out).read()) == {('msvcrt.dll', 'free'): ('my_free', 'func', 'i', 'i'), ('msvcrt.dll', '_iob'): ('my_iob', 'data', '-', '-')})
    obj(td, 'c/z.o', 'int my_free(void*p){return 2;}\n')
    rows, err = collect(td)
    check('a symbol with two definers is an error', err and 'definers' in err, err)
    os.remove(os.path.join(td, 'c/z.o'))
    obj(td, 'c/w.o', 'int other(void){return 0;}\n' + decl('other', '()int') + 'FNVNX_EXPORT("msvcrt.dll","free",other,"func");\n')
    rows, err = collect(td)
    check('a (module, name) given twice is an error', err and 'given twice' in err, err)
    os.remove(os.path.join(td, 'c/w.o'))
    obj(td, 'c/v.o', 'extern int nowhere(void);\nFNVNX_EXPORT("msvcrt.dll","ghost",nowhere,"func");\n')
    rows, err = collect(td)
    check('a symbol the carrying object does not define is an error', err and 'not defined' in err, err)
    os.remove(os.path.join(td, 'c/v.o'))
    obj(td, 'c/u.o', 'int f(void){return 0;}\n' + decl('f', '()int') + 'FNVNX_EXPORT("MSVCRT.dll","f",f,"func");\n')
    rows, err = collect(td)
    check('an upper-case module is an error', err is not None and 'lower-case' in err, err)

# the return letter of the definer's own declared type, by class (ret_class)
for typ, want in (('()void', 'v'), ('()float', 'f'), ('()double', 'd'), ('()long_double', 'd'), ('()long_long_int', 'q'), ('()long_long_unsigned_int', 'q'), ('()*char', 'i'),
                  ('()unsigned_int', 'i'), ('()long_int', 'i'), ('()enum:E=unsigned_int', 'i'), ('()const:long_long_int', 'q'), ('(int,*void)_Bool', 'i')):
    check('ret_class %s -> %s' % (typ, want), exportgen.ret_class(exportgen.ctypes_compat.parse(typ)) == want, typ)
for typ in ('()struct:S{a:int;}', '()vec:4:16:16:float', '()complex:8:4:float', '()ref:4:4:int', '*int'):
    try:
        exportgen.ret_class(exportgen.ctypes_compat.parse(typ))
        check('ret_class %s is an error' % typ, False)
    except ValueError:
        check('ret_class %s is an error' % typ, True)
with tempfile.TemporaryDirectory() as td:
    obj(td, 'a/d.o', 'double dd(void){return 1;}\n' + decl('dd', '()double') + 'FNVNX_EXPORT("m.dll","dd",dd,"func");\n')
    rows, err = collect(td)
    check('a function export carries the return letter of its declared type', rows == [('m.dll', 'dd', 'dd', 'func', 'd', '-')], (rows, err))
    obj(td, 'a/n.o', 'int nd(void){return 1;}\nFNVNX_EXPORT("m.dll","nd",nd,"func");\n')
    rows, err = collect(td)
    check('a function export whose definer has no type row is an error', err and 'no type row' in err, err)

# args_class: the argument letters of the definer's declared parameters
for typ, want in (('()void', '-'), ('(*void,int)int', 'ii'), ('(float,double,long_double,long_long_int)void', 'fddq'), ('(enum:E=unsigned_int,ref:4:4:int,[4]int)void', 'iii'),
                  ('(const:long_long_unsigned_int)void', 'q')):
    check('args_class %s -> %s' % (typ, want), exportgen.args_class(exportgen.ctypes_compat.parse(typ)) == want, typ)
for typ in ('(struct:S{a:int;})void', '(vec:4:16:16:float)void', '(complex:8:4:float)void', '(?)int', '(*void,...)int', '*int'):
    try:
        exportgen.args_class(exportgen.ctypes_compat.parse(typ))
        check('args_class %s is an error' % typ, False)
    except ValueError:
        check('args_class %s is an error' % typ, True)
# the entry kind: a guest-state body, void(void) or red; its row carries `-` for ret and args
with tempfile.TemporaryDirectory() as td:
    obj(td, 'a/e.o', 'void body(void){}\n' + decl('body', '()void') + 'FNVNX_EXPORT("kernel32.dll","RaiseException",body,"entry");\n')
    rows, err = collect(td)
    check('an entry whose definer is void(void) is a row with ret - and args -', rows == [('kernel32.dll', 'RaiseException', 'body', 'entry', '-', '-')], (rows, err))
    check('exports.tsv with an entry reads back', exportgen.read('kernel32.dll\tRaiseException\tbody\tentry\t-\t-\n') == {('kernel32.dll', 'RaiseException'): ('body', 'entry', '-', '-')})
    obj(td, 'b/f.o', 'int arg1(int a){return a;}\n' + decl('arg1', '(int)int') + 'FNVNX_EXPORT("kernel32.dll","Arg1",arg1,"entry");\n')
    rows, err = collect(td)
    check('an entry that is not void(void) is an error', err and 'not void(void)' in err, err)
    os.remove(os.path.join(td, 'b/f.o'))
    obj(td, 'b/g.o', 'void r0(void){}\n' + decl('r0', '()int') + 'FNVNX_EXPORT("kernel32.dll","R0",r0,"entry");\n')
    rows, err = collect(td)
    check('an entry that returns a value is an error', err and 'not void(void)' in err, err)
    os.remove(os.path.join(td, 'b/g.o'))
    obj(td, 'b/h.o', 'void nt(void){}\nFNVNX_EXPORT("kernel32.dll","NT",nt,"entry");\n')
    rows, err = collect(td)
    check('an entry without a type row is an error', err and 'no type row' in err, err)
    os.remove(os.path.join(td, 'b/h.o'))
    obj(td, 'b/k.o', 'void kk(void){}\n' + decl('kk', '()void') + 'FNVNX_EXPORT("kernel32.dll","KK",kk,"code");\n')
    rows, err = collect(td)
    check('a kind other than func, entry, data is an error', err is not None and 'none of func, entry, data' in err, err)
    obj(td, 'c/a.o', 'int two(int a, double b){return 0;}\n' + decl('two', '(int,double)int') + 'FNVNX_EXPORT("m.dll","two",two,"func");\n')
    os.remove(os.path.join(td, 'b/k.o'))
    rows, err = collect(td)
    check('a function export carries the argument letters of its declared type', ('m.dll', 'two', 'two', 'func', 'i', 'id') in (rows or []), (rows, err))


# the relocation form: the definer is `&symbol`, so a typo is a compile error and the symbol is the object's own symbol (C++ as well as C)
with tempfile.TemporaryDirectory() as td:
    try:
        obj(td, 'a/t.o', 'int real_fn(void){return 0;}\nFNVNX_EXPORT("m.dll","typo",real_fnn,"func");\n')
        check('a symbol that is not declared is a compile error', False)
    except subprocess.CalledProcessError:
        check('a symbol that is not declared is a compile error', True)
    src = ('#include "fnvnx_export.h"\nextern "C" {\nint cf(int a){return a;}\nvoid ce(void){}\nint cd[2];\n}\n'
           + decl('cf', '(int)int') + decl('ce', '()void')
           + 'FNVNX_EXPORT("m.dll","CF",cf,"func");\nFNVNX_EXPORT("m.dll","CE",ce,"entry");\nFNVNX_EXPORT("m.dll","CD",cd,"data");\n')
    os.makedirs(td + '/b')
    open(td + '/b/x.cc', 'w').write(src)
    subprocess.run(['g++', '-c', '-fPIC', '-I', os.path.join(C, 'include'), td + '/b/x.cc', '-o', td + '/b/x.o'], check=True)
    rows, err = collect(td)
    check('C++: func, entry and data records round-trip through the relocations', rows == [('m.dll', 'CD', 'cd', 'data', '-', '-'), ('m.dll', 'CE', 'ce', 'entry', '-', '-'), ('m.dll', 'CF', 'cf', 'func', 'i', 'i')], (rows, err))
# parse_records on bytes: a missing relocation, a section symbol, an addend, an empty field, an unterminated string are errors; padding between records is skipped
R = exportgen.parse_records
good = b'a.dll\0F\0func\0'.ljust(24, b'\0') + b'b.dll\0G\0data\0'.ljust(24, b'\0')
ok1 = R(good, {16: ('sym_a', 0), 40: ('sym_b', 0)}, 8, 'w')
check('parse_records: two records with padding', ok1 == [('a.dll', 'F', 'sym_a', 'func'), ('b.dll', 'G', 'sym_b', 'data')], ok1)
for why, data, rel in (('no relocation', good, {16: ('sym_a', 0)}), ('a section symbol', good, {16: (None, 0), 40: ('sym_b', 0)}), ('an addend', good, {16: ('sym_a', 4), 40: ('sym_b', 0)}),
                       ('a relocation belonging to no record', good, {16: ('sym_a', 0), 40: ('sym_b', 0), 8: ('x', 0)}), ('an unterminated string', b'a.dll\0F\0func', {})):
    try:
        R(data, rel, 8, 'w')
        check('parse_records: %s is an error' % why, False)
    except ValueError:
        check('parse_records: %s is an error' % why, True)

names = 'msvcrt.dll\tfree\nmsvcrt.dll\t_iob\nwsock32.dll\tsocket\n'
spec = {'msvcrt': '1 cdecl free(ptr)\n2 extern _iob\n', 'wsock32': '1 stdcall socket(long long long) ws2_32.socket\n', 'ws2_32': '1 stdcall socket(long long long)\n'}
ex = {('msvcrt.dll', 'free'): ('my_free', 'func', 'v', 'i'), ('msvcrt.dll', '_iob'): ('my_iob', 'data', '-', '-'), ('ws2_32.dll', 'socket'): ('whatever_sock', 'func', 'i', 'iii')}
rows, errs, _ = B.derive(names, lambda d: spec[d], None, [], {}, ex)
got = {(r[0], r[1]): (r[4], r[6], r[7]) for r in rows}
check("parse_export accepts the runtime module '!rt' and still rejects other non *.dll modules",
      exportgen.parse_export(b"!rt\teh_x\tsym_x\tentry\n", 't') == [('!rt', 'eh_x', 'sym_x', 'entry')] and not any(_ok(lambda m=m: exportgen.parse_export(m.encode(), 't')) for m in ("rt\ta\tb\tfunc\n", "!rt2\ta\tb\tfunc\n")), 0)
check('derive takes the symbol and the return letter from the record, through the forward chain of the declared machine (wsock32 -> ws2_32)',
      not errs and got == {('msvcrt.dll', 'free'): ('v', 'my_free', 'link'), ('msvcrt.dll', '_iob'): ('-', 'my_iob', 'link'), ('wsock32.dll', 'socket'): ('i', 'whatever_sock', 'link')}, (errs, got))
rows, errs, _ = B.derive(names, lambda d: spec[d], None, [], {}, {k: v for k, v in ex.items() if k != ('msvcrt.dll', 'free')})
check('a function import with no record is an error (no constant names a symbol or a return type)', any('msvcrt.dll!free: no export record' in e for e in errs), errs)
rows, errs, _ = B.derive(names, lambda d: spec[d], None, [], {}, {**ex, ('wsock32.dll', 'socket'): ('other', 'func', 'i', '-')})
check('two records in one forward chain are an error', any('one import has one definer' in e for e in errs), errs)
rows, errs, _ = B.derive(names, lambda d: spec[d], None, [], {}, {**ex, ('msvcrt.dll', 'free'): ('my_free', 'data', '-', '-')})
check('data/function disagreement with the spec is an error', any('exports.tsv says data' in e for e in errs), errs)
rows, errs, _ = B.derive(names, lambda d: spec[d], None, [], {'free': 'forwarder'}, ex)
check('the va record must name the exported symbol', any('the va record names the forwarder' in e for e in errs), errs)
print('fails: %d' % fails)
sys.exit(1 if fails else 0)
