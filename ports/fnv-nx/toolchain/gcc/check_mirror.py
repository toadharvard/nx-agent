#!/usr/bin/env python3
"""check_mirror.py - the checks of the fnvnx-a64 mirror (toolchain/gcc/build.sh header: the class rule).

  check_mirror.py <prefix> [--s2 <stage 2 prefix>] [--gcc13 13.3.0] [--manifest] [--libnx <libnx checkout>]

The rule: every ILP32 runtime library a game unit links comes from the unit's own compiler (GCC 13.3.0 + msvcmodel.patch)
with the unit's data-model flags -mabi=ilp32 -fshort-wchar -fsigned-char. Checks (any failure exits 1):
  C1  every multilib entry of the shipped driver (and of stage 2's) with -mabi=ilp32 carries -fshort-wchar and -fsigned-char;
      the only ILP32 library directories in the mirror are ilp32/wchar16/schar and ilp32/wchar16/schar/pic.
  C2  selection: -mabi=ilp32 alone resolves to the LP64 directory and a link with it fails; the full flag set resolves
      to ilp32/wchar16/schar.
  C3  wide characters: for every ILP32 variant the target preprocessor says __SIZEOF_WCHAR_T__ 2, __WCHAR_MAX__ 0xffff and
      no __CHAR_UNSIGNED__; wcslen and the other wide functions of the ILP32 libc.a use 2-byte accesses (ldrh/strh), the
      LP64 ones 4-byte accesses.
  C4  the libstdc++ headers of the ILP32 variant are 13.3 (_GLIBCXX_RELEASE 13, 20240xxx); no c++/15.2.0 ILP32 headers exist.
  C5  (--manifest) MANIFEST.json: gcc version, msvcmodel.patch sha256, flags, sizeof/alignof of wchar_t, size_t, long,
      long double, pointer, read from the game compiler's object code (no run).
  C6  (--libnx) libnx is built by stage 1 (flags without -fshort-wchar/-fsigned-char). Its interface carries no type whose
      size or layout depends on them: every named type of the libnx headers has the same layout under both flag sets, no
      libnx declaration (type, member, function prototype) names wchar_t. Reports the numbers checked.
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
    if d1 != '.':
        fail('C2 -mabi=ilp32 alone resolves to "%s", not the LP64 directory' % d1)
    if d2 != 'ilp32/wchar16/schar':
        fail('C2 full ILP32 flags resolve to "%s"' % d2)
    with tempfile.TemporaryDirectory() as t:
        open(t + '/m.c', 'w').write('int main(void){return 0;}\n')
        r = run([gcc] + mir + ['-mabi=ilp32', t + '/m.c', '-o', t + '/m.elf'])
        if r.returncode == 0:
            fail('C2 a link of -mabi=ilp32 without -fshort-wchar succeeded')
        r = run([gcc] + mir + FLAGS + [t + '/m.c', '-o', t + '/m.elf', '-nostartfiles', '-Wl,-e,main'])
        if r.returncode != 0:
            fail('C2 a link with the full ILP32 flags failed: ' + r.stderr.strip()[-300:])
    print('C2 ok: -mabi=ilp32 -> "%s" (link fails), full flags -> "%s"' % (d1, d2))


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
    n = 0
    for m in WIDE:
        member = m
        a32 = member_asm(prefix, prefix + '/%s/lib/ilp32/wchar16/schar/libc.a' % TARGET, member)
        a64 = member_asm(prefix, prefix + '/%s/lib/libc.a' % TARGET, member)
        if a32 is None or a64 is None:
            fail('C3 %s: not exactly one member (lib|libc)_a-%s.o in the ILP32 and LP64 libc.a' % (m, m))
            continue
        if not re.search(r'\b(ldrh|strh)\b', a32):
            fail('C3 %s: the ILP32 object has no 2-byte access' % m)
        if re.search(r'\b(ldrh|strh)\b', a64):
            fail('C3 %s: the LP64 object has a 2-byte access' % m)
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
            '#include <stddef.h>\nconst unsigned char fnvnx_probe[] = {sizeof(wchar_t), _Alignof(wchar_t), sizeof(size_t), '
            '_Alignof(size_t), sizeof(long), _Alignof(long), sizeof(long double), _Alignof(long double), sizeof(void *), '
            '_Alignof(void *)};\n')
        r = run([gcc] + FLAGS + ['-O0', '-c', t + '/p.c', '-o', t + '/p.o'])
        if r.returncode != 0:
            fail('C5 probe does not compile: ' + r.stderr.strip()[-300:])
            return
        dump = run([prefix + '/bin/%s-objdump' % TARGET, '-s', '-j', '.rodata', t + '/p.o']).stdout
        hexs = ''.join(re.findall(r'^ [0-9a-f]{4} ((?:[0-9a-f]{2,8} ?)+)', dump, re.M)).replace(' ', '')[:20]
        vals = [int(hexs[i:i + 2], 16) for i in range(0, 20, 2)]
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
    man['SEMANTICS_FLAGS'] = ' '.join(man['flags']['c++']) if flags_dir else man['ilp32_flags']
    man['sizeof_wchar_t'], man['sizeof_size_t'], man['sizeof_long'] = (man['sizeof'][k] for k in ('wchar_t', 'size_t', 'long'))
    open(prefix + '/MANIFEST.json', 'w').write(json.dumps(man, indent=1, sort_keys=True) + '\n')
    print('C5 ok: manifest written: %s' % json.dumps(man, sort_keys=True))


def dwarf_layout(obj, libnx_inc):
    from elftools.elf.elffile import ELFFile
    out, uses_wchar = {}, set()
    with open(obj, 'rb') as f:
        dw = ELFFile(f).get_dwarf_info()
        for cu in dw.iter_CUs():
            dies = {d.offset: d for d in cu.iter_DIEs()}

            def name(d):
                a = d.attributes.get('DW_AT_name')
                return a.value.decode() if a else None

            def sig(d, depth=0):
                """resolved layout of a type DIE: size and, for aggregates, member offsets and member layouts"""
                if d is None:
                    return ('void',)
                tag = d.tag
                if depth > 8:
                    return (tag, name(d))
                t = d.attributes.get('DW_AT_type')
                tgt = dies.get(t.value + cu.cu_offset) if t else None
                size = d.attributes['DW_AT_byte_size'].value if 'DW_AT_byte_size' in d.attributes else None
                if tag in ('DW_TAG_typedef', 'DW_TAG_const_type', 'DW_TAG_volatile_type', 'DW_TAG_restrict_type', 'DW_TAG_atomic_type'):
                    return sig(tgt, depth + 1)
                if tag == 'DW_TAG_pointer_type':
                    return ('ptr', size)
                if tag == 'DW_TAG_array_type':
                    cnt = tuple(c.attributes['DW_AT_upper_bound'].value if 'DW_AT_upper_bound' in c.attributes else None
                                for c in d.iter_children() if c.tag == 'DW_TAG_subrange_type')
                    return ('array', cnt, sig(tgt, depth + 1))
                if tag in ('DW_TAG_structure_type', 'DW_TAG_union_type'):
                    mem = tuple((name(c), c.attributes['DW_AT_data_member_location'].value
                                 if 'DW_AT_data_member_location' in c.attributes else 0,
                                 sig(dies.get(c.attributes['DW_AT_type'].value + cu.cu_offset), depth + 1))
                                for c in d.iter_children() if c.tag == 'DW_TAG_member')
                    return (tag, size, mem)
                if tag == 'DW_TAG_base_type':
                    return ('base', name(d), size)       # signedness (encoding) is not layout: not part of the signature
                return (tag, size)

            def chain_has_wchar(d, depth=0):
                if d is None or depth > 12:
                    return False
                if d.tag == 'DW_TAG_typedef' and name(d) == 'wchar_t':
                    return True
                t = d.attributes.get('DW_AT_type')
                if t and chain_has_wchar(dies.get(t.value + cu.cu_offset), depth + 1):
                    return True
                return any(chain_has_wchar(dies.get(c.attributes['DW_AT_type'].value + cu.cu_offset), depth + 1)
                           for c in d.iter_children() if c.tag == 'DW_TAG_member' and 'DW_AT_type' in c.attributes)
            files = {}
            lp = dw.line_program_for_CU(cu)
            if lp:
                for i, e in enumerate(lp['file_entry']):
                    files[i + (0 if lp['version'] >= 5 else 1)] = e.name.decode() if isinstance(e.name, bytes) else e.name
                dirs = lp['include_directory']
                for i, e in enumerate(lp['file_entry']):
                    di = e.dir_index
                    dn = dirs[di - (0 if lp['version'] >= 5 else 1)] if (di or lp['version'] >= 5) else b''
                    dn = dn.decode() if isinstance(dn, bytes) else dn
                    fn = e.name.decode() if isinstance(e.name, bytes) else e.name
                    files[i + (0 if lp['version'] >= 5 else 1)] = os.path.join(dn, fn)
            for d in dies.values():
                if d.tag not in ('DW_TAG_typedef', 'DW_TAG_structure_type', 'DW_TAG_union_type', 'DW_TAG_enumeration_type'):
                    continue
                n = name(d)
                df = d.attributes.get('DW_AT_decl_file')
                path = files.get(df.value, '') if df else ''
                if not n or not path.startswith(libnx_inc):
                    continue
                key = (d.tag, n)
                out[key] = sig(d)
                if chain_has_wchar(d):
                    uses_wchar.add(key)
    return out, uses_wchar


def c6(prefix, libnx):
    inc = os.path.join(libnx, 'nx', 'include')
    gcc = prefix + '/bin/%s-gcc' % TARGET
    base = ['-mabi=ilp32', '-march=armv8-a+crc+crypto', '-mtune=cortex-a57', '-fPIE', '-ftls-model=local-exec', '-D__SWITCH__',
            '-I' + inc, '-x', 'c', '-std=gnu11', '-g', '-fno-eliminate-unused-debug-types', '-c']
    res = {}
    with tempfile.TemporaryDirectory() as t:
        open(t + '/all.c', 'w').write('#include <switch.h>\n')
        for tag, extra in (('A', []), ('B', ['-fshort-wchar', '-fsigned-char'])):
            aux = '%s/%s.aux' % (t, tag)
            r = run([gcc] + base + extra + ['-aux-info', aux, t + '/all.c', '-o', '%s/%s.o' % (t, tag)])
            if r.returncode != 0:
                fail('C6 <switch.h> does not compile under flag set %s: %s' % (tag, r.stderr.strip()[-300:]))
                return
            lay, wch = dwarf_layout('%s/%s.o' % (t, tag), inc)
            protos = [ln for ln in open(aux) if ('/' + inc.lstrip('/')) in ln or inc in ln]
            res[tag] = (lay, wch, protos)
    (la, wa, pa), (lb, wb, pb) = res['A'], res['B']
    bad = [k for k in sorted(set(la) | set(lb)) if la.get(k) != lb.get(k)]
    for k in bad:
        fail('C6 libnx type %s %s has a different layout under -fshort-wchar/-fsigned-char' % k)
    for k in sorted(wa | wb):
        fail('C6 libnx type %s %s carries wchar_t' % k)
    for ln in pa + pb:
        if re.search(r'\bwchar_t\b|\bwint_t\b', ln):
            fail('C6 a libnx prototype names wchar_t/wint_t: ' + ln.strip()[:160])
    funcs = len({re.search(r'(\w+)\s*\(', ln).group(1) for ln in pa if re.search(r'(\w+)\s*\(', ln)})
    print('C6: %d libnx types and %d function prototypes checked, %d type layout differences, %d use wchar_t'
          % (len(la), funcs, len(bad), len(wa | wb)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('prefix')
    ap.add_argument('--s2')
    ap.add_argument('--gcc13', default='13.3.0')
    ap.add_argument('--manifest', action='store_true')
    ap.add_argument('--flags-dir', help='directory of flags-{c,c++,asm}.json from verify/guest_flags.py')
    ap.add_argument('--flags-commit', default='')
    ap.add_argument('--libnx')
    a = ap.parse_args()
    prefix = a.prefix.rstrip('/')
    here = os.path.dirname(os.path.abspath(__file__))
    c1(prefix, a.s2)
    c2(prefix, a.s2, a.gcc13)
    c3(prefix, a.s2)
    c4(prefix, a.gcc13)
    if a.manifest:
        c5(prefix, a.s2, a.gcc13, here, a.flags_dir, a.flags_commit)
    if a.libnx:
        c6(prefix, a.libnx)
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
