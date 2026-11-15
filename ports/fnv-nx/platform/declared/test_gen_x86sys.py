#!/usr/bin/env python3 -I
"""test_gen_x86sys: x86sys.json is exactly what gen_x86sys.py gives from os.json and the pinned sources (a pure function), and the rows carry
their sources. Usage: test_gen_x86sys.py MIRROR_DIR WINE_DIR CPUID_MODEL_JSON CPUREF_JSON SDM_PDF"""
import json
import os
import subprocess
import sys
import tempfile

H = os.path.dirname(os.path.abspath(__file__))
mirror, wine, cpum, cpuref, sdm = sys.argv[1:6]
out = os.path.join(tempfile.mkdtemp(), "x86sys.json")
subprocess.run([sys.executable, "-I", os.path.join(H, "gen_x86sys.py"), "--os", os.path.join(H, "os.json"), "--sources", os.path.join(H, "x86sys_sources.json"),
                "--mirror", mirror, "--wine", wine, "--cpumodel", cpum, "--cpuref", cpuref, "--sdm", sdm, "--out", out], check=True)
ok = open(out).read() == open(os.path.join(H, "x86sys.json")).read()
rec = json.load(open(out))
fp = rec["thread_fp_initial"]
checks = [("x86sys.json is the generator's output", ok), ("precedence is in the header", bool(rec.get("precedence"))),
          ("initial MXCSR 0x1F80 / x87 CW 0x27F", (fp["mxcsr"]["value"], fp["x87_control_word"]["value"]) == (0x1f80, 0x27f)),
          ("0x29 is not a ring-3 vector", 0x29 not in rec["int_ring3"]["vectors"]), ("no opcode list in tss_io_bitmap", "opcodes" not in rec["tss_io_bitmap"]),
          ("cr0.mp is undecided with a why; cr0.em and cr0.ts are 0, cr4.osxmmexcpt is 1, each with a source", rec["cr0"]["mp"].get("status") == "undecided" and bool(rec["cr0"]["mp"].get("why")) and (rec["cr0"]["em"].get("value"), rec["cr0"]["ts"].get("value"), rec["cr4_sse"]["osxmmexcpt"].get("value")) == (0, 0, 1) and all(v.get("source") for v in (rec["cr0"]["em"], rec["cr0"]["ts"], rec["cr4_sse"]["osxmmexcpt"]))),
          ("no mxcsr_mask row in x86sys.json", "mxcsr_mask" not in json.dumps({k: v for k, v in rec.items() if k != "processor_features"}) and "mxcsr_mask" not in "".join(r.get("source", "") for r in rec["processor_features"]["records"].values())),
          ("cr4.osfxsr is derived from processor_features[6]", rec["cr4_sse"]["osfxsr"]["value"] == rec["processor_features"]["records"]["6"]["value"] and "processor_features[6]" in rec["cr4_sse"]["osfxsr"]["source"]),
          ("every PF row has a value with its source, or is undecided with a why", all(("value" in r and r["source"]) != (r.get("status") == "undecided" and bool(r.get("why"))) for r in rec["processor_features"]["records"].values())),
          ("SSE/SSE2/MMX/CX8/TSC rows come from the CPUID record and are 1", [rec["processor_features"]["records"][str(i)].get("value") for i in (2, 3, 6, 8, 10)] == [1] * 5),
          ("SSE3/CX16/XSAVE rows are 0 under the declared ECX mask", [rec["processor_features"]["records"][str(i)].get("value") for i in (13, 14, 17)] == [0] * 3)]
und = [k for k, r in rec["processor_features"]["records"].items() if r.get("status") == "undecided"]
g = subprocess.run([sys.executable, "-I", os.path.join(H, "gen_pfh.py"), "--x86sys", out, "--header", out + ".h"], capture_output=True, text=True)
checks.append(("gen_pfh fails exactly while a PF row is undecided and writes no header then", (g.returncode != 0) == bool(und) and os.path.exists(out + ".h") == (not und)))
R = rec["processor_features"]["records"]
checks += [("rows Wine 9.0 never sets (0,1,4,5,15,16) are 0 and cite the zero-initialised section", all(R[str(i)].get("value") == 0 and "zero-filled" in R[str(i)]["source"] for i in (0, 1, 4, 5, 15, 16))),
           ("3DNOW is 0 because only the other vendor's branch sets it", R["7"].get("value") == 0 and "another vendor" in R["7"]["source"]),
           ("PAE and NX come from CPUID bits under the masks", (R["9"].get("value"), R["12"].get("value")) == (1, 1)),
           ("DAZ is undecided while cpuid_model.json lacks mxcsr_mask", R["11"].get("status") == "undecided" and "mxcsr_mask" in R["11"]["why"] and "mxcsr_mask" not in json.load(open(cpum))),
           ("every decided PF row cites a Wine 9.0 file and line", all(("line" in r["source"]) for r in R.values() if "value" in r))]
D = rec["descriptors"]
checks += [("cs/ds/es/ss: base 0, limit 4 GiB, G=1, D/B=1, DPL=3 decided with sources", all((D[n]["base"]["value"], D[n]["limit"]["value"], D[n]["g"]["value"], D[n]["d_b"]["value"], D[n]["dpl"]["value"]) == (0, 0xFFFFFFFF, 1, 1, 3) and all("source" in D[n][f] for f in ("base", "limit", "g", "d_b", "dpl")) for n in ("cs", "ds", "es", "ss"))),
           ("type and accessed are undecided with a blocker; fs is wholly undecided", all(D[n][f].get("status") == "undecided" and D[n][f].get("why") for n in ("cs", "ds", "es", "ss") for f in ("type", "accessed")) and all(v.get("status") == "undecided" for v in D["fs"].values())),
           ("lar/lsl/verr/verw are undecided and name the missing field", all(v.get("status") == "undecided" and v.get("why") for n in ("cs", "ds", "es", "ss", "fs") for v in rec["lar_lsl"][n].values())),
           ("the SDM is pinned with its order number", rec["sources"]["sdm"]["order_number"] == "253668-093US" and len(rec["sources"]["sdm"]["sha256"]) == 64)]
checks.append(("cpuid_faulting is 0 with a source naming the sources searched", rec["cpuid_faulting"]["value"] == 0 and "is_privileged_instr" in rec["cpuid_faulting"]["source"] and "__cpuid" in rec["cpuid_faulting"]["source"]))
for n, c in checks:
    print(("PASS " if c else "FAIL ") + n)
sys.exit(0 if all(c for _, c in checks) else 1)
