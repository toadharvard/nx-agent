#!/usr/bin/env python3
"""check_hostapi.py - the guest's ext_call uses against the declarations of hostabi/include/fnvnx/hostapi.h.

The header is the single source of the host-service numbering. Declared in it, machine-readably:
  * every service number:  `#define FNVNX_PLAT_<NAME> <number>u`  (FIRST/LAST are range markers, not services);
  * every module number:   `#define FNVNX_EXT_<NAME> <number>u`;
  * every argument count:  a row `<number> <NAME> n=<count>` of the totality table (also in the "L5 ADDITIONS" block), and for
    the sockets the list `n = the count each service states (SOCK_SOCKET 3, CLOSE 1, ...)` of the table's 16..33 row.
    A service without a count (a row marked NOT SERVED) cannot be used.
Checks (exit 1 with one line per violation):
  A. the header is consistent: no service number and no module number defined twice, no service name defined twice, every
     table row names a defined service with the defined number, every defined service (but the not-served ones) has a count, the
     socket list names exactly the FNVNX_PLAT_SOCK_* services;
  B. every use of ext_call in platform/ and rt/guest/ is
        fnvnx_hcall4(<slot>ext_call, FNVNX_EXT_PLATFORM, FNVNX_PLAT_<NAME>, <args>, <literal n>)
     or a call of a wrapper of that form: a function whose body makes that call with the service (and the count) taken from its
     own parameters. Wrappers are found by rule, transitively (a function that passes its own parameter as the service of a
     wrapper is a wrapper). At every use the service is a header name, the module is a FNVNX_EXT_ name (never a number), the
     count is an integer literal equal to the header's count of that service, and a service is never a literal number;
  C. no scanned file defines a name the header defines;
  D. the host's served set equals the declared set: the services the host answers are the rows `[FNVNX_PLAT_<NAME>] = {n, handler}` of
     g_plat (toolchain/host/host_platform.inc) and the `case FNVNX_PLAT_SOCK_<NAME>: return n;` of host_sock_nargs
     (toolchain/host/host_sock.inc). Every declared service that the header counts has exactly one server with the header's count; a served
     name the header does not declare, or a declared (counted) service with no server, fails. A host file never serves by a numeric range
     (FNVNX_PLAT_SOCK_FIRST / _LAST): every number outside the declared set answers NO_SERVICE by the table's own default.
  E. the identifiers FNVNX_PLAT_* appear only in hostapi.h's own defines and in unit files (.c, .cpp, and the .inc files a unit includes):
     no other header (.h, .hpp, .hh anywhere under the root but build/out directories) mentions one, comments included. A header
     reaches a service only through a function of a unit.
Calls on a module other than FNVNX_EXT_PLATFORM (the GPU module) are not services of the table; only their module must be a name.
Usage: check_hostapi.py [--root ports/fnv-nx] [--selftest]
"""
import os
import re
import sys

HDR = "hostabi/include/fnvnx/hostapi.h"
HOST_PLAT = "toolchain/host/host_platform.inc"
HOST_SOCK = "toolchain/host/host_sock.inc"
SCAN = ["platform", "rt/guest"]
EXTS = (".c", ".cpp", ".h", ".inc", ".hpp", ".cc")
NOT_SERVED = set()   # services the header declares without a count are exactly those whose table row says NOT SERVED


