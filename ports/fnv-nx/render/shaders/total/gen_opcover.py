#!/usr/bin/env python3
"""gen_opcover.py: why each empty handler of the translator's MojoShader GLSL profile is not a stub. Records, no prose matching, no per-handler list.

    python3 -I gen_opcover.py MOJOSHADER_TREE SM_TABLE_JSON OP_SYNTAX_TSV OUT_COVER_TSV OUT_RESERVED_TSV [--check]

MOJOSHADER_TREE is the (patched) tree the translator root compiles (external `mojoshader`, plus translator/mojoshader*.patch). It is read, never
changed. Every fact comes from its own code or from op_syntax.tsv (gen_opsyntax.py):

  universe   every instruction-table row (mojoshader_internal.h, MOJOSHADER_DO_INSTRUCTION_TABLE, index = token value = sm_table `opnum`) and the
             profile hooks the token parsers call (parse_phase_token -> phase_emitter, parse_end_token -> end_emitter).
  empty      a handler is empty iff its body is empty after comments are removed (emit_GLSL_<op> of every row; the GLSL hooks named by the
             DEFINE_PROFILE macro order <-> the Profile struct field order). parse_args_NULL is judged the same way for every row that uses it.
  class a    structural no-op: every sm_table form of the opcode has an op_syntax row `ok` with 0 destination, 0 source and 0 other operands.
  class b    the semantics is carried by another emitter. The state function `state_<op>` of the row (the instruction table's state hook) is read:
             (b1) the lists its callee add_* functions append to (`reglist_insert(ctx, &ctx->L, ...)`), and the profile hooks that process_definitions
                  runs over each list `for (item = ctx->L.next ...)` -> `ctx->profile->H_emitter` -> emit_GLSL_H (DEFINE_PROFILE order);
             (b2) the ctx fields the state function assigns (`ctx->f = ...`) and the non-empty emit_GLSL_* bodies that read them.
             The slot is accepted iff the covering set is non-empty and holds no empty handler (not itself, not another empty one).
  class c    the row is unreachable: its opcode_string is 0, and parse_instruction_token returns before the emitter call. The facts (function,
             lines of the test, the return, the only emitter call site) go to OUT_RESERVED_TSV as the input of L1's certificate; the body is not edited.
  none       no class applies: a red row. Nothing falls back.

OUT_COVER_TSV columns: slot key opnum op class covering evidence.  slot is `emit` (the opcode's emitter) or `args` (parse_args_NULL), key is the
sm_table key stage:form (one row per sm_table opcode entry) or, for a table row without a sm_table entry, `-:<OP>`. Rows are sorted; two runs over
the same inputs are byte-equal.
"""
import json, os, re, sys

ROW = re.compile(r'^INSTRUCTION(_STATE)?\((\w+),\s*(0|"[^"]*"),\s*(\d+),\s*(\w+),\s*(\w+),\s*(\w+)\)', re.M)


def strip_comments(s):
    s = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), s, flags=re.S)
    return re.sub(r"//[^\n]*", lambda m: " " * len(m.group(0)), s)


def body_of(src, name_re):
    """{name: (body text, line of the header)} of every function whose header matches name_re (stripped source, braces matched)."""
    out = {}
    for m in re.finditer(r"^[A-Za-z_][\w \*]*?\b(" + name_re + r")\s*\([^;{]*\)\s*\{", src, re.M):
        i = m.end() - 1
        depth, j = 0, i
        while j < len(src):
            c = src[j]
            if c in "\"'":                      # string / char literal: braces inside do not count
                q = c; j += 1
                while src[j] != q:
                    j += 2 if src[j] == "\\" else 1
            elif c == "{": depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0: break
            j += 1
        out[m.group(1)] = (src[i + 1:j], src.count("\n", 0, m.start()) + 1)
    return out


def load(tree):
    ints = open(os.path.join(tree, "mojoshader_internal.h")).read()
    t0 = ints.index("#if MOJOSHADER_DO_INSTRUCTION_TABLE")
    table = [(m.group(2), m.group(3), int(m.group(4)), m.group(5), bool(m.group(1))) for m in ROW.finditer(ints[t0:ints.index("#undef MOJOSHADER_DO_INSTRUCTION_TABLE", t0)])]
    msrc = open(os.path.join(tree, "mojoshader.c")).read()
    glsl = open(os.path.join(tree, "profiles", "mojoshader_profile_glsl.c")).read()
    return table, msrc, glsl


