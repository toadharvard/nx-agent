#!/usr/bin/env python3
"""undef.py - sort the guest link's still-undefined symbols into categories with the stream that owns them.

    tools/pipeline/undef.py <undefined.txt> [--addrmap tsv] [--units units.json] [--objects objects.tsv]
                            [-o undefined_by_category.tsv]

undefined.txt: one (mangled) name per line, as the guest link writes it. The other inputs refine the address
symbols `__fnv_<addr>`: whose unit defines the address (units.json functions) and whether that unit compiled
(objects.tsv), S4's CRT/helper classification (platform/crt/crt_symbols.tsv) and Win32 imports
(platform/win32/imports.tsv). Output TSV: name, category, detail. Prints the counts.
"""
import collections
import json
import os
import re
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
DATA_START, DATA_END = 0x00fdf000, 0x02100000

# key -> (label, owner)
CATEGORIES = collections.OrderedDict([
    ('code_uncompiled', ('image code address: its unit does not compile', 'S1-compile')),
    ('code_notemitted', ('image code address: unit compiled, definition not emitted (inlined/unmatched)', 'S1/S2')),
    ('code_crt', ('image code address with no unit: CRT/C++ library/helper (crt_symbols.tsv)', 'S4-runtime')),
    ('code_eh', ('image code address with no unit: EH funclet / atexit dtor (GCC emits its own)', 'S2/S4')),
    ('code_nobody', ('image code address with no unit: function without a body in the decomp', 'S4/S1')),
    ('code_unknown', ('image code address with no unit, unclassified', 'S2/S4')),
    ('data_addr', ('image data address not absolute (should come from aliases.ld)', 'S2-link')),
    ('bridge', ('ABI bridge symbol not generated', 'S2-link')),
    ('vtable_typeinfo', ('C++ vtable / typeinfo / VTT: key function not compiled or never defined', 'S1/S2b')),
    ('cxx_thunk', ('C++ virtual / covariant thunk', 'S1/S2b')),
    ('cxx_ctor_dtor', ('C++ constructor / destructor by name (implicit ctor/dtor calls)', 'S1/S2')),
    ('cxx_stdlib', ('C++ standard library (libstdc++ ILP32 not linked or MSVC STL names)', 'S5-toolchain')),
    ('cxx_by_name', ('C++ function / variable by name, no definition', 'S1/S2')),
    ('ghidra_name', ('Ghidra placeholder name (FUN_/DAT_/PTR_/g_ + address) not bound by address', 'S2-link')),
    ('win32_import', ('Win32 / DLL import by name', 'S4-runtime')),
    ('crt_msvc', ('MSVC CRT / compiler helper by name', 'S4-runtime')),
    ('libc_glibc', ('glibc-only libc symbol (unit compiled against glibc headers)', 'S9 (GAME_CXX)')),
    ('libc', ('C library / libm / libgcc symbol', 'S5-toolchain')),
    ('os_backend', ('platform/os backend function (no guest OS backend in the link)', 'S5/S4')),
    ('fnvnx_platform', ('fnvnx_* platform / runtime glue', 'S4/S5')),
    ('d3d', ('Direct3D 9 / D3DX', 'S6')),
    ('audio_input', ('DirectSound / DirectInput / XInput / vorbisfile', 'S7')),
    ('steam', ('Steam API', 'S4-runtime')),
    ('sse', ('SSE intrinsic / builtin', 'S8/S1')),
    ('c_by_name', ('C-linkage / plain name (decomp helper or global) defined by no compiled unit', 'S1/S2')),
])
WS2 = re.compile(r'^_?(closesocket|socket|connect|send|sendto|recv|recvfrom|bind|listen|accept|select|shutdown|'
                 r'setsockopt|getsockopt|ioctlsocket|htons|htonl|ntohs|ntohl|inet_addr|inet_ntoa|gethostbyname|'
                 r'gethostname|getpeername|getsockname|__WSAFDIsSet|WSA\w+)(@\d+)?$')
MSVC_CRT = re.compile(
    r'^_?_?(CI\w+|ftol\w*|alloca_probe\w*|chkstk|security_\w+|SEH_\w+|EH_\w+|CxxThrowException|'
    r'CxxFrameHandler\w*|RTDynamicCast|RTtypeid|RTCastToVoid|purecall|onexit|initterm\w*|doexit|'
    r'(w)?(str|mem|mbs|wcs)\w*_s|i(64)?toa\w*|ui64toa|atoi64|snprintf|vsnprintf|stricmp|strnicmp|mbsnicmp|mbsicmp|'
    r'strlwr|strupr|wcsicmp|wcsnicmp|controlfp\w*|fpreset|clearfp|statusfp|finite|isnan|hypot|time64|localtime64|'
    r'gmtime64|mktime64|ftime64|f\w+_nolock|fopen_s|fread_s|sprintf_s|set_se_translator|invalid_parameter\w*|'
    r'crt\w+|getmbcp|mbctype|iob_func|ReadWriteBarrier|ReadBarrier|WriteBarrier|byteswap_\w+|rotl\w*|rotr\w*|'
    r'BitScan\w+|InterlockedCompareExchange\w*|beginthreadex|endthreadex|errno|get_errno|set_errno)$')