def strip_comments(s):
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if s.startswith("/*", i):
            j = s.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("\n" * s.count("\n", i, j))
            i = j
        elif s.startswith("//", i):
            j = s.find("\n", i)
            i = n if j < 0 else j
        elif c == '"' or c == "'":
            j = i + 1
            while j < n and s[j] != c:
                j += 2 if s[j] == "\\" else 1
            out.append(s[i:j + 1])
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse_header(text, errs):
    code = strip_comments(text)
    svc, mod = {}, {}
    for m in re.finditer(r"^[ \t]*#define[ \t]+FNVNX_PLAT_([A-Z0-9_]+)[ \t]+(0[xX][0-9a-fA-F]+|\d+)[uU]?\b", code, re.M):
        name, num = m.group(1), int(m.group(2), 0)
        if name in ("SOCK_FIRST", "SOCK_LAST"):
            continue
        if name in svc:
            errs.append("%s: service name %s defined twice" % (HDR, name))
        svc[name] = num
    for m in re.finditer(r"^[ \t]*#define[ \t]+FNVNX_EXT_([A-Z0-9_]+)[ \t]+(0[xX][0-9a-fA-F]+|\d+)[uU]?\b", code, re.M):
        name, num = m.group(1), int(m.group(2), 0)
        if num < 64 and name not in ("NO_MODULE", "NO_SERVICE", "BAD_NARGS"):
            mod[name] = num
    byn = {}
    for name, num in svc.items():
        byn.setdefault(num, []).append(name)
    for num, names in sorted(byn.items()):
        if len(names) > 1:
            errs.append("%s: service number %d defined twice: %s" % (HDR, num, ", ".join(sorted(names))))
    bym = {}
    for name, num in mod.items():
        bym.setdefault(num, []).append(name)
    for num, names in sorted(bym.items()):
        if len(names) > 1:
            errs.append("%s: module number %d defined twice: %s" % (HDR, num, ", ".join(sorted(names))))
    # argument counts: rows of the totality table (comment text) "<num> <NAME> n=<k>"
    count, notserved = {}, set()
    for m in re.finditer(r"^[ \t]*\*?[ \t]*(\d+)[ \t]+([A-Z][A-Z0-9_]*)[ \t]+(n=(\d+)|NOT SERVED)", text, re.M):
        num, name = int(m.group(1)), m.group(2)
        if name not in svc:
            errs.append("%s: table row %d %s names no FNVNX_PLAT_ service" % (HDR, num, name))
            continue
        if svc[name] != num:
            errs.append("%s: table row says %s is %d, the #define says %d" % (HDR, name, num, svc[name]))
        if name in count or name in notserved:
            errs.append("%s: table has two rows for %s" % (HDR, name))
        if m.group(4) is not None:
            count[name] = int(m.group(4))
        else:
            notserved.add(name)
    # the socket list
    m = re.search(r"n = the count each service states \(([^)]*)\)", re.sub(r"\n[ \t]*\*[ \t]*", " ", text))
    socks = {k for k in svc if k.startswith("SOCK_")}
    if not m:
        errs.append("%s: no socket count list ('n = the count each service states (...)')" % HDR)
    else:
        first = True
        listed = {}
        for it in re.finditer(r"([A-Z0-9_]+)[ \t]+(\d+)", m.group(1)):
            nm = it.group(1)
            if not nm.startswith("SOCK_"):
                nm = "SOCK_" + nm
            if nm in listed:
                errs.append("%s: socket count list names %s twice" % (HDR, nm))
            listed[nm] = int(it.group(2))
        if set(listed) != socks:
            errs.append("%s: socket count list differs from the FNVNX_PLAT_SOCK_* services: %s" %
                        (HDR, sorted(set(listed) ^ socks)))
        for k, v in listed.items():
            if k in count:
                errs.append("%s: %s has a count in the table and in the socket list" % (HDR, k))
            count[k] = v
    for name in svc:
        if name not in count and name not in notserved:
            errs.append("%s: service %s has no declared argument count (no table row 'n=')" % (HDR, name))
    return svc, mod, count, notserved


# ---- C parsing of the scanned sources
def split_args(s):
    args, depth, cur, i = [], 0, [], 0
    q = None
    while i < len(s):
        c = s[i]
        if q:
            cur.append(c)
            if c == "\\" and i + 1 < len(s):
                cur.append(s[i + 1])
                i += 1
            elif c == q:
                q = None
        elif c in "\"'":
            q = c
            cur.append(c)
        elif c in "([{":
            depth += 1
            cur.append(c)
        elif c in ")]}":
            depth -= 1
            cur.append(c)
        elif c == "," and depth == 0:
            args.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        i += 1
    last = "".join(cur).strip()
    if last or args:
        args.append(last)
    return args


def match_paren(s, i):
    """s[i] == '(' -> index after the matching ')'"""
    depth, q = 0, None
    while i < len(s):
        c = s[i]
        if q:
            if c == "\\":
                i += 1
            elif c == q:
                q = None
        elif c in "\"'":
            q = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


