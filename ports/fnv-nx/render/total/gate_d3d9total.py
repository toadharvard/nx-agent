#!/usr/bin/env python3
"""Gate "d3d9total" (fnvnx-gate/1) for verify/verdict.py: the D3D9 -> backend translation is total by construction.
    GATE_OUT=<dir> python3 -I ports/fnv-nx/render/total/gate_d3d9total.py          run in the checkout root; writes GATE_OUT/d3d9total.json.
    Exit 0 iff green. Needs gcc, g++ (-m32 for the header probe) and python3; about 20 s.
Universe = every (enumeration, enumerator) of the spec's enums that the translation consumes by switch (render/d3d9/front/gen_enums.py
SWITCHED, from render/d3d9/spec/d3d9_spec.json plus the vendor FOURCCs) + every SM1-3 opcode row of render/shaders/total/sm_table.json.
Certified, enum values: the front (render/d3d9/front/front_*.c) compiles with -Werror=switch -Werror=switch-enum against the header regenerated
  from the spec (equal to the committed one), AND the enumeration is switched in the front: the same sources compiled against a copy of the
  header with one extra enumerator of that enumeration FAIL with the flags and COMPILE without them (so the failure is the missing case, no
  other error). An enumeration that fails the sensitivity check certifies none of its values.
Certified, SM commands (the 171 rows of render/shaders/total/sm_table.json = (stage, opcode form) of SM1-3): the pinned MojoShader clone (declared
  input: FNVNX_SHADERS_DEPS/mojoshader, default render/shaders/build/deps/mojoshader; its git object database must hold the commit MOJO_PIN of
  render/shaders/build.sh) is exported at that commit, render/shaders/mojoshader.patch is applied and render/shaders/total/fnvnx_sm_rows.h (equal to
  gen_sm_rows.py's output for sm_table.json, one row line per command) is put next to it; the translator sources (mojoshader.c, the glsl profile)
  must compile, which evaluates the _Static_asserts of fnvnx_sm_check.h: every row's opcode is an instructions[] entry at its opcode value
  (not RESERVED, emitter linked for every profile), its shader-type mask holds the stage, comparison forms have their control value in the
  translator's comparison table, token rows (phase) supply the token the parser compares with.  Sensitivity, as for the enums: in scratch copies
  (1) a removed table entry, (2) a stage mask without the stage, (3) a row with an unknown name must each FAIL with the assertion, else no row is
  certified.  Without the clone nothing is certified (red).  Not decided from runs, names or text.  Result extras: "parts"."""
import importlib.util, json, os, shutil, subprocess, sys, tempfile, time
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, "..", ".."))
FRONT = os.path.join(PORT, "render", "d3d9", "front")
BACKEND = os.path.join(PORT, "render", "d3d9", "backend")
SM_TABLE = os.path.join(PORT, "render", "shaders", "total", "sm_table.json")
STRICT = ["-Wall", "-Wextra", "-Werror", "-Werror=switch", "-Werror=switch-enum"]
SHADERS = os.path.join(PORT, "render", "shaders")
CLONE = os.path.join(os.environ.get("FNVNX_SHADERS_DEPS", os.path.join(SHADERS, "build", "deps")), "mojoshader")   # declared input
# the profiles of the translator build (render/shaders/build.sh keeps glsl only; the others are compiled out)
SM_CC = ["cc", "-fsyntax-only", "-Wall"] + ["-DSUPPORT_PROFILE_%s=0" % p for p in
         ("ARB1", "ARB1_NV", "D3D", "GLSL120", "GLSPIRV", "HLSL", "METAL", "SPIRV")]
SM_SOURCES = ["mojoshader.c", os.path.join("profiles", "mojoshader_profile_glsl.c")]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build(front_dir, flags):
    """compile every front source syntax-only; returns (ok, last stderr)"""
    srcs = sorted(f for f in os.listdir(front_dir) if f.endswith(".c"))
    err = ""
    for f in srcs:
        r = subprocess.run(["gcc", "-std=c11", "-fsyntax-only", *flags, "-I", BACKEND, "-I", front_dir, os.path.join(front_dir, f)],
                           capture_output=True, text=True)
        if r.returncode:
            return False, r.stderr[-400:]
    return True, err


