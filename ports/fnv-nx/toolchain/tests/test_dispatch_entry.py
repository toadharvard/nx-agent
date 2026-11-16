#!/usr/bin/env python3
"""Test of ENTRY1 (toolchain/dispatch/fnvnx_dispatch.cc): `fnvnx_entry` on a pointer-to-function type marks native entry addresses. A call through a value of
that type is not rewritten and has no .fnvnx.disp row (a plain fnptr call is still rewritten); the address of a function converts to the entry type; every other
conversion with an entry type on one side (int, plain fnptr, void*, C cast, reinterpret_cast, implicit) is the error ENTRY1-CONT naming both types; the
attribute on anything but a pointer-to-function type is an error. Built through plugbuild (the recipe's flags and plugins).
Run: python3 -I <this file> from the checkout root."""
import json, os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402

check = pb.Checks('test_dispatch_entry')
FIX = os.path.join(HERE, 'dispatch_entry')


def body(dis, frag):
    sym = next((k for k in dis if frag in k), None)
    return '\n'.join(re.sub(r'\s+', ' ', l.strip()) for l in dis.get(sym, []))


def disp_rows(obj):
    from elftools.elf.elffile import ELFFile
    with open(obj, 'rb') as fh:
        s = ELFFile(fh).get_section_by_name('.fnvnx.disp')
        return s.data().decode().split('\n')[:-1] if s is not None else []


def header_languages(outs, store, inc_dir):
    """THE UNIVERSE OF THE HEADER TESTS, from the recipe's own PP-stage dependency facts: a header of INC_DIR (the owning root's first include directory, which the
    stage sees as /inc/0, and as /src/include after the root's own relative `../include` quote) is tested as C if and only if some C unit of the product root includes
    it, and as C++ if and only if some C++ unit does. A header that no unit includes is in neither. OUTS: the objects.json files of builds of the root (its variants
    included); each has `pp_keys` (unit -> key of its PP stage), and the PP stage's `deps.d` lists every file the unit read."""
    langs = {}
    for out in outs:
        pp = json.load(open(os.path.join(out, 'objects.json')))['pp_keys']
        for unit, key in pp.items():
            res = json.load(open(os.path.join(store, 'keys', key, 'result.json')))
            tree = res['outputs']['/out']
            entries = json.load(open(os.path.join(store, 'tree', tree[:2], tree + '.json')))['entries']
            h = entries['deps.d']['h']
            deps = open(os.path.join(store, 'obj', h[:2], h)).read().replace('\\\n', ' ')
            lang = 'C' if unit.endswith('.c') else 'C++'
            for path in deps.split()[1:]:
                n = os.path.normpath(path)
                for pre in ('/inc/0/', '/src/include/'):
                    if n.startswith(pre) and n.endswith('.h') and '/' not in n[len(pre):]:
                        langs.setdefault(n[len(pre):], set()).add(lang)
    return {h: l for h, l in langs.items() if os.path.isfile(os.path.join(inc_dir, h))}


def headers(td, so, outs, store):
    """a header of platform/include is compiled in the languages of the units that include it (header_languages), under the recipe's flags and plugins. The include
    dirs are the owning root's own (platform/build.json `include`: the repository directories of the root and of the roots it `uses`), copied whole under the toy
    root, in the root's order; the pinned externals of that list are the toy root's own toolchain."""
    inc = os.path.join(HERE, '..', '..', 'platform', 'include')
    uni = header_languages(outs, store, inc)
    owner = json.load(open(os.path.join(HERE, '..', '..', 'platform', 'build.json')))
    repo = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))   # a directory the root generates (a stage output) is not a repository directory: it is no header home here
    for i, d in enumerate(x for x in owner['include'] if x.startswith('ports/fnv-nx/') and os.path.isdir(os.path.join(repo, x))):
        if d == 'ports/fnv-nx/platform/include':   # the root's own include dir lives inside a copy of the root: its relative quotes (`../os/os.h`) resolve as in the root
            hdrs = os.path.join(td, 'platform-headers')   # the root's header files only, in the root's layout (a unit of the root is no unit of the toy root)
            shutil.copytree(os.path.join(repo, 'ports/fnv-nx/platform'), hdrs, ignore=lambda d, names: [n for n in names if not (n.endswith('.h') or os.path.isdir(os.path.join(d, n)))])
            pb.EXTRA['copy'].append((hdrs, 'plat'))
        else:
            pb.EXTRA['copy'].append((d, 'inc%d' % i))
            pb.EXTRA['include'].append(pb.ROOT + '/inc%d' % i)
    print('header universe: %d headers; C %d, C++ %d' % (len(uni), sum('C' in v for v in uni.values()), sum('C++' in v for v in uni.values())))
    try:
        for h in sorted(uni):
            for ext, lang in (('c', 'C'), ('cpp', 'C++')):
                if lang not in uni[h]:
                    continue
                src = os.path.join(td, 'hdr_%s.%s' % (h[:-2], ext))
                open(src, 'w').write('#include "../plat/include/%s"\n' % h)   # by the root's own relative path, as its units reach it: the header's relative quotes resolve in the root's tree
                r = pb.compile(src, os.path.join(td, 'hdr_%s_%s.o' % (h[:-2], ext)), so)
                check('platform/include/%s compiles as %s' % (h, lang), r.returncode == 0, r.stderr[:900])
    finally:
        pb.EXTRA['copy'].clear(); pb.EXTRA['include'].clear()


