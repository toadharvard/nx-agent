#!/usr/bin/env python3
"""Census of every failure call site of the translator root (measurement tool, not a build).
usage: fail_census.py SM_TABLE.json OUT.tsv FILE...      (FILE: the sources of the root after all patches: the patched mojoshader tree and src/*.c)
Sites: every call of `fail`/`failf` and of any wrapper (a function whose whole body is one call of fail/failf, to a fixpoint).
For each site the enclosing function and the guards (the `if`/`else` conditions of the brace nest that contains the call, innermost last) are
extracted from the text with comments and string literals removed. Classification, by fixed rule on the guard tokens, first match wins; everything
that no rule proves is (o), so (o) is the safe class:
  r  resource: the guard or the call message is an allocation/out-of-memory check (token malloc/calloc/realloc/Malloc/alloc/out_of_memory/NULL pointer test of
     a variable assigned by an allocation call in the same function).
  v  rejected by the front end: the site is in emit_<PROFILE>_<OP> (or is guarded by a shader-model predicate: shader_version_atleast, shader_is_pixel,
     shader_is_vertex, dk_ps1x, support_glsldk) and <OP> is an opcode row of SM_TABLE.json whose `models` are all ps_1_x (so the fallthrough runs only for a shader model >= ps_2_0, which the row excludes);
     the row (key, models) is cited in OUT.tsv.
  o  other.
OUT.tsv: file<TAB>line<TAB>function<TAB>class<TAB>citation-or-guard. Prints global counts."""
import json, re, sys, collections
tab, out = sys.argv[1], sys.argv[2]
files = sys.argv[3:]
ops = json.load(open(tab))["opcodes"]
oprow = {}
for k, v in ops.items(): oprow.setdefault(k.split(":", 1)[1].upper(), []).append((k, v["models"]))
def strip(t):
    t = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), t, flags=re.S)
    t = re.sub(r"//[^\n]*", "", t)
    t = re.sub(r'"(\\.|[^"\\\n])*"', lambda m: '"' + " " * (len(m.group(0)) - 2) + '"', t)
    return t
src = {f: strip(open(f).read()) for f in files}
raw = {f: open(f).read() for f in files}
# wrappers: functions whose body is exactly one fail/failf call
wrap = {"fail", "failf"}
def funcs(t):
    for m in re.finditer(r"^[A-Za-z_][^\n;{}()]*?\b([A-Za-z_]\w*)\s*\([^;{}]*?\)\s*\n?\{", t, flags=re.M):
        s = m.end() - 1; d = 0
        for i in range(s, len(t)):
            if t[i] == "{": d += 1
            elif t[i] == "}":
                d -= 1
                if d == 0: yield m.group(1), s, i; break
changed = True
while changed:
    changed = False
    for f, t in src.items():
        for name, s, e in funcs(t):
            if name in wrap: continue
            body = t[s + 1:e].strip()
            if re.fullmatch(r"(?:%s)\s*\([^;]*\)\s*;" % "|".join(sorted(wrap)), body, flags=re.S):
                wrap.add(name); changed = True
call = re.compile(r"\b(%s)\s*\(" % "|".join(sorted(wrap)))
rows = []
for f, t in src.items():
    for name, s, e in funcs(t):
        if name in wrap: continue
        body = t[s:e + 1]
        # brace nest with guards
        guards, stack, pos = [], [], 0
        events = []
        for m in re.finditer(r"[{}]|\b(?:else\s+if|if|else|while|for|switch)\b", body): events.append(m)
        # linear scan: remember the last control header before each '{'
        last_hdr = None; nest = []
        i = 0
        calls = [(m.start(), m.group(1)) for m in call.finditer(body)]
        for ci, (cpos, cname) in enumerate(calls):
            nest = []; hdr = None; j = 0
            for m in re.finditer(r"\{|\}|\b(?:else\s+if|if|else)\b", body[:cpos]):
                tok = m.group(0)
                if tok in ("if", "else if"):
                    k = m.end(); d = 0
                    while k < cpos and not (body[k] == "(" ): k += 1
                    st = k
                    while k < len(body):
                        if body[k] == "(": d += 1
                        elif body[k] == ")":
                            d -= 1
                            if d == 0: break
                        k += 1
                    hdr = body[st + 1:k].strip() if st < cpos else None
                    hdr_end = k
                elif tok == "else":
                    hdr = "else"
                elif tok == "{":
                    nest.append(hdr); hdr = None
                elif tok == "}":
                    if nest: nest.pop()
            # braceless if directly before the call
            pre = body[:cpos].rstrip()
            gl = [g for g in nest if g]
            if hdr and pre.endswith(")") and hdr != "else": gl.append(hdr)
            line = src[f][:s + cpos].count("\n") + 1
            msg = re.search(r'"([^"]*)"', raw[f][len(raw[f][:0]):].split("\n")[line - 1] if line - 1 < len(raw[f].split("\n")) else "")
            rows.append([f, line, name, gl, msg.group(1) if msg else ""])
alloc = re.compile(r"\b(malloc|calloc|realloc|Malloc|alloc|out_of_memory)\b")
modelp = re.compile(r"\b(shader_version_atleast|shader_is_pixel|shader_is_vertex|dk_ps1x|support_glsldk)\b")
res = []
cnt = collections.Counter()
for f, line, fn, gl, msg in rows:
    g = " && ".join(gl)
    cls, cite = "o", g or "(unguarded)"
    if alloc.search(g) or "out of memory" in msg.lower(): cls, cite = "r", g or msg
    else:
        m = re.match(r"emit_[A-Z0-9_]+?_([A-Z0-9_]+)$", fn)
        op = None
        if m:
            c = m.group(1)
            for k in oprow:
                if c == k: op = k
        if op and (not gl or modelp.search(g)) and all(mods and all(x.startswith("ps_1_") for x in mods) for _, mods in oprow[op]):
            cls = "v"; cite = "; ".join("%s models %s" % (k, ",".join(mods)) for k, mods in oprow[op])
    cnt[cls] += 1
    res.append((f, line, fn, cls, cite))
with open(out, "w") as o:
    for r in res: o.write("%s\t%d\t%s\t%s\t%s\n" % r)
print("wrappers", sorted(wrap)); print("sites", len(res), dict(cnt))
for r in res:
    if r[3] == "o": print("o:", r[0].split("/")[-1], r[1], r[2], r[4][:80])
