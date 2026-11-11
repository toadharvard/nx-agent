#!/usr/bin/env python3
"""Synthetic exe + DLLs for loader/host/modcheck.c (rules M2..M4 of the module loader).

    synth_modules.py OUTDIR          writes OUTDIR/{exe.pe, c.dll, b.dll, a.dll, d.dll, e.dll, <variants>} and OUTDIR/synth_mod.h

World (module table order = initialisation order; every module is static unless noted):
  exe   base 0x400000, TLS (index 0, one callback), imports kernel32!Sleep, user32!#17, A.dll!afun and A.dll!#7, b.dll!#2,
        b.dll!bfwd (a forwarder to c), c.dll!cfun
  c.dll preferred = assigned base 0x520000 (no relocation applied), no entry point, no import and no relocation directory;
        exports cfun (ordinal 9), cdata (ordinal 10)
  b.dll preferred 0x30000000, assigned 0x510000 (relocated), entry point; exports bfun 1, (unnamed) 2, bdata 3, bfwd 4 ->
        "C.cfun", bfwdord 5 -> "c.#9"; imports c.dll!cfun, c.dll!#10, kernel32!Sleep
  a.dll preferred 0x400000 (collides with the exe), assigned 0x500000 (relocated: HIGHLOW words in three sections, ABSOLUTE
        padding and an explicit ABSOLUTE entry), TLS (template, zero fill, one callback), entry point; imports b by name
        (bfun, bfwd) and by ordinal (#2 unnamed, #3 named bdata), kernel32!Sleep; exports afun (ordinal 7)
  d.dll on demand, assigned 0x540000 (relocated), entry point, imports e.dll!efun, c.dll!cfun; exports dfun
  e.dll on demand, assigned 0x550000, no entry; exports efun
Variants (same layout, one defect each) are written as extra files; the header lists their SHA-256 and sizes."""
import hashlib, os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pebuild

TEXT, RDATA, DATA = 0x1000, 0x2000, 0x3000
RX, RO, RW = 0x60000020, 0x40000040, 0xC0000040
BASES = {'c.dll': 0x520000, 'b.dll': 0x510000, 'a.dll': 0x500000, 'd.dll': 0x540000, 'e.dll': 0x550000}
FILES = {}       # file name -> bytes
FACTS = {}       # image name -> dict


def skeleton(base, dll=True, entry=0, **kw):
    img = pebuild.Img(base, dll=dll, entry_rva=entry, **kw)
    img.section('.text', TEXT, 0x400, RX)
    img.section('.rdata', RDATA, 0x1000, RO)
    img.section('.data', DATA, 0x200, RW, vsize=0x1800)
    return img


def reloc_words(img):
    """HIGHLOW words in all three sections, plus an explicit ABSOLUTE entry"""
    img.va_word(DATA + 0x100, TEXT + 0x10)
    img.va_word(DATA + 0x104, DATA + 0x80)
    img.va_word(DATA + 0x1fc, RDATA + 0x40)         # last word of the raw data
    img.va_word(TEXT + 0x100, DATA + 0x100)
    img.va_word(RDATA + 0x7f0, TEXT + 0x20)
    img.reloc(DATA + 0x108, 0)                      # ABSOLUTE with an offset: padding by another name, nothing to apply


def finish(img, name, preferred, assigned, slots, exp_values, tls=None, bad_type=False, strip=False):
    data = img.build(reloc_rva=0x5000)
    FILES[name] = data
    hl = [(assigned + r) for r, t in sorted(img.relocs) if t == 3] if assigned != preferred else []
    FACTS[name] = dict(base=assigned, pref=preferred, entry=(assigned + img.entry_rva) if img.entry_rva else 0, slots=slots,
                       relocs=hl, tls=tls, soi=pebuild.page_up(max(s[1] + max(s[3], len(s[2])) for s in img.secs + ([['', 0x5000, bytearray(0x200), 0x200, 0]] if img.relocs else []))))


def build_exe():
    img = skeleton(0x400000, dll=False, entry=TEXT + 0x10)
    slots = img.imports(RDATA, [('KERNEL32.dll', ['Sleep']), ('USER32.dll', [17]), ('A.dll', ['afun', 7]), ('b.dll', [2, 'bfwd']),
                                ('c.dll', ['cfun'])])
    img.put(RDATA + 0x840, struct.pack('<II', 0x400000 + TEXT + 0x100, 0))
    img.tls(RDATA + 0x800, DATA, DATA + 0x20, DATA + 0x80, RDATA + 0x840, 0x10, 3 << 20)
    img.u32(DATA + 0x80, 0x12345678)
    FILES['exe.pe'] = img.build()
    FACTS['exe'] = dict(base=0x400000, pref=0x400000, entry=0x400000 + TEXT + 0x10, relocs=[], soi=0x4000 + 0x1000,
                        tls=dict(tmpl=0x400000 + DATA, tsize=0x20, zero=0x10, index_va=0x400000 + DATA + 0x80, index=0, align=4,
                                 cbs=[0x400000 + TEXT + 0x100]), slots=slots)