def front_copy(tmp, name, header_text):
    d = os.path.join(tmp, name)
    shutil.copytree(FRONT, d, ignore=shutil.ignore_patterns("__pycache__", "*.so"))
    open(os.path.join(d, "d3d9_enums.gen.h"), "w").write(header_text)
    return d


def sm_compile(tree):
    """compile the translator sources of a clone export; returns (ok, stderr)"""
    for src in SM_SOURCES:
        r = subprocess.run(SM_CC + ["-I", tree, os.path.join(tree, src)], capture_output=True, text=True)
        if r.returncode:
            return False, r.stderr
    return True, ""


def sm_part(tmp, gen_rows):
    """returns (certified row keys, detail dict)"""
    sm = json.load(open(SM_TABLE))["opcodes"]
    info = {"clone": CLONE, "rows_header_equals_generated": False, "baseline_compiles": False, "sensitivity": {}}
    rows_path = os.path.join(SHADERS, "total", "fnvnx_sm_rows.h")
    info["rows_header_equals_generated"] = open(rows_path).read() == gen_rows.render()
    pin = None
    for line in open(os.path.join(SHADERS, "build.sh")):
        if line.startswith("MOJO_PIN="):
            pin = line[len("MOJO_PIN="):].split()[0].strip("\"'")
    if not pin or len(pin) != 40 or any(c not in "0123456789abcdef" for c in pin):
        info["error"] = "MOJO_PIN not found in render/shaders/build.sh"
        return [], info
    tree = os.path.join(tmp, "mojo")
    os.makedirs(tree)
    ar = subprocess.run("git -C '%s' archive %s | tar -x -C '%s'" % (CLONE, pin, tree), shell=True, capture_output=True, text=True)
    if ar.returncode:
        info["error"] = "clone %s lacks commit %s: %s" % (CLONE, pin, ar.stderr[-200:])
        return [], info
    ap = subprocess.run(["git", "apply", os.path.join(SHADERS, "mojoshader.patch")], cwd=tree, capture_output=True, text=True)
    if ap.returncode:
        info["error"] = "mojoshader.patch does not apply: " + ap.stderr[-200:]
        return [], info
    shutil.copy(rows_path, os.path.join(tree, "fnvnx_sm_rows.h"))
    open(os.path.join(tree, "mojoshader_version.h"), "w").write('#define MOJOSHADER_VERSION -1\n#define MOJOSHADER_CHANGESET "gate"\n')   # cmake-generated, not in the export
    info["baseline_compiles"], err = sm_compile(tree)
    info["error"] = err[-400:]

    def edit(path, fn):
        t = open(path).read()
        u = fn(t)
        assert u != t
        open(path, "w").write(u)

    def probe(name, mutate):
        d = os.path.join(tmp, "probe_" + name)
        shutil.copytree(tree, d)
        mutate(d)
        ok, e = sm_compile(d)
        info["sensitivity"][name] = (not ok) and ("static assertion failed" in e or "undeclared" in e or "not declared" in e)

    internal = lambda d: os.path.join(d, "mojoshader_internal.h")
    probe("removed_table_entry", lambda d: edit(internal(d),
          lambda t: "\n".join(l for l in t.split("\n") if not l.startswith("INSTRUCTION(MOV, "))))
    probe("stage_not_in_mask", lambda d: edit(internal(d),
          lambda t: t.replace('INSTRUCTION(DST, "DST", 1, DSS, MOJOSHADER_TYPE_VERTEX', 'INSTRUCTION(DST, "DST", 1, DSS, MOJOSHADER_TYPE_PIXEL')))
    probe("unknown_name", lambda d: open(os.path.join(d, "fnvnx_sm_rows.h"), "a").write('FNVNX_SM_OP("probe", MOJOSHADER_TYPE_PIXEL, 0, NOSUCHOP)\n'))
    ids = set()
    for line in open(rows_path):
        if line.startswith("FNVNX_SM_OP(") or line.startswith("FNVNX_SM_TOKEN("):
            ids.add(line.split('"')[1])
    good = info["rows_header_equals_generated"] and info["baseline_compiles"] and len(info["sensitivity"]) == 3 and all(info["sensitivity"].values())
    return ([k for k in sorted(sm) if k in ids] if good else []), info