def main(argv):
    check = "--check" in argv
    a = [x for x in argv if not x.startswith("--")]
    if len(a) != 5:
        print(__doc__); return 1
    tree, tpath, spath, outc, outr = a
    table, msrc_raw, glsl_raw = load(tree)
    msrc, glsl = strip_comments(msrc_raw), strip_comments(glsl_raw)
    sm = json.load(open(tpath))["opcodes"]
    syn = {}
    for ln in open(spath).read().splitlines()[1:]:
        c = ln.split("\t")
        syn[c[0]] = c

    emit = body_of(glsl, r"emit_GLSL_\w+")                      # name -> (body, line)
    empty_emit = {n for n, (b, _) in emit.items() if not b.strip()}
    args_f = body_of(msrc, r"parse_args_NULL")
    args_null_empty_semantics = args_f["parse_args_NULL"][0].strip()  # `return 1;` : consumes no token
    funcs = body_of(msrc, r"(?:state|add|parse)_\w+|process_definitions|parse_instruction_token")
    # DEFINE_PROFILE order <-> Profile struct order: hook name H -> emit_GLSL_<suffix>
    ph = open(os.path.join(tree, "profiles", "mojoshader_profile.h")).read()
    fields = re.findall(r"^\s*emit_\w+\s+(\w+)_emitter;", strip_comments(ph), re.M)
    macro = re.search(r"#define DEFINE_PROFILE\(prof\)\s*\{(.*?)\n\},", msrc_raw, re.S).group(1)
    toks = re.findall(r"emit_##prof##_(\w+),", macro)
    hook_fn = {f: "emit_GLSL_" + s for f, s in zip(fields, toks)}
    if len(fields) != len(toks):
        print("opcover: Profile fields and DEFINE_PROFILE disagree"); return 2

    # lists -> hooks (process_definitions)
    pd = funcs["process_definitions"][0]
    list_hooks = {}
    parts = re.split(r"for\s*\(\s*item\s*=\s*ctx->(\w+)\.next", pd)
    for k in range(1, len(parts), 2):
        for h in re.findall(r"ctx->profile->(\w+)_emitter", parts[k + 1].split("for (item", 1)[0]):
            list_hooks.setdefault(parts[k], set()).add(h)

    def cover(opname):
        st = funcs.get("state_" + opname)
        if st is None:
            return [], "no state function"
        body = st[0]
        cov, ev = set(), []
        for callee in re.findall(r"\b(add_\w+)\s*\(", body):
            fb = funcs.get(callee)
            if not fb: continue
            for lst in re.findall(r"reglist_insert\(\s*ctx\s*,\s*&ctx->(\w+)", fb[0]):
                for h in sorted(list_hooks.get(lst, ())):
                    cov.add(hook_fn[h]); ev.append("state_%s>%s>ctx->%s>%s_emitter" % (opname, callee, lst, h))
        written = set(re.findall(r"ctx->(\w+)\s*=(?!=)", body))
        for f in sorted(written):
            for n, (b, _) in sorted(emit.items()):
                if b.strip() and re.search(r"ctx->%s\b(?!\s*=[^=])" % f, b):
                    cov.add(n); ev.append("state_%s writes ctx->%s, read by %s" % (opname, f, n))
        return sorted(cov), ";".join(ev) if ev else "state function feeds no emitter"

    by_num = {}
    for k, v in sm.items():
        if v: by_num.setdefault(v["opnum"], []).append(k)

    rows, reserved = [], []
    # emit slots: instruction-table rows, then the two token-parser hooks
    units = [(i, r[0], r[1], r[2], r[3], r[4]) for i, r in enumerate(table)]
    units += [(0xFFFD, "PHASE", '"PHASE"', 0, "NULL", False)]
    for idx, op, ostr, slots, args, st in units:
        keys = sorted(by_num.get(idx, []))
        fname = "emit_GLSL_phase" if idx == 0xFFFD else "emit_GLSL_" + op
        is_empty = fname in empty_emit
        cls, covering, evid = "own", "-", "non-empty body"
        if is_empty:
            allz = keys and all(syn.get(k, [None] * 10)[-1] == "ok" and syn[k][3:6] == ["0", "0", "0"] for k in keys)
            if ostr == "0":
                cls, evid = "c", "opcode_string 0: see OUT_RESERVED"
            elif allz:
                cls, evid = "a", "op_syntax: 0 dst 0 src 0 other in %d form(s)" % len(keys)
            else:
                cov, ev = cover(op)
                bad = [c for c in cov if c in empty_emit]
                if cov and not bad and st:
                    cls, covering, evid = "b", ",".join(cov), ev
                else:
                    cls, evid = "none", ev if cov else "no covering emitter"
        for k in (keys or ["-:" + op]):
            rows.append(("emit", k, idx, op, cls, covering, evid))
        if args == "NULL":
            if ostr == "0":
                acls, aev = "c", "opcode_string 0"
            elif keys and all(syn.get(k, [None] * 10)[-1] == "ok" and syn[k][3:6] == ["0", "0", "0"] for k in keys):
                acls, aev = "a", "op_syntax: 0 operands in %d form(s); parse_args_NULL body `%s`" % (len(keys), args_null_empty_semantics)
            else:
                bad = [k for k in keys if not (syn.get(k, [None] * 10)[-1] == "ok" and syn[k][3:6] == ["0", "0", "0"])]
                acls, aev = "none", "no 0-operand syntax row for %s" % (",".join(bad) if bad else "any form")
            for k in (keys or ["-:" + op]):
                rows.append(("args", k, idx, op, acls, "-", aev))

    # (c) facts
    pit = funcs["parse_instruction_token"][0]
    pit_line = funcs["parse_instruction_token"][1]
    def line_of(frag):
        i = msrc.index(frag)
        return msrc.count("\n", 0, i) + 1
    t = pit.index("opcode_string == NULL")
    ret = pit.index("return", t)
    emit_call = [m.start() for m in re.finditer(r"\bemitter\s*\(ctx\)", pit)]
    uses_ins_emitter = len(re.findall(r"(?<![\w>.])emitter\s*\(", msrc))
    ok_order = bool(emit_call) and t < ret < emit_call[0] and "}" in pit[t:ret].split("return")[0] + "}" and \
        re.search(r"if\s*\(\s*instruction->opcode_string\s*==\s*NULL\s*\)\s*\{[^}]*return", pit) is not None
    res_rows = [r for r in table if r[1] == "0"]
    res_ops = sorted({r[0] for r in res_rows})
    base = pit_line
    reserved.append(("function", "parse_instruction_token", "mojoshader.c:%d" % pit_line))
    reserved.append(("test", "instruction->opcode_string == NULL", "mojoshader.c:%d" % line_of("instruction->opcode_string == NULL")))
    reserved.append(("rejection", 'fail(ctx, "Unknown opcode.") ; return insttoks + 1', "mojoshader.c:%d" % line_of('fail(ctx, "Unknown opcode.")')))
    reserved.append(("emitter_call", "if (!isfail(ctx)) emitter(ctx)", "mojoshader.c:%d" % line_of("emitter(ctx);")))
    reserved.append(("order", "test < unconditional return < emitter call, in one function", "yes" if ok_order else "NO"))
    reserved.append(("emitter_use_sites", "instruction emitters are called only in parse_instruction_token (count of `emitter(` / `->emitter[` in mojoshader.c)", str(uses_ins_emitter)))
    reserved.append(("rows_opcode_string_0", "table rows with opcode_string 0: %d of %d; all named %s; shader_types UNKNOWN" % (len(res_rows), len(table), ",".join(res_ops)), "all" if all(r[3] == "NULL" for r in res_rows) else "NO"))
    reserved.append(("second_rejection", "(ctx->shader_type & instruction->shader_types) == 0 -> failf, MOJOSHADER_TYPE_UNKNOWN", "mojoshader.c:%d" % line_of("(ctx->shader_type & instruction->shader_types) == 0")))
    reserved.append(("glsl_body", "emit_GLSL_RESERVED: empty (comment: fails in the state machine)", "mojoshader_profile_glsl.c:%d" % (emit["emit_GLSL_RESERVED"][1])))

    rows.sort(key=lambda r: (r[0], r[1], r[3]))
    text = "slot\tkey\topnum\top\tclass\tcovering\tevidence\n" + "".join("\t".join(str(c) for c in r) + "\n" for r in rows)
    rtext = "fact\tdetail\tvalue\n" + "".join("\t".join(r) + "\n" for r in reserved)
    # global counts
    def counts(slot):
        keys = {}
        for r in rows:
            if r[0] == slot: keys.setdefault(r[1], r[4])
        ops = {}
        for r in rows:
            if r[0] == slot: ops.setdefault(r[3], set()).add(r[4])
        cf, co = {}, {}
        for k, c in keys.items(): cf[c] = cf.get(c, 0) + 1
        for o, cs in ops.items():
            for c in cs: co[c] = co.get(c, 0) + 1
        return cf, co
    red = [r for r in rows if r[4] == "none"]
    if check:
        same = os.path.isfile(outc) and open(outc).read() == text and os.path.isfile(outr) and open(outr).read() == rtext
        print("op_cover: %s" % ("byte-equal" if same else "DIFFERS"))
        return 0 if same and not red and ok_order else 2
    open(outc, "w").write(text); open(outr, "w").write(rtext)
    for slot in ("emit", "args"):
        cf, co = counts(slot)
        print("slot %-4s forms by class %s ; table ops by class %s" % (slot, dict(sorted(cf.items())), dict(sorted(co.items()))))
    print("empty emitters: %s" % ",".join(sorted(empty_emit)))
    for r in red: print("  RED %s %s (%s): %s" % (r[0], r[1], r[3], r[6]))
    print("reserved: order %s" % ("ok" if ok_order else "NOT ok"))
    return 2 if (red or not ok_order) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