def build_c(variant=None):
    img = skeleton(0x520000, entry=0)
    img.exports(RDATA + 0x400, 'c.dll', [(9, 'cfun', TEXT + 0x20), (10, 'cdata', DATA + 0x20)])
    name = 'c.dll' if not variant else 'c_%s.dll' % variant
    data = img.build()
    if variant == 'long':
        data += b'\0'
    FILES[name] = data
    if not variant:
        FACTS['c.dll'] = dict(base=0x520000, pref=0x520000, entry=0, relocs=[], tls=None, slots=[], soi=0x4000 + 0x1000)


def build_b(variant=None):
    img = skeleton(0x30000000, entry=TEXT + 0x10)
    slots = img.imports(RDATA, [('c.dll', ['cfun', 10]), ('KERNEL32.dll', ['Sleep'])])
    ents = [(1, 'bfun', TEXT + 0x40), (2, None, TEXT + 0x80), (3, 'bdata', DATA + 0x10), (4, 'bfwd', 'C.cfun'), (5, 'bfwdord', 'c.#9')]
    if variant == 'noexp':
        ents = [e for e in ents if e[1] != 'bfun']
    if variant == 'badfwd':
        ents[3] = (4, 'bfwd', 'C.nosuch')
    if variant == 'cycle':
        ents[3] = (4, 'bfwd', 'B.bfwd')
    img.exports(RDATA + 0x400, 'b.dll', ents)
    reloc_words(img)
    name = 'b.dll' if not variant else 'b_%s.dll' % variant
    finish(img, name, 0x30000000, BASES['b.dll'], slots, None)


def build_a(variant=None):
    img = skeleton(0x400000, entry=TEXT + 0x10, relocs_stripped=(variant == 'noreloc'))
    slots = img.imports(RDATA, [('KERNEL32.dll', ['Sleep']), ('b.dll', ['bfun', 2, 3, 'bfwd'])])
    img.exports(RDATA + 0x400, 'a.dll', [(7, 'afun', TEXT + 0x50)])
    img.put(RDATA + 0x840, struct.pack('<II', 0x400000 + TEXT + 0x100, 0))
    img.relocs.extend([(RDATA + 0x840, 3)])
    img.tls(RDATA + 0x800, DATA, DATA + 0x40, DATA + 0x80, RDATA + 0x840, 0x30, 5 << 20)
    img.u32(DATA + 0x80, 0x12345678)
    reloc_words(img)
    if variant == 'badreloc':
        img.reloc(DATA + 0x110, 1)                  # IMAGE_REL_BASED_HIGH: not an i386 type
    if variant == 'noreloc':
        img.relocs = []
    name = 'a.dll' if not variant else 'a_%s.dll' % variant
    tls = dict(tmpl=BASES['a.dll'] + DATA, tsize=0x40, zero=0x30, index_va=BASES['a.dll'] + DATA + 0x80, index=1, align=16,
               cbs=[BASES['a.dll'] + TEXT + 0x100])
    finish(img, name, 0x400000, BASES['a.dll'], slots, None, tls=tls)


def build_de(which, variant=None):
    base = BASES[which + '.dll']
    img = skeleton(0x600000 if which == 'd' else 0x610000, entry=(TEXT + 0x10) if which == 'd' else 0)
    slots = []
    if which == 'd':
        slots = img.imports(RDATA, [('e.dll', ['efun']), ('c.dll', ['cfun'])])
        img.exports(RDATA + 0x400, 'd.dll', [(1, 'dfun', TEXT + 0x30)])
    else:
        img.exports(RDATA + 0x400, 'e.dll', [(1, 'efun', TEXT + 0x20)])
    reloc_words(img)
    tls = None
    if variant == 'tls':
        img.tls(RDATA + 0x800, DATA, DATA + 0x20, DATA + 0x80, 0, 0, 3 << 20)
        tls = dict(tmpl=0, tsize=0, zero=0, index_va=0, index=0, align=1, cbs=[])
    name = '%s.dll' % which if not variant else '%s_%s.dll' % (which, variant)
    finish(img, name, img.base, base, slots, None, tls=None)


