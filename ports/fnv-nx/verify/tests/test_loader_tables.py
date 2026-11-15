#!/usr/bin/env python3
"""The loader's generated tables (loader/build.json): the eight symbols the loader links against come from generator stages over the pinned exe, never from a
tracked file. Pure: reads the recipe and runs the producers' text functions on toy rows; no private input."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TREE = os.path.join(HERE, "..", "..")
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(TREE, "contract"))
import iat  # noqa: E402
import mkidentity  # noqa: E402
import modules  # noqa: E402
import recipe  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


ROOT = "ports/fnv-nx/loader"
repo = os.path.join(TREE, "..", "..")
roots = recipe.load_admit(repo)
rec = recipe.load_recipe(repo, roots, ROOT)
gen = rec["generated"]
outs = {o for g in gen for o in g["outputs"]}
check("the loader recipe is valid", True)
pins = {e["name"]: e for e in json.load(open(os.path.join(TREE, "verify", "contract_inputs.json")))["external"]}
ext = {e["name"]: e for e in rec["external"]}
check("the exe, redist, manifest and wine externals of the loader are exactly the pins of contract_inputs.json (no second pin)",
      all(ext.get(n) == pins[n] for n in ("exe", "redist", "manifest", "wine")), {n: ext.get(n) for n in ("exe",)})
tools = [g["tool"] for g in gen]
check("one generator each: declareddlls, bridgegen, modules, mkidentity", sorted(tools) == sorted(["ports/fnv-nx/contract/declareddlls.py", "ports/fnv-nx/contract/bridgegen.py", "ports/fnv-nx/contract/modules.py", "ports/fnv-nx/contract/mkidentity.py"]), tools)
check("the exe pin read by every generator is the external `exe`", all("/ext/exe/FalloutNV.exe" in g["args"] for g in gen if g["tool"].endswith(("modules.py", "mkidentity.py"))), "")
check("the tables are outputs of the loader's own generators, named as units", {"gen/modules.c", "gen/import_keys.c", "gen/import_keys.h", "gen/keys.tsv", "gen/iat_data.tsv", "gen/modules.tsv", "gen/identity.c"} <= outs, outs)
check("the loader recipe exports the key header as output IMPORT_KEYS_H (a consumer root takes it through `uses`)", rec.get("outputs", {}).get("IMPORT_KEYS_H") == "gen/import_keys.h" and {"KEYS_TSV", "MODULES_TSV", "IAT_DATA_TSV"} <= set(rec["outputs"]), rec.get("outputs"))
mod = next(g for g in gen if g["tool"].endswith("modules.py"))
bg = next(g for g in gen if g["tool"].endswith("bridgegen.py"))
check("modules.py reads the bridge table that stage A (bridgegen) writes in this recipe, never the manifest external's pinned bridge.tsv",
      mod["args"][mod["args"].index("--bridge") + 1] == "/stage/gen/bridge.tsv" and not any("bridge.tsv" in a and a.startswith("/ext/") for g in gen for a in g["args"]), mod["args"])
check("stage A takes the COM rows through `uses` of bridgefacts and the name table and va record from the tool root, and exports its table", rec.get("uses") == [{"root": "ports/fnv-nx/bridgefacts", "output": "COM_ROWS"}, {"root": "ports/fnv-nx/bridgefacts", "output": "D9_ROWS"}] and "/uses/ports/fnv-nx/bridgefacts/D9_ROWS" in bg["args"] and not any(a.startswith("/ext/manifest/manifest/d9") for a in bg["args"])
      and "/uses/ports/fnv-nx/bridgefacts/COM_ROWS" in bg["args"] and rec["outputs"].get("BRIDGE_TSV") == "gen/bridge.tsv", (bg["args"], rec.get("uses")))
files = subprocess.run(["git", "-C", repo, "ls-files", "ports/fnv-nx/loader"], capture_output=True, text=True).stdout.split()
SYMS = ["fnvnx_modules", "fnvnx_module_count", "fnvnx_import_rows", "fnvnx_import_row_count", "fnvnx_import_key_count", "fnvnx_import_by_key_idx", "fnvnx_exe_sha256", "fnvnx_exe_size"]
import re  # noqa: E402
DEF = re.compile(r"^(?:const\s+)?[A-Za-z_][\w\s\*]*?\b(%s)\b\s*(?:\[[^\]]*\])?\s*=" % "|".join(SYMS), re.M)
defs = [(f, m.group(1)) for f in files if f.endswith((".c", ".cc", ".s", ".S")) for m in DEF.finditer(open(os.path.join(repo, f), errors="replace").read())]
check("no tracked file of the loader defines one of the eight symbols (a checked-in copy is a second declaration)", not defs, defs)
rows = [{"dll": "kernel32.dll", "name": "ExitProcess", "ordinal": "", "conv": "stdcall", "ret": "v", "args": "i", "native_symbol": "f", "bind": "bind"}]
keys, _, _ = iat.c_source(rows, "0" * 64, {("kernel32.dll", "ExitProcess")})
tables = modules.c_source([]) + keys + mkidentity.render(b"x")
need = SYMS
check("the producers' text defines the symbols: modules.py the module table, iat.c_source the import table, mkidentity the identity", all(s in tables for s in need), [s for s in need if s not in tables])
print("FAILED %d" % fails if fails else "all pass")
sys.exit(1 if fails else 0)