FUNC_RE = re.compile(r"(?:^|[;}])\s*(?:[A-Za-z_][\w \t\*]*?[ \t\*])?([A-Za-z_]\w*)\s*\(([^;{}()]*(?:\([^()]*\)[^;{}()]*)*)\)\s*(?:__attribute__\s*\(\(.*?\)\)\s*)?\{", re.M)


def functions(code):
    """[(name, [param names], body_start, body_end)] of the top-level function definitions"""
    out, i, n = [], 0, len(code)
    depth, tops = 0, []
    # find top-level brace blocks
    start = None
    for k, c in enumerate(code):
        if c == "{":
            if depth == 0:
                start = k
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start is not None:
                tops.append((start, k + 1))
                start = None
    prev_end = 0
    for (a, b) in tops:
        head = code[prev_end:a + 1]
        prev_end = b
        m = None
        for m in FUNC_RE.finditer(head):
            pass
        if not m or m.end() != len(head):
            continue
        if m.group(1) in ("if", "for", "while", "switch", "struct", "union", "enum", "extern"):
            continue
        params = []
        for p in split_args(m.group(2)):
            mm = re.search(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*$", p)
            params.append(mm.group(1) if mm else "")
        out.append((m.group(1), params, a, b))
    return out


INT_RE = re.compile(r"^(0[xX][0-9a-fA-F]+|\d+)[uUlL]*$")
CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")


def calls(code, lo, hi):
    """[(name, [args], pos)] of every call-looking `name(` in code[lo:hi]"""
    out = []
    for m in CALL_RE.finditer(code, lo, hi):
        j = m.end() - 1
        e = match_paren(code, j)
        if e < 0 or e > hi:
            continue
        if re.match(r"\s*(?:__attribute__\s*\(\(.*?\)\)\s*)?\{", code[e:e + 200], re.S) and not code[:m.start()].rstrip().endswith(("=", "return", "(", ",")):
            continue   # a function definition, not a call
        out.append((m.group(1), split_args(code[j + 1:e - 1]), m.start()))
    return out


def line_of(code, pos):
    return code.count("\n", 0, pos) + 1


def main(argv):
    root = "."
    selftest = False
    a = list(argv[1:])
    while a:
        x = a.pop(0)
        if x == "--root":
            root = a.pop(0)
        elif x == "--selftest":
            selftest = True
    errs = []
    hdr_text = open(os.path.join(root, HDR)).read()
    svc, mod, count, notserved = parse_header(hdr_text, errs)

    files = []
    for d in SCAN:
        for dp, dn, fn in os.walk(os.path.join(root, d)):
            dn[:] = [x for x in sorted(dn) if x not in ("build", "out")]
            for f in sorted(fn):
                if f.endswith(EXTS):
                    files.append(os.path.join(dp, f))
    srcs = {}
    for f in files:
        try:
            srcs[f] = strip_comments(open(f, errors="replace").read())
        except OSError:
            pass
    rel = lambda f: os.path.relpath(f, root)

    # C: a scanned file must not define a name the header defines
    for f, code in srcs.items():
        for m in re.finditer(r"^[ \t]*#[ \t]*define[ \t]+FNVNX_(PLAT|EXT)_([A-Z0-9_]+)\b", code, re.M):
            kind, name = m.group(1), m.group(2)
            if (kind == "PLAT" and name in svc) or (kind == "EXT" and name in mod):
                errs.append("%s:%d: redefines FNVNX_%s_%s of hostapi.h" % (rel(f), line_of(code, m.start()), kind, name))

    # B: wrappers by rule, to a fixed point. wrappers[name] = (svc param index, count: ('lit', k) | ('par', index))
    wrappers_all = {}   # file -> {name: spec}: a wrapper is a static function of its file
    funcs = {f: functions(code) for f, code in srcs.items()}
    HCALL = "fnvnx_hcall4"

    def classify(arg, params):
        arg = arg.strip()
        if arg in params:
            return ("par", params.index(arg))
        return None

    changed = True
    while changed:
        changed = False
        for f, code in srcs.items():
            wrappers = wrappers_all.setdefault(f, {})
            for (fname, params, a, b) in funcs[f]:
                for (cn, args, pos) in calls(code, a, b):
                    if cn == HCALL:
                        if len(args) != 5 or not args[0].endswith("ext_call") or args[1] != "FNVNX_EXT_PLATFORM":
                            continue
                        sv, nn = args[2], args[4]
                    elif cn in wrappers:
                        si, ni = wrappers[cn]
                        if len(args) <= max(si, ni[1] if ni[0] == "par" else 0):
                            continue
                        sv = args[si]
                        nn = args[ni[1]] if ni[0] == "par" else str(ni[1])
                    else:
                        continue
                    sp = classify(sv, params)
                    if sp is None:
                        continue
                    nl = INT_RE.match(nn.strip())
                    if nl:
                        spec = ("lit", int(nl.group(1), 0))
                    else:
                        np_ = classify(nn, params)
                        if np_ is None:
                            continue
                        spec = np_
                    w = (sp[1], spec)
                    if fname not in wrappers:
                        wrappers[fname] = w
                        changed = True
                    elif wrappers[fname] != w:
                        errs.append("%s:%d: %s passes its parameters to ext_call in two different ways" %
                                    (rel(f), line_of(code, pos), fname))

    uses = []   # (file, line, service, n)
    for f, code in srcs.items():
        wrappers = wrappers_all.setdefault(f, {})
        owners = [(a, b, fname) for (fname, params, a, b) in funcs[f]]
        for (cn, args, pos) in calls(code, 0, len(code)):
            where = "%s:%d" % (rel(f), line_of(code, pos))
            if cn == HCALL and len(args) == 5 and args[0].endswith("ext_call"):
                mod_a, sv, nn = args[1], args[2], args[4]
            elif cn in wrappers:
                si, ni = wrappers[cn]
                if len(args) <= max(si, ni[1] if ni[0] == "par" else 0):
                    errs.append("%s: call of %s with too few arguments" % (where, cn))
                    continue
                if any(a <= pos < b and fn == cn for (a, b, fn) in owners):
                    continue   # recursion is not a use
                mod_a, sv = "FNVNX_EXT_PLATFORM", args[si]
                nn = args[ni[1]] if ni[0] == "par" else str(ni[1])
            else:
                continue
            if INT_RE.match(mod_a.strip()):
                errs.append("%s: module is the literal number %s (use a FNVNX_EXT_ name)" % (where, mod_a.strip()))
                continue
            if mod_a not in mod and not re.match(r"^FNVNX_EXT_[A-Z0-9_]+$", mod_a):
                if mod_a != "module":   # a pass-through of the caller's own parameter is a wrapper's business
                    errs.append("%s: module %r is not a FNVNX_EXT_ name" % (where, mod_a))
                continue
            if mod_a != "FNVNX_EXT_PLATFORM":
                continue   # other modules (GPU): not services of this table
            sv = sv.strip()
            owner = [fn for (a, b, fn) in owners if a <= pos < b]
            owner_params = []
            if owner:
                owner_params = next(p for (fn, p, a, b) in funcs[f] if fn == owner[0])
            if cn == HCALL and sv in owner_params:
                continue   # the wrapper's own body: a use is checked where the wrapper is called
            if cn in wrappers and sv in owner_params:
                continue   # a wrapper passing its parameter on (found as a wrapper above)
            if INT_RE.match(sv):
                errs.append("%s: service is the literal number %s (use the FNVNX_PLAT_ name)" % (where, sv))
                continue
            m = re.match(r"^FNVNX_PLAT_([A-Z0-9_]+)$", sv)
            if not m or m.group(1) not in svc:
                errs.append("%s: service %r is not declared in hostapi.h" % (where, sv))
                continue
            name = m.group(1)
            if name in notserved or name not in count:
                errs.append("%s: %s has no declared argument count" % (where, name))
                continue
            nl = INT_RE.match(nn.strip())
            if not nl:
                errs.append("%s: argument count of %s is not an integer literal: %r" % (where, name, nn.strip()))
                continue
            k = int(nl.group(1), 0)
            if k != count[name]:
                errs.append("%s: %s called with %d arguments, hostapi.h declares %d" % (where, name, k, count[name]))
            uses.append((rel(f), line_of(code, pos), name, k))

    # a FNVNX_PLAT_ service name anywhere else in code than as the service argument of a recognised call is not a use we can check
    for f, code in srcs.items():
        wrappers = wrappers_all.setdefault(f, {})
        for m in re.finditer(r"\bFNVNX_PLAT_([A-Z0-9_]+)\b", code):
            name = m.group(1)
            if name not in svc or re.match(r"^[ \t]*#[ \t]*define", code[code.rfind("\n", 0, m.start()) + 1:m.start() + 1]):
                continue
            # inside the argument list of a recognised call?
            ok = False
            for (cn, args, pos) in calls(code, 0, len(code)):
                if (cn == HCALL or cn in wrappers) and pos < m.start():
                    e = match_paren(code, code.index("(", pos))
                    if m.start() < e:
                        ok = True
                        break
            if not ok:
                errs.append("%s:%d: FNVNX_PLAT_%s outside a checked ext_call use" % (rel(f), line_of(code, m.start()), name))

    # D: served == declared
    served = {}   # name -> (file, count)
    def serve(name, k, where):
        if name in served:
            errs.append("%s: %s is served twice (also %s)" % (where, name, served[name][0]))
        served[name] = (where, k)
    for fname, pat in ((HOST_PLAT, r"\[FNVNX_PLAT_([A-Z0-9_]+)\]\s*=\s*\{\s*(\d+)\s*,"),
                       (HOST_SOCK, r"case\s+FNVNX_PLAT_([A-Z0-9_]+)\s*:\s*return\s+(\d+)\s*;")):
        try:
            hc = strip_comments(open(os.path.join(root, fname), errors="replace").read())
        except OSError:
            errs.append("%s: host dispatch file missing" % fname)
            continue
        if re.search(r"\bFNVNX_PLAT_SOCK_(FIRST|LAST)\b", hc):
            errs.append("%s: serves by a numeric range (FNVNX_PLAT_SOCK_FIRST/LAST); serve the declared names" % fname)
        for m in re.finditer(pat, hc):
            serve(m.group(1), int(m.group(2)), "%s:%d" % (fname, line_of(hc, m.start())))
    for name, (where, k) in sorted(served.items()):
        if name not in svc:
            errs.append("%s: serves %s, which hostapi.h does not declare" % (where, name))
        elif name in notserved:
            errs.append("%s: serves %s, which hostapi.h declares NOT SERVED" % (where, name))
        elif count.get(name) != k:
            errs.append("%s: %s takes %d arguments, hostapi.h declares %s" % (where, name, k, count.get(name)))
    for name in sorted(svc):
        if name not in notserved and name not in served:
            errs.append("hostapi.h declares %s (n=%s) but no host file serves it" % (name, count.get(name)))

    # E: FNVNX_PLAT_ names only in hostapi.h and in units
    nhdr = 0
    for dp, dn, fn in os.walk(root):
        dn[:] = [x for x in sorted(dn) if x not in ("build", "out", ".git", "journal")]
        for f in sorted(fn):
            if not f.endswith((".h", ".hpp", ".hh")):
                continue
            path = os.path.join(dp, f)
            if os.path.abspath(path) == os.path.abspath(os.path.join(root, HDR)):
                continue
            nhdr += 1
            try:
                txt = open(path, errors="replace").read()
            except OSError:
                continue
            for m in re.finditer(r"\bFNVNX_PLAT_[A-Z0-9_]+", txt):
                errs.append("%s:%d: %s in a header other than hostapi.h (only units name host services)" % (rel(path), line_of(txt, m.start()), m.group(0)))

    if selftest:
        print("selftest: %d services, %d modules, %d uses" % (len(svc), len(mod), len(uses)))
        print("wrappers: " + ", ".join(sorted("%s:%s" % (rel(f), n) for f, w in wrappers_all.items() for n in w)))
    for e in errs:
        print("check_hostapi: " + e)
    if errs:
        print("check_hostapi: %d violation(s)" % len(errs))
        return 1
    print("check_hostapi: ok (%d services declared, %d served, 0 differences; %d uses in %d files; %d other headers without FNVNX_PLAT_ names)" % (len(svc), len(served), len(uses), len(srcs), nhdr))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