def expected_values():
    """slot -> ('sys', None) | ('exp', va): every module import of the world, by the PE rule"""
    cfun = BASES['c.dll'] + TEXT + 0x20
    b = BASES['b.dll']
    table = {('a.dll', 'afun'): BASES['a.dll'] + TEXT + 0x50, ('a.dll', '#7'): BASES['a.dll'] + TEXT + 0x50,
             ('b.dll', 'bfun'): b + TEXT + 0x40, ('b.dll', '#1'): b + TEXT + 0x40, ('b.dll', '#2'): b + TEXT + 0x80,
             ('b.dll', '#3'): b + DATA + 0x10, ('b.dll', 'bdata'): b + DATA + 0x10, ('b.dll', 'bfwd'): cfun,
             ('b.dll', '#4'): cfun, ('b.dll', 'bfwdord'): cfun, ('b.dll', '#5'): cfun,
             ('c.dll', 'cfun'): cfun, ('c.dll', '#9'): cfun, ('c.dll', 'cdata'): BASES['c.dll'] + DATA + 0x20,
             ('c.dll', '#10'): BASES['c.dll'] + DATA + 0x20, ('d.dll', 'dfun'): BASES['d.dll'] + TEXT + 0x30,
             ('e.dll', 'efun'): BASES['e.dll'] + TEXT + 0x20}
    return table


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    build_exe(); build_c(); build_b(); build_a(); build_de('d'); build_de('e')
    build_c('long'); build_b('noexp'); build_b('badfwd'); build_b('cycle'); build_a('badreloc'); build_a('noreloc'); build_de('d', 'tls')
    for n, d in FILES.items():
        with open(os.path.join(out, n), 'wb') as fh:
            fh.write(d)
    exp = expected_values()
    order = [('c.dll', 1), ('b.dll', 1), ('a.dll', 1), ('d.dll', 0), ('e.dll', 0)]
    imgs = [('exe', 'exe.pe', None)] + [(n, n, s) for n, s in order]
    h = ['/* generated by synth/synth_modules.py: facts about the synthetic world */\n', '#include "fnvnx_modules.h"\n']
    def sha(n): return ','.join(str(b) for b in hashlib.sha256(FILES[n]).digest())
    h.append('static const fnvnx_module_desc sm_table[] = {\n')
    for n, s in order:
        h.append('    {"%s", {%s}, %du, 0x%08xu, 0x%08xu, %du},\n' % (n, sha(n), len(FILES[n]), BASES[n], FACTS[n]['soi'], s))
    h.append('};\n#define SM_NMOD %d\n' % len(order))
    h.append('static const struct { const char *file; unsigned char sha[32]; unsigned size; } sm_variants[] = {\n')
    variants = sorted(n for n in FILES if '_' in n and n.endswith('.dll'))
    for n in variants:
        h.append('    {"%s", {%s}, %du},\n' % (n, sha(n), len(FILES[n])))
    h.append('};\n#define SM_NVAR %d\n' % len(variants))
    h.append('static const char *const sm_files[] = {"exe.pe", %s};\n' % ', '.join('"%s"' % n for n, _ in order))
    h.append('static const struct sm_image { unsigned base, pref, soi, entry; int has_tls; unsigned tmpl, tsize, zero, index_va, index, align, ncb, cbs[2]; } sm_images[] = {\n')
    for n, f, s in imgs:
        k = 'exe' if n == 'exe' else n
        F = FACTS[k]; t = F['tls']
        h.append('    {0x%08xu, 0x%08xu, 0x%xu, 0x%08xu, %d, %s},\n' % (
            F['base'], F['pref'], F['soi'], F['entry'], 1 if t else 0,
            '0x%08xu, 0x%xu, 0x%xu, 0x%08xu, %du, %du, %du, {%s}' % (t['tmpl'], t['tsize'], t['zero'], t['index_va'], t['index'], t['align'], len(t['cbs']),
                                                                   ', '.join('0x%08xu' % c for c in t['cbs'] + [0] * (2 - len(t['cbs'])))) if t else '0, 0, 0, 0, 0, 0, 0, {0, 0}'))
    h.append('};\n')
    h.append('static const struct { unsigned img, slot; const char *dll; const char *name; unsigned ordinal; int exported; unsigned value; } sm_slots[] = {\n')
    nslots = 0
    for i, (n, f, s) in enumerate(imgs):
        F = FACTS[n]
        for slot, dll, fn in F['slots']:
            key = (dll.lower(), ('#%d' % fn) if isinstance(fn, int) else fn)
            v = exp.get(key)
            h.append('    {%d, 0x%08xu, "%s", %s, %du, %d, 0x%08xu},\n' % (i, F['base'] + slot, dll, '0' if isinstance(fn, int) else '"%s"' % fn,
                                                                        fn if isinstance(fn, int) else 0xffffffff, 1 if v else 0, v or 0))
            nslots += 1
    h.append('};\n#define SM_NSLOTS %d\n' % nslots)
    h.append('static const struct { unsigned img, va, delta; } sm_relocs[] = {\n')
    nr = 0
    for i, (n, f, s) in enumerate(imgs):
        F = FACTS[n]
        for va in F['relocs']:
            h.append('    {%d, 0x%08xu, 0x%08xu},\n' % (i, va, (F['base'] - F['pref']) & 0xffffffff))
            nr += 1
    h.append('    {0, 0, 0}\n};\n#define SM_NRELOCS %d\n' % nr)
    with open(os.path.join(out, 'synth_mod.h'), 'w') as fh:
        fh.write(''.join(h))


main()