def main(argv):
    # the facts of the header universe are an argument of the run (no default path): --platform-out DIR (objects.json of a build of the platform root, repeatable) --store DIR
    outs = [argv[i + 1] for i, a in enumerate(argv) if a == '--platform-out']
    store = next((argv[i + 1] for i, a in enumerate(argv) if a == '--store'), None)
    if not outs or not store:
        sys.exit('test_dispatch_entry: missing --platform-out DIR (objects.json of a build of the platform root) and --store DIR')
    td = tempfile.mkdtemp(prefix='test-dispatch-entry-')
    try:
        so = [pb.build(n, td) for n in ('sdd', 'names', 'dispatch')]
        obj = os.path.join(td, 'ok.o')
        r = pb.compile(os.path.join(FIX, 'ok.cpp'), obj, so)
        check('&func converts to the entry type without error (and the entry calls compile)', r.returncode == 0, r.stderr[:1500])
        if r.returncode:
            return check.done()
        dis = pb.objdump(obj)
        for f in ('10call_entry', '11call_loaded', '12call_via_get'):
            b = body(dis, f)
            check('%s: an entry call is not rewritten (no fnvnx_dispatch call)' % f, bool(b) and 'fnvnx_dispatch' not in b and re.search(r'\b(?:blr|br) x\d+', b), b)
        b = body(dis, '10call_plain')
        check('a plain fnptr call is still rewritten (a call to fnvnx_dispatch)', 'fnvnx_dispatch' in b, b)
        check('the .fnvnx.disp section has the descriptor of the plain call only (no entry descriptor)', disp_rows(obj) == ['stack:i:i'], disp_rows(obj))
        for n, want in (('int2entry', 'conversion between'), ('entry2plain', 'conversion between'), ('plain2entry', 'conversion between'),
                        ('plain2entry_reint', 'conversion between'), ('entry2void', 'conversion between'), ('implicit', 'conversion between'), ('return', 'conversion between'), ('arg', 'conversion between')):
            r = pb.compile(os.path.join(FIX, 'bad_%s.cpp' % n), os.path.join(td, n + '.o'), so)
            check('%s is the error ENTRY1-CONT' % n, r.returncode != 0 and 'ENTRY1-CONT' in r.stderr and want in r.stderr, r.stderr[:900])
        r = pb.compile(os.path.join(FIX, 'ok_paths.cpp'), os.path.join(td, 'p.o'), so)
        check('static initialisers (scalar, array, aggregate), an aggregate copy and a same-type builtin copy are accepted', r.returncode == 0, r.stderr[:1500])
        for n, tag in (('static_dyn', 'ENTRY1-CONT'), ('static_const', 'a constant is no native entry'), ('static_ctor', 'a constant is no native entry'),
                       ('union', 'ENTRY1-UNION'), ('union_nested', 'ENTRY1-UNION'), ('memcpy_other', 'ENTRY1-CONT'), ('memset', 'ENTRY1-CONT'), ('memmove_cast', 'ENTRY1-CONT')):
            r = pb.compile(os.path.join(FIX, 'bad_%s.cpp' % n), os.path.join(td, n + '.o'), so)
            check('%s is an error (%s)' % (n, tag), r.returncode != 0 and tag in r.stderr, r.stderr[:900])
        for ext, lang in (('cpp', 'C++'), ('c', 'C')):
            r = pb.compile(os.path.join(FIX, 'ok_atomic.' + ext), os.path.join(td, 'at_%s.o' % ext), so)
            check('ENTRY1-ATOMIC (%s): generic and sized atomic load, store, exchange and compare-exchange, and the __sync movers, of one entry-holding type are same-type copies' % lang, r.returncode == 0, r.stderr[:1500])
            for n in ('atomic_store_other', 'atomic_load_other', 'atomic_fetch_add'):
                r = pb.compile(os.path.join(FIX, 'bad_%s.%s' % (n, ext)), os.path.join(td, '%s_%s.o' % (n, ext)), so)
                check('%s (%s) is the error ENTRY1-CONT (an atomic move of another type, an arithmetic builtin on an entry)' % (n, lang), r.returncode != 0 and 'ENTRY1-CONT' in r.stderr, r.stderr[:900])
        # C front end: the generic form's `*(U *)&obj` of an object is the object (or a view conversion of it), not a pointer
        r = pb.compile(os.path.join(FIX, 'ok_atomic_local.c'), os.path.join(td, 'atl.o'), so)
        check('ENTRY1-ATOMIC (C): a generic atomic store of a local object of the same type is a same-type copy', r.returncode == 0, r.stderr[:1500])
        r = pb.compile(os.path.join(FIX, 'bad_atomic_store_local.c'), os.path.join(td, 'atlb.o'), so)
        check('ENTRY1-ATOMIC (C): a generic atomic store of a local object of another type is the error ENTRY1-CONT', r.returncode != 0 and 'ENTRY1-CONT' in r.stderr, r.stderr[:900])
        headers(td, so, outs, store)
        r = pb.compile(os.path.join(FIX, 'bad_attr.cpp'), os.path.join(td, 'a.o'), so)
        check('the attribute on a function type (not a pointer) is an error', r.returncode != 0 and 'applies to a pointer-to-function type' in r.stderr, r.stderr[:900])
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