LIBC = set('''abort abs acos asin atan atan2 atexit atof atoi atol bsearch calloc ceil clock cos cosh exit exp fabs
fclose feof ferror fflush fgetc fgets floor fmod fopen fprintf fputc fputs fread free freopen frexp fscanf fseek ftell
fwrite getc getchar getenv gmtime isalnum isalpha isdigit islower isspace isupper ldexp localtime log log10 longjmp
malloc memchr memcmp memcpy memmove memset mktime modf perror pow printf putc putchar puts qsort raise rand realloc
remove rename rewind scanf setjmp setvbuf sin sinh sprintf sqrt srand sscanf strcat strchr strcmp strcpy strcspn strerror
strftime strlen strncat strncmp strncpy strpbrk strrchr strspn strstr strtod strtok strtol strtoul system tan tanh time
tmpfile tolower toupper ungetc vfprintf vprintf vsprintf vsnprintf snprintf wcscpy wcslen wcscmp wcsncpy towlower
towupper iswspace iswalpha iswdigit mbstowcs wcstombs sqrtf sinf cosf tanf atan2f acosf asinf atanf expf logf powf
floorf ceilf fmodf fabsf log10f ldexpf frexpf modff roundf lroundf truncf strtof strtoll strtoull llabs labs div ldiv
memalign posix_memalign aligned_alloc strdup strndup strnlen getpid nanosleep usleep sleep clock_gettime gettimeofday
__errno __assert_func _impure_ptr __getreent _ctype_ __locale_ctype_ptr'''.split())


