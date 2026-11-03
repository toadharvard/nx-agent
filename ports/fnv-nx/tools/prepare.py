#!/usr/bin/env python3
"""prepare.py - derive the port's build tree from a fnv_decomp checkout.

    tools/prepare.py <fnv_decomp checkout> <out tree>

Copies src/ and include/ into <out tree> and applies mechanical, deterministic rewrites (RULES below) that turn
MSVC/x86-only constructs into portable ones. The decomp checkout is never modified, and the output tree stays local:
it is derived from the decomp and must not be committed or shared (see docs/ARCHITECTURE.md, "Legal").

Every rule is a function (path, text) -> text, listed in RULES with a short id that the journal refers to.
Writes <out tree>/prepare_stats.tsv: rule id, files changed, edits.
"""
import os
import re
import shutil
import sys
from collections import Counter

# ---------------------------------------------------------------------------------------------------------- helpers

def match_brace(text, open_at):
    """Index just past the '}' matching text[open_at] == '{' (ignores braces in strings/comments, good enough)."""
    depth, i, n = 0, open_at, len(text)
    while i < n:
        c = text[i]
        if c == '"' or c == "'":
            q = c; i += 1
            while i < n and text[i] != q:
                i += 2 if text[i] == '\\' else 1
        elif text.startswith('//', i):
            i = text.find('\n', i)
            if i < 0: return n
        elif text.startswith('/*', i):
            i = text.find('*/', i)
            if i < 0: return n
            i += 1
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n

# ---------------------------------------------------------------------------------------------------------- rules

INTRIN_UNION = re.compile(r'(?:typedef\s+)?union\s+(?:__declspec\([^)]*\(?[^)]*\)?\)\s*)*(__m128[id]?|__m64)\b[^;{]*\{')

def r_sse_local_types(path, text, stats):
    """SSE1: drop local copies of the MSVC <xmmintrin.h> vector unions; the global sse2neon type is used instead."""
    out, pos = [], 0
    for m in INTRIN_UNION.finditer(text):
        if m.start() < pos: continue
        end = match_brace(text, m.end() - 1)
        tail = re.match(r'\s*(?:__m128[id]?|__m64)?\s*;', text[end:])
        if tail: end += tail.end()
        out.append(text[pos:m.start()]); out.append('/* fnv-nx SSE1: local %s removed */' % m.group(1)); pos = end
        stats['SSE1'] += 1
    out.append(text[pos:])
    return ''.join(out)

MM_PROTO = re.compile(r'^[ \t]*(?:extern\s+"C"\s+)?(?:static\s+)?(?:inline\s+)?(?:const\s+)?'
                      r'(?:__m128[id]?|__m64|float|double|int|unsigned\s+int|void)\s+_mm_\w+\s*\([^;{)]*\)\s*;[ \t]*$', re.M)
EXTERN_C_BLOCK = re.compile(r'extern\s+"C"\s*\{')

def r_sse_local_protos(path, text, stats):
    """SSE2: drop local prototypes of _mm_* intrinsics (and extern "C" blocks left empty by that)."""
    text, n = MM_PROTO.subn('', text)
    stats['SSE2'] += n
    out, pos = [], 0
    for m in EXTERN_C_BLOCK.finditer(text):
        if m.start() < pos: continue
        end = match_brace(text, m.end() - 1)
        if text[m.end():end - 1].strip() == '':
            out.append(text[pos:m.start()]); pos = end; stats['SSE2'] += 1
    out.append(text[pos:])
    return ''.join(out)

M128_MEMBER = re.compile(r'((?:\b\w+|\))(?:\s*(?:\.|->)\s*\w+|\[[^\]\[]*\])*)\s*\.\s*(m128i?)_(f32|f64|u64|u32|u16|u8|i64|i32|i16|i8)\s*\[')
ELEM = {'f32': 'float', 'f64': 'double', 'u64': 'unsigned long long', 'u32': 'unsigned int', 'u16': 'unsigned short',
        'u8': 'unsigned char', 'i64': 'long long', 'i32': 'int', 'i16': 'short', 'i8': 'signed char'}

def r_sse_members(path, text, stats):
    """SSE3: MSVC union members (v.m128_f32[i]) -> element access through a typed pointer (lvalue-safe)."""
    def sub(m):
        stats['SSE3'] += 1
        return '((%s*)&(%s))[' % (ELEM[m.group(3)], m.group(1))
    return M128_MEMBER.sub(sub, text)

RULES = [r_sse_local_types, r_sse_local_protos, r_sse_members]

# ---------------------------------------------------------------------------------------------------------- driver

def main():
    src_root, out_root = sys.argv[1], sys.argv[2]
    stats, files = Counter(), Counter()
    for top in ('src', 'include'):
        for dirpath, _, names in os.walk(os.path.join(src_root, top)):
            rel = os.path.relpath(dirpath, src_root)
            os.makedirs(os.path.join(out_root, rel), exist_ok=True)
            for name in names:
                s, d = os.path.join(dirpath, name), os.path.join(out_root, rel, name)
                if not name.endswith(('.cpp', '.h', '.hpp', '.inl', '.c')):
                    shutil.copyfile(s, d); continue
                with open(s, encoding='utf-8', errors='surrogateescape') as f:
                    text = orig = f.read()
                for rule in RULES:
                    before = Counter(stats)
                    text = rule(s, text, stats)
                    for k in stats:
                        if stats[k] != before[k]: files[k] += 1
                with open(d, 'w', encoding='utf-8', errors='surrogateescape') as f:
                    f.write(text)
    shutil.copytree(os.path.join(src_root, 'build'), os.path.join(out_root, 'build'), dirs_exist_ok=True)
    with open(os.path.join(out_root, 'prepare_stats.tsv'), 'w') as f:
        for k in sorted(stats):
            f.write('%s\t%d files\t%d edits\n' % (k, files[k], stats[k]))
    print(open(os.path.join(out_root, 'prepare_stats.tsv')).read(), end='')

if __name__ == '__main__':
    main()
