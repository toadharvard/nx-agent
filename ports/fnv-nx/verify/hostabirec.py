#!/usr/bin/env python3
"""The one record of the host ABI as the boot machine uses it: every number, name list and table that bootmachine.py, boothost.py,
bootsock.py and their tests need from hostabi/include/fnvnx/{hostapi,placement,reach,sockwire,sockwire_errno}.h, parsed from the headers
(the one source; the headers are L5's). Nothing of the record is typed a second time in verify/.

    import hostabirec; R = hostabirec.record()
    R.define("FNVNX_APP_CORES")              -> int     an object-like `#define NAME <constant expression>` of any of the headers
    R.xlist("FNVNX_WIRE_AF_LIST")            -> [(AF_UNSPEC, 0), ...]   the X(...) rows of a `#define NAME(X) X(a, b) ...` list, numbers resolved
    R.slots                                  -> [names of the uint64 fields of struct FnvnxHostApi after the four header words]
    R.services                               -> {fn: (NAME, least nargs or None)} the TOTALITY TABLE of hostapi.h (module 11), None = NOT SERVED
    python3 hostabirec.py [--include DIR] --out FILE.py     the same record as a Python module (one generated file, for a recipe stage)

Parsing rules (the argument that they close the class): comments are removed; a line ending in a backslash continues; an object-like
`#define NAME <expr>` is evaluated when <expr> is integer literals (with u/l suffixes), earlier names of the record, parentheses and the
operators | & ^ << >> + - ~ and C integer casts; a define that is not such an expression (a macro with parameters other than a list, an
expression with a builtin) is not in the record and R.define raises KeyError for it. An X-list row is `X(a, b, ...)` with each argument an
integer expression or an identifier (kept as a string when it is no define). Asking for anything absent raises: there is no default.
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
INCLUDE = os.path.join(HERE, "..", "hostabi", "include", "fnvnx")
HEADERS = ("hostapi.h", "placement.h", "reach.h", "sockwire.h", "sockwire_errno.h")   # the headers of the declared host ABI, in dependency order


class RecordError(Exception):
    pass


def _strip(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    return text.replace("\\\n", " ")


_TOK = re.compile(r"\s*(?:(0[xX][0-9A-Fa-f]+|\d+)[uUlL]*|([A-Za-z_]\w*)|(<<|>>|[()|&^+\-~*]))")
_CAST = re.compile(r"\(\s*(?:unsigned(?:\s+(?:int|long|char|short))?|int|long|u?int(?:8|16|32|64)_t|uintptr_t|size_t)\s*\)")


def _eval(expr, env):
    """integer value of the C constant expression EXPR, names looked up in ENV; ValueError when it is not one"""
    expr = _CAST.sub(" ", expr)
    toks, pos = [], 0
    expr = expr.strip()
    while pos < len(expr):
        m = _TOK.match(expr, pos)
        if not m:
            raise ValueError(expr)
        pos = m.end()
        if m.group(1) is not None:
            toks.append(("n", int(m.group(1), 0)))
        elif m.group(2) is not None:
            if m.group(2) not in env:
                raise ValueError(expr)
            toks.append(("n", env[m.group(2)]))
        else:
            toks.append(("o", m.group(3)))
    i = [0]

    def peek():
        return toks[i[0]] if i[0] < len(toks) else (None, None)

    def take():
        t = toks[i[0]]
        i[0] += 1
        return t

    def prim():
        k, v = take()
        if k == "n":
            return v
        if v == "(":
            r = bor()
            if take() != ("o", ")"):
                raise ValueError(expr)
            return r
        if v == "~":
            return ~prim() & 0xFFFFFFFFFFFFFFFF
        if v == "-":
            return -prim()
        raise ValueError(expr)

    def level(sub, ops):
        def f():
            r = sub()
            while peek() == ("o", peek()[1]) and peek()[0] == "o" and peek()[1] in ops:
                op = take()[1]
                r = ops[op](r, sub())
            return r
        return f

    mul = level(prim, {"*": lambda a, b: a * b})
    add = level(mul, {"+": lambda a, b: a + b, "-": lambda a, b: a - b})
    sh = level(add, {"<<": lambda a, b: a << b, ">>": lambda a, b: a >> b})
    band = level(sh, {"&": lambda a, b: a & b})
    bxor = level(band, {"^": lambda a, b: a ^ b})
    bor = level(bxor, {"|": lambda a, b: a | b})
    r = bor()
    if i[0] != len(toks):
        raise ValueError(expr)
    return r


def _split_args(s):
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


class Record:
    def __init__(self, include=INCLUDE):
        self.include = include
        self.defines, self.lists, self.texts = {}, {}, {}
        for h in HEADERS:
            with open(os.path.join(include, h)) as f:
                raw = f.read()
            self.texts[h] = raw
            txt = _strip(raw)
            for m in re.finditer(r"^[ \t]*#[ \t]*define[ \t]+(\w+)(\([^)]*\))?[ \t]*([^\n]*)$", txt, re.M):
                name, params, body = m.group(1), m.group(2), m.group(3).strip()
                if params:
                    if params.strip("()").strip() == "X":
                        rows = re.findall(r"\bX\s*\(((?:[^()]|\([^()]*\))*)\)", body)
                        self.lists[name] = [self._row(_split_args(r)) for r in rows]
                    continue
                if not body:
                    continue
                try:
                    self.defines[name] = _eval(body, self.defines)
                except ValueError:
                    pass
        hx = self.texts["hostapi.h"]
        self.services, self.slots = self._totality(hx)

    def _row(self, args):
        out = []
        for a in args:
            try:
                out.append(_eval(a, self.defines))
            except ValueError:
                out.append(a)
        return tuple(out)

    @staticmethod
    def _totality(txt):
        """TOTALITY TABLE of hostapi.h -> ({fn: (NAME, n | None)}, slots). Rows are `<fn> <NAME> n=<k>`, the NOT SERVED rows, and the combined
        sockets row (names, then `n = ... (SOCK_SOCKET 3, CLOSE 1, ...)`); every row is cross-checked against the `#define FNVNX_PLAT_<NAME> <fn>u` lines."""
        if "TOTALITY TABLE BEGIN" not in txt or "TOTALITY TABLE END" not in txt:
            raise RecordError("hostapi.h: no TOTALITY TABLE block")
        block = txt[txt.index("TOTALITY TABLE BEGIN"):txt.index("TOTALITY TABLE END")]
        lines = [re.sub(r"^\s*\*\s?", "", l) for l in block.splitlines()]
        rows = {}
        for l in lines:
            m = re.match(r"\s*(\d+) ([A-Z_0-9]+)\s+n=(\d+)\b", l)
            if m:
                rows[int(m.group(1))] = (m.group(2), int(m.group(3)))
            m = re.match(r"\s*(\d+) ([A-Z_0-9]+)\s+NOT SERVED", l)
            if m:
                rows[int(m.group(1))] = (m.group(2), None)
        body = "\n".join(lines)
        m = re.search(r"16\.\.26 30 31 (.*?)\bn = the count[^(]*\((.*?)\)\s*\n", body, re.S)
        if not m:
            raise RecordError("hostapi.h: the sockets row of the TOTALITY TABLE is not in the known form")
        names = re.findall(r"\bSOCK_[A-Z_]+", m.group(1))
        counts = {("SOCK_" + a if not a.startswith("SOCK_") else a): int(b) for a, b in re.findall(r"([A-Z_]+) (\d+)", m.group(2))}
        if len(names) != 13 or set(counts) != set(names):
            raise RecordError("hostapi.h: the sockets row has %d names and %d counts" % (len(names), len(counts)))
        for f, n in zip(list(range(16, 27)) + [30, 31], names):
            rows[f] = (n, counts[n])
        defs = {int(v): n for n, v in re.findall(r"#define FNVNX_PLAT_([A-Z_0-9]+) (\d+)u", txt)}
        if {f: r[0] for f, r in rows.items()} != defs:
            raise RecordError("hostapi.h: the TOTALITY TABLE rows and the FNVNX_PLAT_* defines differ")
        st = txt[txt.index("typedef struct FnvnxHostApi {"):txt.index("} FnvnxHostApi;")]
        fields = re.findall(r"uint64_t (\w+);", _strip(st))
        return rows, fields[4:]   # after guest_base, guest_size, heap_base, heap_size

    # -- views
    def define(self, name):
        if name not in self.defines:
            raise KeyError("hostabirec: no constant define %s in %s" % (name, ", ".join(HEADERS)))
        return self.defines[name]

    def xlist(self, name):
        if name not in self.lists:
            raise KeyError("hostabirec: no X-list %s in %s" % (name, ", ".join(HEADERS)))
        return list(self.lists[name])

    def platform_services(self):
        """{fn: (lower-case name, least nargs)} of the served non-socket services of module 11"""
        return {f: (n.lower(), k) for f, (n, k) in sorted(self.services.items()) if k is not None and not n.startswith("SOCK_")}

    def sock_services(self):
        return {f: (n.lower(), k) for f, (n, k) in sorted(self.services.items()) if k is not None and n.startswith("SOCK_")}

    def api_size(self):
        """sizeof(FnvnxHostApi): the four header words and the magic/version/size words (0x30 bytes, see hostapi.h) plus 8 bytes per slot"""
        m = re.search(r"typedef struct FnvnxHostApi \{(.*?)\} FnvnxHostApi;", _strip(self.texts["hostapi.h"]), re.S)
        words = re.findall(r"\b(uint32_t|uint64_t)\s+\w+\s*;", m.group(1))
        return sum(4 if w == "uint32_t" else 8 for w in words)


_cache = {}


def record(include=INCLUDE):
    k = os.path.abspath(include)
    if k not in _cache:
        _cache[k] = Record(include)
    return _cache[k]


def module_text(r):
    out = ["# GENERATED by verify/hostabirec.py from hostabi/include/fnvnx/*.h; do not edit\n",
           "DEFINES = %r\n" % (dict(sorted(r.defines.items())),), "LISTS = %r\n" % (dict(sorted(r.lists.items())),),
           "SERVICES = %r\n" % (dict(sorted(r.services.items())),), "SLOTS = %r\n" % (list(r.slots),), "API_SIZE = %d\n" % r.api_size()]
    return "".join(out)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--include", default=INCLUDE)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        text = module_text(Record(a.include))
    except (OSError, RecordError) as e:
        print("hostabirec: %s" % e, file=sys.stderr)
        return 1
    with open(a.out, "w") as f:
        f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