def _table(name):
    """a decomp-derived table of fnv-nx-build (tools/tables.py: FNVNX_TABLES, sha256 against tools/tables.pin.json)"""
    import importlib.util
    s = importlib.util.spec_from_file_location('fnvnx_tables', os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tables.py'))
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m.path(name)


def load_crt():
    m = {}
    p = _table('crt_symbols.tsv')
    if os.path.isfile(p):
        for line in open(p, errors='replace'):
            if line.startswith('#'):
                continue
            f = line.rstrip('\n').split('\t')
            if len(f) > 1 and f[0].startswith('0x'):
                m[int(f[0], 16)] = f[1]
    return m


def load_imports():
    s = set()
    p = _table('imports.tsv')
    if os.path.isfile(p):
        for line in open(p, errors='replace'):
            if line.startswith('#'):
                continue
            f = line.rstrip('\n').split('\t')
            if len(f) > 2:
                s.add(f[2])
    return s


def load_units(units_json, objects_tsv):
    owner, compiled = {}, {}
    if units_json and os.path.isfile(units_json):
        for u in json.load(open(units_json))['units']:
            for f in u.get('functions', []):
                try:
                    owner.setdefault(int(f['address'], 16), u['source'])
                except (KeyError, ValueError, TypeError):
                    pass
    if objects_tsv and os.path.isfile(objects_tsv):
        for line in open(objects_tsv, errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')
            if len(f) >= 3:
                compiled[f[0]] = f[2] == 'ok'
    return owner, compiled


def load_addrmap(path):
    kinds = {}
    if path and os.path.isfile(path):
        for line in open(path, errors='replace'):
            if line.startswith('#'):
                continue
            f = line.split('\t')
            if len(f) > 1:
                try:
                    kinds[int(f[0], 16)] = f[1]
                except ValueError:
                    pass
    return kinds


ADDR = re.compile(r'^__fnv_([0-9a-f]{8})(?:_[CD]\d)?$')
GHIDRA = re.compile(r'^_?(?:FUN|DAT|PTR|UNK|g|LAB|SUB)_[0-9a-fA-F]{8}(?:@\d+)?$')


def classify(name, ctx):
    owner, compiled, crt, imports, amap = ctx
    m = ADDR.match(name)
    if m:
        a = int(m.group(1), 16)
        if DATA_START <= a < DATA_END:
            return 'data_addr', ''
        src = owner.get(a)
        if src is not None:
            if not compiled.get(src, False):
                return 'code_uncompiled', src
            return 'code_notemitted', src
        c = crt.get(a, '')
        if c in ('crt', 'cpplib', 'helper', 'crt_inline', 'thunk'):
            return 'code_crt', c
        if c in ('eh', 'atexit_dtor'):
            return 'code_eh', c
        if c in ('body_named', 'body_unnamed', 'mid_named', 'data', 'mid_data'):
            return 'code_nobody', c
        return 'code_unknown', amap.get(a, '')
    if name.startswith('__fnvnx_br_'):
        return 'bridge', ''
    if re.match(r'^_ZT[VITS]', name) or name.startswith('_ZTT'):
        return 'vtable_typeinfo', name[:4]
    if re.match(r'^_ZT[hvc]', name):
        return 'cxx_thunk', ''
    if re.match(r'^_Z(N[KVL]*)?St|^_ZSt|^_ZNKSt|^_ZNSt|^_ZN9__gnu_cxx|^_ZN10__cxxabiv', name):
        return 'cxx_stdlib', ''
    if name.startswith('_Z'):
        if re.search(r'[CD][0-5]E', name):
            return 'cxx_ctor_dtor', ''
        return 'cxx_by_name', ''
    plain = re.sub(r'@\d+$', '', name)
    plain = plain[6:] if plain.startswith('__imp_') else plain
    base = plain.lstrip('_')
    if GHIDRA.match(name):
        return 'ghidra_name', ''
    if name.startswith('__imp_') or base in imports or plain in imports or WS2.match(name):
        return 'win32_import', ''
    if re.match(r'^(__isoc\d+_|__errno_location$|__assert_fail$|__\w+_chk$|__stack_chk_|__ctype_|__libc_)', name):
        return 'libc_glibc', ''
    if re.match(r'^(fnvnx_)?os_\w+$', name):
        return 'os_backend', ''
    if name.startswith(('fnvnx_', '__fnvnx_')):
        return 'fnvnx_platform', ''
    if re.match(r'^_?(D3DX|Direct3D|D3DPERF|D3D9)', name):
        return 'd3d', ''
    if re.match(r'^_?(DirectSound|DirectInput|XInput|ov_|vorbis_|ogg_|Bink)', name):
        return 'audio_input', ''
    if re.match(r'^_?Steam', name):
        return 'steam', ''
    if re.match(r'^_?_mm_|^__builtin', name):
        return 'sse', ''
    if plain in LIBC or re.match(r'^__(aeabi|aarch64|gcc|cxa|dso_handle|udiv|mul|div)', name):
        return 'libc', ''
    if base in LIBC or MSVC_CRT.match(plain):      # `_memcpy`: the cdecl-decorated (MSVC) spelling of a CRT name
        return 'crt_msvc', 'msvc-spelling' if base in LIBC else ''
    return 'c_by_name', ''


def categorize(names, addrmap=None, units_json=None, objects_tsv=None):
    owner, compiled = load_units(units_json, objects_tsv)
    ctx = (owner, compiled, load_crt(), load_imports(), load_addrmap(addrmap))
    rows = [(n,) + classify(n, ctx) for n in names]
    counts = collections.Counter(r[1] for r in rows)
    examples = collections.defaultdict(list)
    for n, k, det in rows:
        if len(examples[k]) < 3:
            examples[k].append(n)
    cats = []
    for k, (label, owner_s) in CATEGORIES.items():
        if counts.get(k):
            cats.append({'key': k, 'label': label, 'owner': owner_s, 'count': counts[k], 'examples': examples[k]})
    cats.sort(key=lambda c: -c['count'])
    return rows, cats


def categorize_file(path, addrmap=None, units_json=None, objects_tsv=None, out_tsv=None):
    names = [l.strip() for l in open(path, errors='surrogateescape') if l.strip()]
    rows, cats = categorize(names, addrmap, units_json, objects_tsv)
    if out_tsv:
        with open(out_tsv, 'w', errors='surrogateescape') as f:
            f.write('# name\tcategory\tdetail\n')
            for r in sorted(rows, key=lambda r: (r[1], r[0])):
                f.write('%s\t%s\t%s\n' % r)
    return cats


def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 2
    opts = {'--addrmap': None, '--units': None, '--objects': None, '-o': None}
    pos = []
    it = iter(argv)
    for a in it:
        if a in opts:
            opts[a] = next(it)
        else:
            pos.append(a)
    cats = categorize_file(pos[0], opts['--addrmap'], opts['--units'], opts['--objects'], opts['-o'])
    total = sum(c['count'] for c in cats)
    print('undefined symbols: %d' % total)
    for c in cats:
        print('  %7d  %-16s %-14s %s' % (c['count'], c['key'], c['owner'], c['label']))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