def main():
    t0 = time.time()
    gen = load(os.path.join(FRONT, "gen_enums.py"), "gen_enums")
    tmp = tempfile.mkdtemp(prefix="d3d9total_")
    try:
        vals = gen.build()
        text = gen.render(vals)
        committed = open(os.path.join(FRONT, "d3d9_enums.gen.h")).read()
        header_ok = text == committed
        base = front_copy(tmp, "base", text)
        built, berr = build(base, STRICT)
        universe, uncert, sens = [], [], {}
        for e in gen.SWITCHED:
            probe = {k: list(v) for k, v in vals.items()}
            used = {v for _, v in probe[e]}
            extra = next(v for v in range(0x40000000, 0x40000100) if v not in used)
            probe[e].append((e + "_GATEPROBE", extra))
            d = front_copy(tmp, "probe_" + e, gen.render(probe))
            fails_strict = not build(d, STRICT)[0]
            ok_plain = build(d, ["-Wall", "-Wextra", "-Werror", "-Wno-switch", "-Wno-switch-enum"])[0]   # same flags minus the switch checks
            sens[e] = {"fails_with_switch_enum": fails_strict, "compiles_without": ok_plain}
            good = header_ok and built and fails_strict and ok_plain
            for n, _ in vals[e]:
                universe.append("enum:%s:%s" % (e, n))
                if not good:
                    uncert.append("enum:%s:%s" % (e, n))
        n_enum = len(universe)
        sm = json.load(open(SM_TABLE))["opcodes"]
        sm_ids = ["sm:" + k for k in sorted(sm)]
        sm_cert, sm_info = sm_part(tmp, load(os.path.join(PORT, "render", "shaders", "total", "gen_sm_rows.py"), "gen_sm_rows"))
        sm_ok = {"sm:" + k for k in sm_cert}
        universe += sm_ids
        uncert += [i for i in sm_ids if i not in sm_ok]
        cert = len(universe) - len(uncert)
        n_enum_ok = n_enum - sum(1 for u in uncert if u.startswith("enum:"))
        res = {"format": "fnvnx-gate/1", "name": "d3d9total", "status": "green" if not uncert else "red", "universe": len(universe), "certified": cert,
               "uncertified": uncert[:50],
               "detail": "enum values %d of %d certified (header==spec %s, front builds %s, sensitivity %d of %d enumerations); SM commands %d of %d certified "
                         "(clone %s, rows header == generated %s, patched translator compiles %s, sensitivity %s, %s); %.1f s"
                         % (n_enum_ok, n_enum, header_ok, built, sum(1 for s in sens.values() if s["fails_with_switch_enum"] and s["compiles_without"]),
                            len(sens), len(sm_cert), len(sm_ids), CLONE, sm_info["rows_header_equals_generated"], sm_info["baseline_compiles"],
                            json.dumps(sm_info["sensitivity"], sort_keys=True), sm_info.get("error", "")[-200:].replace("\n", " "), time.time() - t0),
               "parts": {"enums": {"universe": n_enum, "certified": n_enum_ok, "sensitivity": sens, "front_error": berr},
                         "sm": {"universe": len(sm_ids), "certified": len(sm_cert), "info": sm_info}}}
        with open(os.path.join(os.environ["GATE_OUT"], "d3d9total.json"), "w") as fh:
            json.dump(res, fh, sort_keys=True)
        return 0 if not uncert else 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
