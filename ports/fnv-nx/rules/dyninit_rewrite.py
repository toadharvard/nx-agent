#!/usr/bin/env python3
"""dyninit_rewrite.py - one native function per original dynamic initializer (rule XC1).

MSVC compiled every file-scope object with a dynamic initializer to its own function (`??__E`, in .text$yc,
called from the CRT table __xc_a..__xc_z) and, for a non-trivial destructor, an atexit thunk (`??__F`, in
.text$yd) that `??__E` registers. A unit that spells these as plain object definitions tagged with the original
addresses:

    // @fnv 0x00000010          <- the dynamic initializer
    // @fnv 0x00000020          <- the atexit destructor, when the type has one
    T g_obj(g_obj_arg0, g_obj_arg1);

is rewritten so that each initializer is its own extern "C" function named by its address:

    extern T g_obj;
    // @fnv 0x00000020
    extern "C" void __fnvnx_dynexit_00000020(void) { fnvnx_dyn_destroy(g_obj); }
    // @fnv 0x00000010
    extern "C" void __fnvnx_dyninit_00000010(void) { ::new ((fnvnx_dyn_tag *)(void *)__builtin_addressof(g_obj))
        T(g_obj_arg0, g_obj_arg1); fnvnx_xc_atexit(__fnvnx_dynexit_00000020); }

The fact the rule rests on: the tag address is the address of a function whose recipe symbol (build/units.json)
is `??__E...` (kind E) and, for the second tag, `??__F...` (kind F). GCC folds all initializers of a unit into one
function, so their order relative to other units is lost; a function per initializer lets the startup runner call
them in the original order. The object itself is no longer defined natively: it lives in the data image and every
reference binds to its address. A copy of such a definition in a unit that does not own the address (the recipe
says which unit defines it) becomes a plain extern declaration.
"""
from __future__ import annotations

import json
import os
import re

import core

MARKER = '// fnvnx S10 dyninit_rewrite: '
TAG = re.compile(r'^[ \t]*//[ \t]*@fnv[ \t]+(0x[0-9a-fA-F]+)[ \t]*$')
NAME_AT_END = re.compile(r'([A-Za-z_]\w*)\s*$')
# A placement form of our own (tag type) instead of <new>: some generated headers define the standard
# placement operator new themselves, and <new> would define it a second time.
PROLOGUE = (MARKER + 'one extern "C" function per original initializer (startup/tools/dyninit_rewrite.py)\n'
            'struct fnvnx_dyn_tag;\n'
            'inline void *operator new(decltype(sizeof 0), fnvnx_dyn_tag *p) noexcept { return p; }\n'
            'inline void operator delete(void *, fnvnx_dyn_tag *) noexcept {}\n'
            'extern "C" int fnvnx_xc_atexit(void (*)(void));\n'
            'template <class T> static inline void fnvnx_dyn_destroy(T &o) { o.~T(); }\n')


class Index:
    """Original addresses of ??__E / ??__F functions and the unit that defines each."""

    def __init__(self, units: dict):
        self.kind: dict[int, str] = {}
        self.home: dict[int, str] = {}
        self.homes_of_e: set[str] = set()
        for u in units['units']:
            for f in u['functions']:
                a = int(f['address'], 16)
                s = f['symbol']
                k = 'E' if s.startswith('??__E') else 'F' if s.startswith('??__F') else 'O'
                self.kind.setdefault(a, k)
                self.home.setdefault(a, u['source'])
                if k == 'E':
                    self.homes_of_e.add(u['source'])

    @classmethod
    def load(cls, path: str) -> 'Index':
        with open(path, encoding='utf-8') as f:
            return cls(json.load(f))


def split_decl(text: str):
    """'T x;' -> (T, x, None); 'T x(args);' -> (T, x, args); None if it is not a plain object definition."""
    t = text.strip()
    if t.startswith('FNVNX_KEEP '):
        t = t[len('FNVNX_KEEP '):].strip()
    if not t.endswith(';') or '{' in t or '=' in t or '[' in t:
        return None
    body = t[:-1].rstrip()
    args = None
    if body.endswith(')'):
        depth = 0
        for i in range(len(body) - 1, -1, -1):
            c = body[i]
            if c == ')':
                depth += 1
            elif c == '(':
                depth -= 1
                if depth == 0:
                    args = body[i + 1:-1].strip()
                    body = body[:i].rstrip()
                    break
        else:
            return None
        if not args:
            return None  # 'T x()' declares a function, never an object with an initializer
    m = NAME_AT_END.search(body)
    if not m:
        return None
    name, typ = m[1], body[:m.start()].strip()
    if not typ or typ.split()[0] in ('static', 'extern', 'typedef', 'thread_local', 'inline', 'constexpr'):
        return None
    if typ.endswith(('::', ',')) or '(' in typ:
        return None
    return typ, name, args


