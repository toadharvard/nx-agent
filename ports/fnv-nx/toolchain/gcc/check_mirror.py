#!/usr/bin/env python3
"""check_mirror.py - the checks of the fnvnx-a64 mirror (toolchain/gcc/build.sh header: the class rule).

  check_mirror.py <prefix> [--s2 <stage 2 prefix>] [--gcc13 13.3.0] [--manifest]

The rule: every ILP32 runtime library a game unit links comes from the unit's own compiler (GCC 13.3.0 + msvcmodel.patch)
with the unit's data-model flags -mabi=ilp32 -fshort-wchar -fsigned-char. Checks (any failure exits 1):
  C1  every multilib entry of the shipped driver (and of stage 2's) with -mabi=ilp32 carries -fshort-wchar and -fsigned-char;
      the only ILP32 library directories in the mirror are ilp32/wchar16/schar and ilp32/wchar16/schar/pic.
  C2  selection: the directory is the driver's answer under the pack's own flags (no directory is named): it holds libc, libstdc++,
      libsupc++ and libgcc and a link with those flags succeeds; -mabi=ilp32 alone resolves elsewhere, holds no library, and
      its link fails.
  C3  wide characters: for every ILP32 variant the target preprocessor says __SIZEOF_WCHAR_T__ 2, __WCHAR_MAX__ 0xffff and
      no __CHAR_UNSIGNED__; wcslen and the other wide functions of the libc.a that driver selects use 2-byte accesses
      (ldrh/strh).
  C4  the libstdc++ headers of the ILP32 variant are 13.3 (_GLIBCXX_RELEASE 13, 20240xxx); no c++/15.2.0 ILP32 headers exist.
  C6  no sized deallocation: no object of the pack has an undefined reference to a sized or aligned-sized operator delete
      (the four mangled names come from the built compiler, not a list); their definitions, if built, call the unsized forms.
  C7  one C++ library, one ABI: exactly one C++ include version (13.3.0), every ELF object of the library trees is ELF32 AArch64.
  C5  (--manifest) MANIFEST.json: gcc version, msvcmodel.patch sha256, flags, sizeof/alignof of wchar_t, size_t, long,
      long double, pointer, read from the game compiler's object code (no run).
  C8  libnx (stage 3, the game's compiler, same semantics set) is in the ILP32 pic dir with its headers; no member writes x19..x27 (read-only
      mentions: a store source, a compare, a test). Reports the numbers checked.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

TARGET = 'aarch64-none-elf'
FLAGS = ['-mabi=ilp32', '-fshort-wchar', '-fsigned-char']
fails = []


def fail(msg):
    fails.append(msg)
    print('FAIL: ' + msg)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def multilibs(gcc):
    out = run([gcc, '-print-multi-lib'])
    r = []
    for ln in out.stdout.split():
        d, _, fl = ln.partition(';')
        r.append((d, [f for f in fl.split('@') if f]))
    return r


def c1(prefix, s2):
    for name, gcc in (('shipped', prefix + '/bin/%s-gcc' % TARGET),) + ((('stage2', s2 + '/bin/%s-gcc' % TARGET),) if s2 else ()):
        ml = multilibs(gcc)
        if not ml:
            fail('C1 %s driver: no multilib table' % name)
        for d, fl in ml:
            if 'mabi=ilp32' in fl and not ('fshort-wchar' in fl and 'fsigned-char' in fl):
                fail('C1 %s driver: ILP32 variant "%s" lacks -fshort-wchar/-fsigned-char (%s)' % (name, d, fl))
    want = {'ilp32/wchar16/schar', 'ilp32/wchar16/schar/pic'}
    base = prefix + '/%s/lib/ilp32' % TARGET
    have = set()
    for root, _, files in os.walk(base):
        if any(f.endswith('.a') for f in files):
            have.add('ilp32/' + os.path.relpath(root, base) if root != base else 'ilp32')
    if have != want:
        fail('C1 ILP32 library directories %s, expected %s' % (sorted(have), sorted(want)))
    print('C1 ok: ILP32 variants %s' % sorted(have))


def c2(prefix, s2=None, gcc13='13.3.0'):
    # the game compiler's base driver (stage 2, gcc 13.3) against the mirror's libraries
    gcc = (s2 or prefix) + '/bin/%s-gcc' % TARGET
    mir = ['--sysroot=%s/%s' % (prefix, TARGET), '-B%s/lib/gcc/%s/%s/' % (prefix, TARGET, gcc13)]
    d1 = run([gcc, '-mabi=ilp32', '-print-multi-directory']).stdout.strip()
    d2 = run([gcc] + FLAGS + ['-print-multi-directory']).stdout.strip()
    # the directory is the driver's answer under the pack's own flags; no directory is named here. The mirror is ILP32 only:
    # the directory of the incomplete flag set (-mabi=ilp32 alone) holds no library, so that link must fail
    if d1 == d2:
        fail('C2 -mabi=ilp32 alone resolves to the same directory "%s" as the full ILP32 flags' % d2)
    for lib in ('libc.a', 'libstdc++.a', 'libsupc++.a', 'libgcc.a'):
        if not (os.path.exists('%s/%s/lib/%s/%s' % (prefix, TARGET, d2, lib)) or os.path.exists('%s/lib/gcc/%s/%s/%s/%s' % (prefix, TARGET, gcc13, d2, lib))):
            fail('C2 the selected directory "%s" has no %s' % (d2, lib))
    if os.path.exists('%s/%s/lib/%s/libc.a' % (prefix, TARGET, d1)):
        fail('C2 -mabi=ilp32 alone resolves to "%s", which holds a libc.a' % d1)
    with tempfile.TemporaryDirectory() as t:
        open(t + '/m.c', 'w').write('int main(void){return 0;}\n')
        r = run([gcc] + mir + ['-mabi=ilp32', t + '/m.c', '-o', t + '/m.elf'])
        if r.returncode == 0:
            fail('C2 a link of -mabi=ilp32 without -fshort-wchar succeeded')
        r = run([gcc] + mir + FLAGS + [t + '/m.c', '-o', t + '/m.elf', '-nostartfiles', '-Wl,-e,main'])
        if r.returncode != 0:
            fail('C2 a link with the full ILP32 flags failed: ' + r.stderr.strip()[-300:])
    print('C2 ok: -mabi=ilp32 -> "%s" (no library, link fails), pack flags -> "%s" (link ok)' % (d1, d2))


WIDE = ['wcslen', 'wcscpy', 'wcscmp', 'wmemcmp', 'wcschr']   # functions that read or write single wchar_t elements


def member_asm(prefix, libc, name):
    od = prefix + '/bin/%s-objdump' % TARGET
    ar = prefix + '/bin/%s-ar' % TARGET
    names = [m for m in run([ar, 't', libc]).stdout.split() if re.fullmatch(r'(lib|libc)_a-%s\.o' % name, m)]
    if len(names) != 1:
        return None
    member = names[0]
    with tempfile.TemporaryDirectory() as t:
        r = run([ar, 'x', libc, member], cwd=t)
        if r.returncode != 0 or not os.path.exists(t + '/' + member):
            return None
        return run([od, '-d', t + '/' + member]).stdout


def c3(prefix, s2=None):
    gcc = (s2 or prefix) + '/bin/%s-gcc' % TARGET
    for d, fl in multilibs(gcc):
        if 'mabi=ilp32' not in fl:
            continue
        macros = run([gcc] + ['-' + f for f in fl] + ['-dM', '-E', '-x', 'c', '/dev/null']).stdout
        if not re.search(r'#define __SIZEOF_WCHAR_T__ 2$', macros, re.M) or not re.search(r'#define __WCHAR_MAX__ 0xffff', macros) \
                or '__CHAR_UNSIGNED__' in macros:
            fail('C3 variant %s: the target preprocessor does not say 2-byte wchar_t / signed char' % d)
    # the pack's libraries are the ones the driver selects under the pack's own flags
    sel = run([gcc] + FLAGS + ['-print-multi-directory']).stdout.strip()
    n = 0
    for m in WIDE:
        member = m
        a32 = member_asm(prefix, '%s/%s/lib/%s/libc.a' % (prefix, TARGET, sel), member)
        if a32 is None:
            fail('C3 %s: not exactly one member (lib|libc)_a-%s.o in the selected libc.a' % (m, m))
            continue
        if not re.search(r'\b(ldrh|strh)\b', a32):
            fail('C3 %s: the selected object has no 2-byte access' % m)
        n += 1
    print('C3 ok: ILP32 variants preprocess to 2-byte wchar_t; %d wide functions use 2-byte accesses' % n)


def c4(prefix, gcc13):
    base = prefix + '/%s/include/c++' % TARGET
    if sorted(os.listdir(base)) != [gcc13]:
        fail('C4 c++ header directories %s, expected [%s]' % (sorted(os.listdir(base)), gcc13))
        return
    cfg = '%s/%s/%s/ilp32/wchar16/schar/bits/c++config.h' % (base, gcc13, TARGET)
    txt = open(cfg).read() if os.path.exists(cfg) else ''
    if not re.search(r'#define _GLIBCXX_RELEASE 13\b', txt) or not re.search(r'#define __GLIBCXX__ 2024\d\d\d\d', txt):
        fail('C4 libstdc++ headers of the ILP32 variant are not 13.3 (%s)' % cfg)
    print('C4 ok: libstdc++ headers %s only, ILP32 c++config.h is 13' % gcc13)


def c5(prefix, s2, gcc13, here, flags_dir=None, flags_commit=''):
    gcc = (s2 or prefix) + '/bin/%s-gcc' % TARGET
    with tempfile.TemporaryDirectory() as t:
        open(t + '/p.c', 'w').write(
            '#include <stddef.h>\n#include <float.h>\n#include <sys/config.h>\n'
            'const unsigned char fnvnx_probe[] = {sizeof(wchar_t), _Alignof(wchar_t), sizeof(size_t), '
            '_Alignof(size_t), sizeof(long), _Alignof(long), sizeof(long double), _Alignof(long double), sizeof(void *), '
            '_Alignof(void *), LDBL_MANT_DIG, DBL_MANT_DIG,\n'
            '#ifdef _LDBL_EQ_DBL\n1\n#else\n0\n#endif\n};\n')
        r = run([gcc] + FLAGS + ['-O0', '-c', t + '/p.c', '-o', t + '/p.o'])
        if r.returncode != 0:
            fail('C5 probe does not compile: ' + r.stderr.strip()[-300:])
            return
        dump = run([prefix + '/bin/%s-objdump' % TARGET, '-s', '-j', '.rodata', t + '/p.o']).stdout
        hexs = ''.join(re.findall(r'^ [0-9a-f]{4} ((?:[0-9a-f]{2,8} ?)+)', dump, re.M)).replace(' ', '')
        vals = [int(hexs[i:i + 2], 16) for i in range(0, 26, 2)]   # 10 data-model bytes, then LDBL_MANT_DIG, DBL_MANT_DIG, newlib's _LDBL_EQ_DBL
    ldbl_mant, dbl_mant, newlib_eq = vals[10:13]
    vals = vals[:10]
    names = ['wchar_t', 'size_t', 'long', 'long double', 'pointer']
    man = {
        'compiler': 'gcc %s' % run([gcc, '-dumpfullversion']).stdout.strip(),
        'msvcmodel_patch_sha256': hashlib.sha256(open(os.path.join(here, '..', 'msvcmodel', 'msvcmodel.patch'), 'rb').read()).hexdigest(),
        'target': TARGET,
        'ilp32_flags': ' '.join(FLAGS),
        'ilp32_variant_dirs': ['ilp32/wchar16/schar', 'ilp32/wchar16/schar/pic'],
        'sizeof': {n: vals[2 * i] for i, n in enumerate(names)},
        'alignof': {n: vals[2 * i + 1] for i, n in enumerate(names)},
    }
    # long double of the exe's compiler (VC++ 2008) is binary64: the runtime's size and mantissa are the compiler's own answers (the recipe compares them
    # with the pinned compiler's before any unit runs), and newlib derives _LDBL_EQ_DBL from them: it is defined exactly when the mantissas are equal.
    man['ldbl_mant_dig'], man['dbl_mant_dig'], man['newlib_ldbl_eq_dbl'] = ldbl_mant, dbl_mant, newlib_eq
    if bool(newlib_eq) != (ldbl_mant == dbl_mant):
        fail('C5 newlib _LDBL_EQ_DBL=%d but LDBL_MANT_DIG=%d, DBL_MANT_DIG=%d (it must follow from the compiler)' % (newlib_eq, ldbl_mant, dbl_mant))
    if man['compiler'] != 'gcc ' + gcc13:
        fail('C5 the game compiler is %s, not gcc %s' % (man['compiler'], gcc13))
    if man['sizeof']['wchar_t'] != 2 or man['sizeof']['size_t'] != 4 or man['sizeof']['pointer'] != 4:
        fail('C5 the ILP32 data model is not the exe\'s: %s' % man['sizeof'])
    # L1's pack manifest: the same facts under the names the guest roots compare (all from the built compiler's own answers)
    man['version'] = man['compiler'].split()[1]
    if flags_dir:   # the flags as compiled, per language, from verify/guest_flags.py (commit recorded)
        fl = {l: json.load(open('%s/flags-%s.json' % (flags_dir, l))) for l in ('c', 'c++', 'asm')}
        # the C set is derived again from the built C driver: the C++ set minus every flag it rejects for C
        derived = []
        for f in fl['c++']:
            r = run([gcc, f, '-O0', '-fsyntax-only', '-x', 'c', '/dev/null'])
            if r.returncode == 0 and 'but not for C' not in r.stderr and 'unrecognized' not in r.stderr:
                derived.append(f)
        if derived != fl['c']:
            fail('C5 flags-c.json differs from the set the built C driver accepts: %s' % sorted(set(derived) ^ set(fl['c'])))
        r = run([gcc] + fl['c'] + ['-O0', '-c', '-x', 'c', '/dev/null', '-o', '/dev/null'])
        if r.returncode != 0:
            fail('C5 the pack\'s own C driver rejects its C set: ' + r.stderr.strip()[-300:])
        man['flags'] = fl
        man['guest_flags_commit'] = flags_commit
        man['guest_flags_sha256'] = {l: hashlib.sha256(open('%s/flags-%s.json' % (flags_dir, l), 'rb').read()).hexdigest() for l in fl}
    man['libnx'] = LIBNX_INFO
    man['SEMANTICS_FLAGS'] = ' '.join(man['flags']['c++']) if flags_dir else man['ilp32_flags']
    man['sizeof_wchar_t'], man['sizeof_size_t'], man['sizeof_long'] = (man['sizeof'][k] for k in ('wchar_t', 'size_t', 'long'))
    open(prefix + '/MANIFEST.json', 'w').write(json.dumps(man, indent=1, sort_keys=True) + '\n')
    print('C5 ok: manifest written: %s' % json.dumps(man, sort_keys=True))


LIBNX_INFO = {}
WRITES_OK = ('str', 'stp', 'strb', 'strh', 'stur', 'sturb', 'sturh', 'stlr', 'cmp', 'cmn', 'tst')   # the instructions that only READ their register operands


def c8(prefix):
    """libnx is built by the game's compiler (stage 3): libnx.a is in the ILP32 pic multilib dir, its headers are installed, and no member writes
    x19..x27 (the guest's registers: never saved, never clobbered; C code cannot, -ffixed-x19..x27; the asm is judged on the objects). A mention of
    x19..x27 is allowed only as the source of a store, a compare or a test (the exception dump reads them). The ELF32 class of every member is C7's."""
    d = prefix + '/%s/lib/ilp32/wchar16/schar/pic' % TARGET
    lib = d + '/libnx.a'
    if not os.path.isfile(lib) or not os.path.isfile(prefix + '/%s/include/switch.h' % TARGET):
        fail('C8 libnx.a (%s) or switch.h is not in the mirror' % lib)
        return
    objdump = prefix + '/bin/%s-objdump' % TARGET
    members = run([prefix + '/bin/%s-ar' % TARGET, 't', lib]).stdout.split()
    bad, seen = [], 0
    reg = re.compile(r'\b[xw](?:19|2[0-7])\b')
    cur = ''
    for ln in run([objdump, '-d', '--no-show-raw-insn', lib]).stdout.splitlines():
        m = re.match(r'^(\S+\.o):\s+file format', ln)
        if m:
            cur = m.group(1)
            continue
        m = re.match(r'^\s*[0-9a-f]+:\s+(\S+)\s+(.*)$', ln)
        if not m or not reg.search(m.group(2)):
            continue
        seen += 1
        op = m.group(1)
        ops = m.group(2).split('//')[0]
        first = re.split(r',(?![^\[]*\])', ops)   # operands, a [..] address kept whole
        regops = [o for o in first[:2 if op in ('stp',) else 1]]
        in_addr = any(reg.search(o) for o in first if '[' in o)
        if in_addr or op not in WRITES_OK or any(reg.search(o) for o in first[2 if op == 'stp' else 1:] if '[' not in o and op not in ('cmp', 'cmn', 'tst')):
            bad.append('%s: %s %s' % (cur, op, ops.strip()))
    for x in bad[:20]:
        fail('C8 libnx writes or addresses through x19..x27: ' + x)
    LIBNX_INFO.update({'archive': '%s/lib/ilp32/wchar16/schar/pic/libnx.a' % TARGET, 'members': len(members), 'x19_x27_mentions_checked': seen,
                       'source_tree_sha256': os.environ.get('LIBNX_TREE_SHA256', '')})
    print('C8 ok: libnx.a has %d members, %d instructions mention x19..x27 (a store source, a compare or a test is allowed), %d violations' % (len(members), seen, len(bad)))


def dwarf_layout(obj, inc):
    """{(tag, name): (size, ((member, offset, member size), ...))} of every struct and union declared under INC, from the compiler's own DWARF."""
    from elftools.elf.elffile import ELFFile
    from elftools.elf.relocation import RelocationHandler
    # the ILP32 object's DWARF relocations are R_AARCH64_P32_ABS32 (1): the same 32-bit S + A as R_AARCH64_ABS32 (258)
    RelocationHandler._RELOCATION_RECIPES_AARCH64.setdefault(1, RelocationHandler._RELOCATION_RECIPES_AARCH64[258])
    out = {}
    with open(obj, 'rb') as f:
        dw = ELFFile(f).get_dwarf_info()
        for cu in dw.iter_CUs():
            dies = {d.offset: d for d in cu.iter_DIEs()}
            nm = lambda d: d.attributes['DW_AT_name'].value.decode() if 'DW_AT_name' in d.attributes else None  # noqa: E731

            def ref(d):
                t = d.attributes.get('DW_AT_type')
                return dies.get(t.value + cu.cu_offset) if t else None

            def size(d, depth=0):
                if d is None or depth > 16:
                    return 0
                if d.tag in ('DW_TAG_typedef', 'DW_TAG_const_type', 'DW_TAG_volatile_type', 'DW_TAG_restrict_type', 'DW_TAG_atomic_type'):
                    return size(ref(d), depth + 1)
                if 'DW_AT_byte_size' in d.attributes:
                    return d.attributes['DW_AT_byte_size'].value
                if d.tag == 'DW_TAG_array_type':
                    n = 1
                    for c in d.iter_children():
                        if c.tag == 'DW_TAG_subrange_type':
                            n *= c.attributes['DW_AT_upper_bound'].value + 1 if 'DW_AT_upper_bound' in c.attributes else 0
                    return n * size(ref(d), depth + 1)
                return 0
            lp = dw.line_program_for_CU(cu)
            files = {}
            if lp:
                v5 = lp['version'] >= 5
                dirs = lp['include_directory']
                for i, e in enumerate(lp['file_entry']):
                    di = e.dir_index
                    dn = dirs[di - (0 if v5 else 1)] if (di or v5) else b''
                    dn = dn.decode() if isinstance(dn, bytes) else dn
                    fn = e.name.decode() if isinstance(e.name, bytes) else e.name
                    files[i + (0 if v5 else 1)] = os.path.join(dn, fn)
            for d in dies.values():
                if d.tag not in ('DW_TAG_structure_type', 'DW_TAG_union_type') or not nm(d) or 'DW_AT_byte_size' not in d.attributes:
                    continue
                df = d.attributes.get('DW_AT_decl_file')
                if not df or not files.get(df.value, '').startswith(inc):
                    continue
                out[(d.tag, nm(d))] = (d.attributes['DW_AT_byte_size'].value,
                                       tuple((nm(c), c.attributes['DW_AT_data_member_location'].value if 'DW_AT_data_member_location' in c.attributes else 0,
                                              size(ref(c))) for c in d.iter_children() if c.tag == 'DW_TAG_member'))
    return out


DATA_MODEL_WARNINGS = ('-Wpointer-to-int-cast', '-Wint-to-pointer-cast', '-Wshift-count-overflow', '-Woverflow', '-Wincompatible-pointer-types')


def c9(prefix, s2, libnx_inc=None, warn_log=None):
    """The data-model class of libnx (written for LP64, built ILP32). Reports, never decides: (1) the libnx compile's warnings by -W option, every
    -Wint-to-pointer-cast, -Wpointer-to-int-cast, -Wint-conversion, -Wincompatible-pointer-types, -Wshift-count-overflow and -Woverflow as file:line
    (unique); (2) the struct and union types of switch.h's closure whose sizeof or member offsets/sizes differ between the same compiler's
    -mabi=ilp32 and LP64 compile (from the compiler's DWARF). The list goes to MANIFEST.json (man['libnx_data_model'])."""
    inc = libnx_inc or (prefix + '/%s/include' % TARGET)
    warn_log = warn_log or os.environ.get('LIBNX_WARN_LOG')
    res = {'warnings': {}, 'findings': {}, 'layout': {}}
    if warn_log and os.path.isfile(warn_log):
        seen = {}
        for ln in open(warn_log, errors='replace'):
            m = re.match(r'^(\S+?):(\d+):\d+: warning: (.*) \[(-W[^\]]*)\]$', ln.rstrip('\n'))
            if m:
                f = re.sub(r'(?:[^/\s]+/\.\./)+', '', m.group(1))   # include/switch/services/../sf/hipc.h and the macro-expansion paths are one file
                seen.setdefault((m.group(4), f, int(m.group(2))), m.group(3))
        for (opt, f, l), msg in sorted(seen.items()):
            res['warnings'][opt] = res['warnings'].get(opt, 0) + 1
            if opt in ('-Wint-to-pointer-cast', '-Wpointer-to-int-cast', '-Wint-conversion', '-Wincompatible-pointer-types', '-Wshift-count-overflow', '-Woverflow'):
                res['findings'].setdefault(opt, []).append('%s:%d' % (f, l))
    gcc = (s2 or prefix) + '/bin/%s-gcc' % TARGET
    lay = {}
    with tempfile.TemporaryDirectory() as t:
        open(t + '/all.c', 'w').write('#include <switch.h>\n')
        for tag, extra in (('ilp32', ['-mabi=ilp32', '-fshort-wchar', '-fsigned-char']), ('lp64', ['-mabi=lp64'])):
            r = run([gcc] + extra + ['-march=armv8-a+crc+crypto', '-D__SWITCH__', '-I' + inc, '-I' + inc + '/switch', '-x', 'c', '-std=gnu11', '-g',
                                    '-fno-eliminate-unused-debug-types', '-c', t + '/all.c', '-o', '%s/%s.o' % (t, tag)])
            if r.returncode != 0:
                fail('C9 <switch.h> does not compile (%s): %s' % (tag, r.stderr.strip()[-300:]))
                return
            lay[tag] = dwarf_layout('%s/%s.o' % (t, tag), inc)
    a, b = lay['ilp32'], lay['lp64']
    diff = []
    for k in sorted(set(a) & set(b)):
        if a[k] != b[k]:
            moved = [m[0] for m, n in zip(a[k][1], b[k][1]) if m != n]
            diff.append('%s %s: sizeof %d (ILP32) vs %d (LP64), members that differ: %s' % (k[0][7:], k[1], a[k][0], b[k][0], ','.join(str(x) for x in moved) or '-'))
    res['layout'] = {'types': len(set(a) & set(b)), 'differ': len(diff), 'list': diff}
    LIBNX_INFO['data_model'] = res
    print('C9: libnx warnings by option %s; %d findings; %d struct/union types of switch.h compared, %d differ between ILP32 and LP64'
          % (json.dumps(res['warnings'], sort_keys=True), sum(len(v) for v in res['findings'].values()), res['layout']['types'], len(diff)))
    # the five data-model classes must be empty (they are also -Werror= of the guest semantics set, so the libnx compile itself stops on one)
    bad = {o: n for o, n in res['warnings'].items() if o in DATA_MODEL_WARNINGS and n}
    if bad:
        fail('C9 libnx compiles with data-model warnings: %s' % json.dumps(bad, sort_keys=True))



SIZED = '''#include <new>
void f(void *p, std::size_t n, std::align_val_t a) {
    ::operator delete(p, n); ::operator delete[](p, n); ::operator delete(p, n, a); ::operator delete[](p, n, a);
}
'''
UNSIZED = '''#include <new>
void f(void *p, std::align_val_t a) { ::operator delete(p); ::operator delete[](p); ::operator delete(p, a); ::operator delete[](p, a); }
'''


def derive(prefix, s2, src):
    """The deallocation functions a call set refers to, mangled by the built compiler (undefined symbols of the probe)."""
    gxx = (s2 or prefix) + '/bin/%s-g++' % TARGET
    with tempfile.TemporaryDirectory() as t:
        open(t + '/p.cc', 'w').write(src)
        r = run([gxx] + FLAGS + ['-fsized-deallocation', '-O0', '-c', t + '/p.cc', '-o', t + '/p.o'])
        if r.returncode != 0:
            fail('C6 the probe does not compile: ' + r.stderr.strip()[-300:])
            return []
        nm = run([prefix + '/bin/%s-nm' % TARGET, '-u', '--format=posix', t + '/p.o']).stdout
        return sorted(l.split()[0] for l in nm.splitlines() if l.split() and l.split()[0].startswith(('_Zdl', '_Zda')))


def libs(prefix):
    for d in (prefix + '/%s/lib' % TARGET, prefix + '/lib/gcc/%s' % TARGET):
        for root, _, files in os.walk(d):
            for f in sorted(files):
                if f.endswith(('.a', '.o')):
                    yield os.path.join(root, f)


def c6(prefix, s2):
    sized, unsized = derive(prefix, s2, SIZED), derive(prefix, s2, UNSIZED)
    if len(sized) != 4 or len(unsized) != 4:
        fail('C6 derived %d sized and %d unsized deallocation symbols, expected 4 and 4: %s %s' % (len(sized), len(unsized), sized, unsized))
        return
    fwd = {z: z.replace('Pvj', 'Pv') for z in sized}    # ILP32 size_t is 'j': the sized form minus its size parameter
    if sorted(fwd.values()) != unsized:
        fail('C6 sized %s do not map onto unsized %s' % (sized, unsized))
        return
    nm, od, ar = (prefix + '/bin/%s-%s' % (TARGET, x) for x in ('nm', 'objdump', 'ar'))
    nlibs = nref = ndef = 0
    for lib in libs(prefix):
        nlibs += 1
        for ln in run([nm, '-A', '--format=posix', lib]).stdout.splitlines():
            f = ln.split()
            if len(f) < 3 or f[1] not in fwd:
                continue
            if f[2] in 'Uu':
                nref += 1
                fail('C6 %s refers to the sized deallocation function %s' % (f[0].rstrip(':'), f[1]))
            else:
                ndef += 1
                m = re.match(r'(.*)\[(.*)\]:$', f[0])
                with tempfile.TemporaryDirectory() as t:
                    obj = lib if not m else t + '/' + m.group(2)
                    if m:
                        run([ar, 'x', lib, m.group(2)], cwd=t)
                    rel = run([od, '-dr', obj]).stdout
                if not re.search(r'R_AARCH64_\w+\s+%s\b' % re.escape(fwd[f[1]]), rel):
                    fail('C6 the definition of %s in %s does not call %s' % (f[1], f[0].rstrip(':'), fwd[f[1]]))
    print('C6 ok: %d library files scanned, %d references to the %d sized deallocation functions (%s), %d definitions, all forwarding'
          % (nlibs, nref, len(sized), ' '.join(sized), ndef))


def c7(prefix, gcc13):
    base = prefix + '/%s/include/c++' % TARGET
    if sorted(os.listdir(base)) != [gcc13]:
        fail('C7 c++ include versions %s, expected [%s]' % (sorted(os.listdir(base)), gcc13))
    re_ = prefix + '/bin/%s-readelf' % TARGET
    nlibs = nobj = 0
    for lib in libs(prefix):
        nlibs += 1
        out = run([re_, '-h', lib]).stdout
        classes = re.findall(r'^\s*Class:\s+(\S+)', out, re.M)
        machs = re.findall(r'^\s*Machine:\s+(.+?)\s*$', out, re.M)
        flags = re.findall(r'^\s*Flags:\s+(.+?)\s*$', out, re.M)
        nobj += len(classes)
        bad = sorted({c for c in classes if c != 'ELF32'}) + sorted({m for m in machs if 'AArch64' not in m})
        if bad:
            fail('C7 %s: ELF objects that are not ILP32 AArch64: %s' % (lib, bad))
    print('C7 ok: c++ include version %s only; %d library files, %d ELF objects, all ELF32 AArch64' % (gcc13, nlibs, nobj))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('prefix')
    ap.add_argument('--s2')
    ap.add_argument('--gcc13', default='13.3.0')
    ap.add_argument('--manifest', action='store_true')
    ap.add_argument('--flags-dir', help='directory of flags-{c,c++,asm}.json from verify/guest_flags.py')
    ap.add_argument('--flags-commit', default='')
    a = ap.parse_args()
    prefix = a.prefix.rstrip('/')
    here = os.path.dirname(os.path.abspath(__file__))
    c1(prefix, a.s2)
    c2(prefix, a.s2, a.gcc13)
    c3(prefix, a.s2)
    c4(prefix, a.gcc13)
    c6(prefix, a.s2)
    c7(prefix, a.gcc13)
    c8(prefix)
    c9(prefix, a.s2)
    if a.manifest:
        c5(prefix, a.s2, a.gcc13, here, a.flags_dir, a.flags_commit)
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
