#!/usr/bin/env python3
"""test_bridge.py: the guest bridge of d3d9 (render/d3d9/gen/gen_bridge.py, dev/face.h).  python3 -I tests/l6/bridge/test_bridge.py
 a  guest-side calls through key tables: exports return faces, out handles are faces with identity, the class table is the object's own
 b  rows: one per spec method (dll !<iface>, ordinal = slot) + one per signed export; every native symbol is defined (the link) and unique
 c  a row missing from the import table is reported by nx_bridge_init
 e  private data D3DSPD_IUNKNOWN of a guest object: AddRef / Release go through fnvnx_guest_invoke (app_unk_guest.cpp)
 d  generator totality: a spec item without a rule stops the generator (exit 2)"""
import copy, json, os, subprocess, sys, tempfile, shutil
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install()
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
D9 = os.path.join(ROOT, "render", "d3d9"); DEV = os.path.join(D9, "dev"); GEN = os.path.join(D9, "gen"); SPEC = os.path.join(D9, "spec", "d3d9_spec.json"); ENV = os.path.join(D9, "env")
INC = os.path.join(ROOT, "shim", "include"); PY = [sys.executable, "-I"]


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


def main():
    fails = []; tmp = tempfile.mkdtemp(prefix="d9br_")
    for cmd in (PY + [os.path.join(GEN, "gen_impl.py"), tmp, SPEC], PY + [os.path.join(GEN, "gen_wine_ids.py"), tmp], PY + [os.path.join(DEV, "state_model.py"), tmp, SPEC], PY + [os.path.join(GEN, "gen_bridge.py"), tmp, SPEC],
                PY + [os.path.join(ENV, "gen_env.py"), tmp]):
        r = sh(cmd)
        if r.returncode: print("GEN FAILED " + " ".join(cmd[2:3]) + "\n" + (r.stderr or r.stdout)[-800:]); return 1
    rows = [l.rstrip("\n").split("\t") for l in open(os.path.join(tmp, "d9bridge_rows.tsv"))]
    spec = json.load(open(SPEC)); want = [("!" + m["iface"], m["name"], str(m["slot"])) for m in spec["methods"]] + [("d3d9.dll", e["name"], "") for e in spec["exports"] if not e.get("undocumented")]
    if sorted(want) != sorted((r[0], r[1], r[2]) for r in rows): fails.append("b: rows != spec methods + signed exports")
    if any(len(r) != 8 or r[3] != "stdcall" or r[7] != "sym" or r[4] not in "vif" or set(r[5]) - set("if") or (r[0][0] == "!" and r[5][:1] != "i") for r in rows): fails.append("b: row shape")
    if len({r[6] for r in rows}) != len(rows) or len({(r[0], r[1]) for r in rows}) != len(rows): fails.append("b: symbols or (dll, name) not unique")
    with open(os.path.join(tmp, "rows.gen.h"), "w") as f:
        f.write('extern "C" {\n' + "".join(f"void {r[6]}();\n" for r in rows) + "}\nstruct Row { const char* dll; const char* name; uint32_t ordinal; void* fn; };\nstatic const Row g_rows[] = {\n")
        f.write("".join(f'    {{"{r[0]}", "{r[1]}", {r[2] or "0xffffffffu"}, (void*){r[6]}}},\n' for r in rows) + f"}};\nenum {{ G_NROWS = {len(rows)} }};\n")
    fl = ["g++", "-m32", "-std=c++17", "-fno-exceptions", "-fno-rtti", "-O0", "-w", "-fpermissive", "-I", INC, "-I", tmp, "-I", DEV, "-I", HERE]
    srcs = [os.path.join(os.path.join(ROOT, "tests", "l6", "native") if s == "app_unk_native.cpp" else DEV, s) for s in ("obj.cpp", "app_unk_native.cpp", "d3d9_adapter.cpp", "d3d9_exports.cpp")] + [os.path.join(tmp, s) for s in ("d9impl_unk.gen.cpp", "d9bridge.gen.cpp")] + \
           [os.path.join(tmp, s) for s in os.listdir(tmp) if s.startswith("env_") and s.endswith(".cpp")] + [os.path.join(HERE, "t_bridge.cpp")]
    exe = os.path.join(tmp, "t_bridge")
    r = sh(fl + srcs + ["-o", exe, "-Wl,--unresolved-symbols=ignore-all"])   # methods without a body yet (G-METHOD) stay unresolved; this test calls none of them
    if r.returncode: print("BUILD FAILED\n" + r.stderr[-2500:]); return 1
    for arg, parts in (([], "ab"), (["hide"], "c")):
        r = sh([exe] + arg); print(r.stdout[-1200:], end="")
        import re
        got = {m.group(1): (int(m.group(2)), int(m.group(3))) for m in re.finditer(r"PART (\w): (\d+) checks, (\d+) failed", r.stdout)}
        for p in parts if not arg else "a":
            if r.returncode or p not in got or got[p][1] or not got[p][0]: fails.append(f"{parts}: {got.get(p)} rc={r.returncode}")
    # e: application IUnknown objects through the lifter runtime's guest-call primitive
    exe2 = os.path.join(tmp, "t_appunk")
    r = sh(fl + [os.path.join(DEV, "obj.cpp"), os.path.join(DEV, "app_unk_guest.cpp"), os.path.join(tmp, "d9impl_unk.gen.cpp"), os.path.join(HERE, "t_appunk.cpp"), "-o", exe2, "-Wl,--unresolved-symbols=ignore-all"])
    if r.returncode: print("BUILD FAILED (e)\n" + r.stderr[-1500:]); return 1
    r = sh([exe2]); print(r.stdout[-600:], end="")
    if r.returncode or "PART e:" not in r.stdout or ", 0 failed" not in r.stdout: fails.append("e: rc=%d" % r.returncode)
    # d: totality of the generator
    muts = [("struct by value", lambda s: next(p for m in s["methods"] for p in m["params"] if p["kind"] == "struct").update(byref=False)),
            ("8-byte value", lambda s: next(p for m in s["methods"] for p in m["params"] if p["kind"] == "value" and not p["byref"]).update(size=8)),
            ("unknown kind", lambda s: next(p for m in s["methods"] for p in m["params"] if p["kind"] == "enum").update(kind="thing")),
            ("slot gap", lambda s: s["methods"].pop(5)),
            ("in handle by reference", lambda s: next(p for m in s["methods"] for p in m["params"] if p["kind"] == "handle" and p["byref"]).update(dir="in")),
            ("unknown return kind", lambda s: s["methods"][10].update(ret_kind="thing"))]
    for name, f in muts:
        s2 = copy.deepcopy(spec); f(s2); p2 = os.path.join(tmp, "mut.json"); json.dump(s2, open(p2, "w"))
        r = sh(PY + [os.path.join(GEN, "gen_bridge.py"), os.path.join(tmp, "mut"), p2]); ok = r.returncode == 2
        print(f"generator mutation ({name}): {'refused' if ok else 'ACCEPTED rc=%d' % r.returncode}")
        if not ok: fails.append(f"d: {name}")
    print(f"bridge rows: {len(rows)} (methods {sum(1 for r in rows if r[0][0] == '!')}, exports {sum(1 for r in rows if r[0][0] != '!')})")
    shutil.rmtree(tmp, ignore_errors=True)
    print("FAILED: " + "; ".join(fails) if fails else "ALL OK")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
