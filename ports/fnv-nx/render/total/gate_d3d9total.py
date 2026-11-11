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
Certified, SM opcodes: none. Whether the SM1-3 translator has an emitter for an opcode is not a build error today (see SM_DETAIL), so the
  part is reported uncertified; the gate does not guess from names or text.  Result extras: "parts"."""
import importlib.util, json, os, shutil, subprocess, sys, tempfile, time
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, "..", ".."))
FRONT = os.path.join(PORT, "render", "d3d9", "front")
BACKEND = os.path.join(PORT, "render", "d3d9", "backend")
SM_TABLE = os.path.join(PORT, "render", "shaders", "total", "sm_table.json")
STRICT = ["-Wall", "-Wextra", "-Werror", "-Werror=switch", "-Werror=switch-enum"]
SM_DETAIL = ("SM1-3: MojoShader (pinned ad5dff8 + render/shaders/mojoshader.patch) dispatches through instructions[], positional (index = opcode "
             "value) and generated from the INSTRUCTION/INSTRUCTION_STATE X-macro list with emit_<PROFILE>_<op> from PROFILE_EMITTERS(op): a listed "
             "opcode without an emitter function is a link error already. NOT a build error: (1) the (stage, opcode value) rows of sm_table.json "
             "against that list (an opcode value that falls on a RESERVED row, whose emitter is an empty function that fails only at run time in the "
             "state machine; a stage mask t that excludes the stage), (2) emitters that handle only part of an opcode's forms. Build error needs: "
             "a header generated from sm_table.json (rows STAGE_MASK, OPCODE_VALUE, NAME), included by the patched mojoshader.c, with "
             "_Static_assert(OPIDX_<NAME> == OPCODE_VALUE) from an enum built by the same X-macro (an unknown NAME is an undeclared identifier) "
             "and a static assert on the row's stage mask; the gate then compiles the patched source (needs the pinned clone).")


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
        universe += sm_ids
        uncert += sm_ids
        cert = len(universe) - len(uncert)
        res = {"format": "fnvnx-gate/1", "name": "d3d9total", "status": "green" if not uncert else "red", "universe": len(universe), "certified": cert,
               "uncertified": uncert[:50],
               "detail": "enum values %d of %d certified (header==spec %s, front builds %s, sensitivity %d of %d enumerations); SM opcodes 0 of %d certified: %s; %.1f s"
                         % (n_enum - sum(1 for u in uncert if u.startswith("enum:")), n_enum, header_ok, built, sum(1 for s in sens.values() if
                            s["fails_with_switch_enum"] and s["compiles_without"]), len(sens), len(sm_ids), SM_DETAIL, time.time() - t0),
               "parts": {"enums": {"universe": n_enum, "certified": n_enum - sum(1 for u in uncert if u.startswith("enum:")), "sensitivity": sens,
                                   "front_error": berr},
                         "sm": {"universe": len(sm_ids), "certified": 0}}}
        with open(os.path.join(os.environ["GATE_OUT"], "d3d9total.json"), "w") as fh:
            json.dump(res, fh, sort_keys=True)
        return 0 if not uncert else 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
