"""s8_asm.py - prepare rules of stream S8-asm: MSVC x86 inline assembly -> portable C++ (docs/ASM.md).

Rules (ids cited in the journal):
  ASM1  the decomp's x87 helper header (include/fnv_asm_helpers.h: fnv_SinCos, fnv_FloatToInt and the fixed x87 /
        rdtsc / cpuid / int 3 macros) is replaced by a header that maps the same names onto shim/include/fnvnx_asm.h.
        Edits = __asm tokens of the replaced header.
  ASM2  every `__asm { ... }` block in src/ whose instruction sequence is in PATTERNS becomes the equivalent call
        into fnvnx_asm.h (line count kept); the unit gets `#include "fnvnx_asm.h"` (ASM3). Edits = blocks.
        Blocks that match no pattern are left as they are and counted in ASM-LEFT, with a warning naming the file,
        line and enclosing `// @fnv` address (the key for a per-site entry in SITES below).
  ASM3  `#include "fnvnx_asm.h"` added at the top of a unit that ASM2 or ASM4 rewrote.
  ASM4  the MSVC intrinsic __rdtsc(): calls -> fnvnx_rdtsc64(), the decomp's local prototypes of it dropped.
"""
import os
import re
import sys

# ---------------------------------------------------------------------------------------------------------- ASM1

HELPERS_HEADER = '''// fnv-nx ASM1: the decomp's x87 helper header, replaced by portable definitions of the same names
// (shim/include/fnvnx_asm.h, docs/ASM.md).
#pragma once
#define FNVNX_ASM_FNV_HELPERS 1
#include "fnvnx_asm.h"
'''


def _norm_path(path):
    return path.replace('\\', '/')


def r_asm_helpers_header(path, text, stats):
    """ASM1: include/fnv_asm_helpers.h -> FNVNX_ASM_FNV_HELPERS + fnvnx_asm.h."""
    if not _norm_path(path).endswith('/include/fnv_asm_helpers.h'):
        return text
    stats['ASM1'] += len(re.findall(r'\b__asm\b', text))
    return HELPERS_HEADER


# ---------------------------------------------------------------------------------------------------------- ASM2

ASM_BLOCK = re.compile(r'\b__asm\s*\{([^{}]*)\}')
ID = r'([A-Za-z_]\w*)'

# Normalized instruction sequence (one instruction per line, lower-case mnemonic, single spaces, `ptr [x]`, `, `)
# -> C++ statement. Operand names are the C++ variables the MSVC block named.
PATTERNS = [
    # rdtsc; low dword of the counter into a 32-bit variable
    (re.compile(r'rdtsc\nmov dword ptr \[%s\], eax' % ID),
     lambda m: 'fnvnx_asm_store32(&(%s), fnvnx_rdtsc32());' % m.group(1)),
    # rdtsc; full counter into a 64-bit variable
    (re.compile(r'rdtsc\nmov dword ptr \[%s\], eax\nmov dword ptr \[\1 ?\+ ?4\], edx' % ID),
     lambda m: 'fnvnx_asm_store64(&(%s), fnvnx_rdtsc64());' % m.group(1)),
    # x87 conversion in the current rounding mode, store width = width of the destination
    (re.compile(r'fld %s\nfistp %s' % (ID, ID)),
     lambda m: 'fnvnx_asm_fistp(&(%s), %s);' % (m.group(2), m.group(1))),
    # fsincos: first fstp takes ST0 = cos, second ST1 = sin
    (re.compile(r'fld %s\nfsincos\nfstp %s\nfstp %s' % (ID, ID, ID)),
     lambda m: 'fnvnx_fsincos(%s, &(%s), &(%s));' % (m.group(1), m.group(2), m.group(3))),
]

# Per-site replacements for blocks no pattern can express: {(path suffix, '0x<function address>'): fn(body) -> str}.
# Empty at decomp 87eb9373: every site there matches a pattern (docs/ASM.md).
SITES = {}


def normalize_asm(body):
    """MSVC asm block body -> one normalized instruction per line."""
    out = []
    for line in re.split(r'\n|\b__asm\b', body):
        line = line.split(';', 1)[0].split('//', 1)[0].strip()
        if not line: continue
        line = re.sub(r'\s+', ' ', line)
        line = re.sub(r'\s*,\s*', ', ', line)
        line = re.sub(r'\bptr\s*\[\s*', 'ptr [', line)
        line = re.sub(r'\s*\]', ']', line)
        mn, _, rest = line.partition(' ')
        out.append((mn.lower() + ' ' + rest).strip())
    return '\n'.join(out)


def enclosing_fnv(text, pos):
    found = None
    for m in re.finditer(r'@fnv (0x[0-9a-fA-F]+)', text[:pos]):
        found = m.group(1)
    return found or '-'


def translate_block(path, text, m):
    norm = normalize_asm(m.group(1))
    for pat, emit in PATTERNS:
        pm = pat.fullmatch(norm)
        if pm: return emit(pm)
    key_addr = enclosing_fnv(text, m.start())
    for (suffix, addr), fn in SITES.items():
        if addr == key_addr and _norm_path(path).endswith(suffix): return fn(m.group(1))
    return None


def r_asm_blocks(path, text, stats):
    """ASM2 (+ASM3): __asm blocks -> fnvnx_asm.h calls."""
    if '__asm' not in text or _norm_path(path).endswith('/include/fnv_asm_helpers.h'):
        return text
    out, pos, done = [], 0, 0
    for m in ASM_BLOCK.finditer(text):
        repl = translate_block(path, text, m)
        if repl is None:
            stats['ASM-LEFT'] += 1
            sys.stderr.write('s8_asm: untranslated __asm block %s:%d (@fnv %s)\n'
                             % (path, text.count('\n', 0, m.start()) + 1, enclosing_fnv(text, m.start())))
            continue
        out.append(text[pos:m.start()])
        out.append('{ %s /* fnv-nx ASM2 */ }' % repl + '\n' * m.group(0).count('\n'))
        pos = m.end(); done += 1
    if not done: return text
    out.append(text[pos:])
    stats['ASM2'] += done
    return add_include(''.join(out), stats)


INCLUDE_LINE = '#include "fnvnx_asm.h" /* fnv-nx ASM3 */\n'


def add_include(text, stats):
    if INCLUDE_LINE in text: return text
    stats['ASM3'] += 1
    return INCLUDE_LINE + text


# ---------------------------------------------------------------------------------------------------------- ASM4

RDTSC_PROTO = re.compile(r'^[ \t]*(?:extern\s+"C"\s+)?unsigned\s+__int64\s+(?:__cdecl\s+)?__rdtsc\s*\(\s*(?:void)?\s*\)'
                         r'\s*;[ \t]*$', re.M)
RDTSC_CALL = re.compile(r'\b__rdtsc\s*\(\s*\)')


def r_rdtsc_intrinsic(path, text, stats):
    """ASM4: __rdtsc() -> fnvnx_rdtsc64(); the decomp's local prototypes of the intrinsic dropped."""
    if '__rdtsc' not in text: return text
    text, n = RDTSC_PROTO.subn('/* fnv-nx ASM4 */', text)
    text, k = RDTSC_CALL.subn('fnvnx_rdtsc64()', text)
    if not (n or k): return text
    stats['ASM4'] += n + k
    return add_include(text, stats)


RULES = [r_asm_helpers_header, r_asm_blocks, r_rdtsc_intrinsic]
