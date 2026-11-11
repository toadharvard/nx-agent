"""What an MSVC decorated data name says about its object: the size of the type (when the name fixes it), and
the bytes of literals whose names spell their contents (__real@, __xmm@, short ??_C@ strings).

MSVC mangles a global array as a pointer to its element (`int a[16]` -> ?a@@3PAHA), so a top-level pointer gives
only a lower bound; class types give none.
"""
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
from mkdataprobes import SAFE, SPECIAL  # noqa: E402

PRIM = {'C': 1, 'D': 1, 'E': 1, 'F': 2, 'G': 2, 'H': 4, 'I': 4, 'J': 4, 'K': 4, 'M': 4, 'N': 8, 'O': 8}
PRIM_U = {'_N': 1, '_J': 8, '_K': 8, '_W': 2, '_D': 1, '_E': 1}
STORAGE = re.compile(r'@([234])([A-Z_][A-Z0-9_$@?]*)$')


def type_size(name):
    """(size, exact) for a decorated variable name, or None. exact=False: size is a lower bound."""
    if not name.startswith('?') or '@' not in name:
        return None
    # the storage class char (3 global, 2 static member, 4 local static) and the type that follows it: take the
    # last candidate whose tail is a type encoding ending in a cv class letter
    for m in reversed(list(STORAGE.finditer(name))):
        t = m.group(2)
        r = _type(t)
        if r is not None:
            return r
    for i in range(len(name) - 2, 0, -1):
        if name[i] in '234' and name[i - 1] == '@':
            r = _type(name[i + 1:])
            if r is not None:
                return r
    return None


def _type(t):
    if len(t) < 2:
        return None
    if t[-1] not in 'ABCD':
        return None
    body = t[:-1]
    if body in PRIM:
        return PRIM[body], True
    if body in PRIM_U:
        return PRIM_U[body], True
    if body.startswith('W4') and body.endswith('@'):
        return 4, True
    if body[0] in 'PQRS' and len(body) > 2:
        return 4, False            # a pointer, or an array of anything (MSVC mangles arrays as pointers)
    if body[0] in 'TUV' and body.endswith('@'):
        return 1, False
    return None


def real_bytes(name):
    """bytes of __real@<hex> / __xmm@<hex> (the name is the value, big-endian hex)"""
    if name.startswith(('__real@', '__xmm@')):
        h = name.split('@', 1)[1]
        if re.fullmatch(r'[0-9a-f]+', h) and len(h) % 2 == 0:
            return bytes.fromhex(h)[::-1]
    return None


def _num(enc):
    """MSVC encoded number: digit d -> d+1; else hex digits A..P terminated by @. Returns (value, rest)."""
    if not enc:
        return None, enc
    if enc[0].isdigit():
        return int(enc[0]) + 1, enc[1:]
    v, i = 0, 0
    while i < len(enc) and 'A' <= enc[i] <= 'P':
        v = v * 16 + ord(enc[i]) - 65
        i += 1
    if i < len(enc) and enc[i] == '@':
        return v, enc[i + 1:]
    return None, enc


def literal_bytes(name):
    """(bytes, complete) of a narrow string literal ??_C@_0<len>@<hash>@<chars>@: complete when the name spells
    every byte including the terminating NUL (MSVC keeps at most 32 characters in the name)."""
    if not name.startswith('??_C@_0'):
        return None
    n, rest = _num(name[7:])
    if n is None or '@' not in rest:
        return None
    enc = rest.split('@', 1)[1]
    out, i = bytearray(), 0
    while i < len(enc):
        c = enc[i]
        if c == '@':
            break
        if c != '?':
            if ord(c) not in SAFE:
                return None
            out.append(ord(c))
            i += 1
        elif i + 1 < len(enc) and enc[i + 1] in SPECIAL:
            out.append(SPECIAL[enc[i + 1]])
            i += 2
        elif i + 3 < len(enc) and enc[i + 1] == '$' and 'A' <= enc[i + 2] <= 'P' and 'A' <= enc[i + 3] <= 'P':
            out.append((ord(enc[i + 2]) - 65) * 16 + ord(enc[i + 3]) - 65)
            i += 4
        else:
            return None
    if len(out) > n:
        return None
    return bytes(out), len(out) == n


def import_name(name):
    """__imp__Foo@8 / __imp_?Foo@@YGHXZ / __imp__foo -> Foo / foo"""
    if not name.startswith('__imp_'):
        return None
    n = name[6:]
    if n.startswith('_'):
        n = n[1:]
        return n.split('@', 1)[0]
    if n.startswith('?'):
        return n[1:].split('@', 1)[0]
    return n.split('@', 1)[0]