def rewrite(rel: str, text: str, index: Index, stats=None, report=None) -> str:
    """Rewrite the tagged initializer definitions of one source file (rel: path relative to the tree)."""
    if MARKER in text or '@fnv' not in text:
        return text
    lines = text.split('\n')
    out: list[str] = []
    changed = 0
    i = 0
    while i < len(lines):
        m = TAG.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        tags = []
        j = i
        while j < len(lines) and TAG.match(lines[j]):
            tags.append(int(TAG.match(lines[j])[1], 16))
            j += 1
        if index.kind.get(tags[0]) != 'E' or len(tags) > 2:
            out.extend(lines[i:j])
            i = j
            continue
        # the declaration: up to the first line ending with ';' (generated units use one line)
        k = j
        while k < len(lines) and k - j < 8 and not lines[k].rstrip().endswith(';'):
            k += 1
        decl_lines = lines[j:k + 1]
        parsed = split_decl(' '.join(decl_lines)) if k < len(lines) else None
        e = tags[0]
        f = tags[1] if len(tags) == 2 else None
        if f is not None and index.kind.get(f, 'F') != 'F':
            f = None
        if not parsed:
            if report is not None:
                report.append((e, f, rel, '', '', 'skipped: not a plain object definition'))
            out.extend(lines[i:k + 1])
            i = k + 1
            continue
        typ, name, args = parsed
        indent = re.match(r'[ \t]*', decl_lines[0])[0]
        owned = index.home.get(e) == rel
        if not owned:
            # a copy (flag-variant unit): keep the line count, no second definition or construction
            for t in tags:
                out.append('%s// fnvnx S10: initializer 0x%08x of %s is defined by %s' % (
                    indent, t, name, index.home.get(e, '?')))
            out.append('%sextern %s %s;' % (indent, typ, name))
            out.extend([''] * (len(decl_lines) - 1))
            if stats is not None:
                stats['XC1x'] += 1
            if report is not None:
                report.append((e, f, rel, name, typ, 'extern (owned by %s)' % index.home.get(e, '?')))
        else:
            out.append('%sextern %s %s;' % (indent, typ, name))
            ctor = '%s(%s)' % (typ, args) if args is not None else typ
            reg = ''
            if f is not None:
                out.append('%s// @fnv 0x%08x' % (indent, f))
                out.append('%sextern "C" void __fnvnx_dynexit_%08x(void) { fnvnx_dyn_destroy(%s); }' % (
                    indent, f, name))
                reg = ' fnvnx_xc_atexit(__fnvnx_dynexit_%08x);' % f
            out.append('%s// @fnv 0x%08x' % (indent, e))
            out.append('%sextern "C" void __fnvnx_dyninit_%08x(void) { ::new ((fnvnx_dyn_tag *)(void *)__builtin_addressof(%s)) %s;%s }'
                       % (indent, e, name, ctor, reg))
            if stats is not None:
                stats['XC1'] += 1
            if report is not None:
                report.append((e, f, rel, name, typ, 'init' + (' + exit' if f is not None else '')))
        changed += 1
        i = k + 1
    if not changed:
        return text
    return PROLOGUE + '\n'.join(out)


# ---- prepare rule ---------------------------------------------------------------------------------------

_INDEX_CACHE: dict[str, Index] = {}


def r_dyninit(path, text, stats):
    """XC1: per-initializer extern "C" functions for the ??__E definitions of a unit (see the module docstring)."""
    if '@fnv' not in text:
        return text
    root, units = core._units_json(path)
    if not units:
        return text
    if units not in _INDEX_CACHE:
        _INDEX_CACHE[units] = Index.load(units)
    rel = os.path.relpath(os.path.abspath(path), root).replace(os.sep, '/')
    return rewrite(rel, text, _INDEX_CACHE[units], stats)


RULES = [r_dyninit]
