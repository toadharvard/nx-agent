#!/usr/bin/env python3 -I
"""test_gen_x86sys: x86sys.json is exactly what gen_x86sys.py gives from os.json and the pinned sources (a pure function), and the rows carry
their sources. Usage: test_gen_x86sys.py MIRROR_DIR WINE_DIR CPUID_MODEL_JSON KERNEL_DIR"""
import json
import os
import subprocess
import sys
import tempfile

H = os.path.dirname(os.path.abspath(__file__))
mirror, wine, cpum, kernel = sys.argv[1:5]
out = os.path.join(tempfile.mkdtemp(), "x86sys.json")
subprocess.run([sys.executable, "-I", os.path.join(H, "gen_x86sys.py"), "--os", os.path.join(H, "os.json"), "--sources", os.path.join(H, "x86sys_sources.json"),
                "--mirror", mirror, "--wine", wine, "--cpumodel", cpum, "--kernel", kernel, "--out", out], check=True)
ok = open(out).read() == open(os.path.join(H, "x86sys.json")).read()
rec = json.load(open(out))
fp = rec["thread_fp_initial"]
checks = [("x86sys.json is the generator's output", ok), ("precedence is in the header", bool(rec.get("precedence"))),
          ("initial MXCSR 0x1F80 / x87 CW 0x27F", (fp["mxcsr"]["value"], fp["x87_control_word"]["value"]) == (0x1f80, 0x27f)),
          ("other_vectors is decided: #GP vector*8+2, access violation {0, 0xffffffff}, flags 0, eip_advance 0, Wine line numbers cited",
           (lambda o: o.get("status") is None and (o["fault"], o["error_code"], o["exception_code"], o["flags"], o["parameters"], o["eip_advance"]) == ("#GP", "vector*8+2", 0xC0000005, 0, [0, 0xffffffff], 0)
            and "segv_handler TRAP_x86_PROTFLT lines" in o["source"] and "c8373ee49fb6" in o["source"])(rec["int_ring3"]["other_vectors"])),
          ("null_seg_access is decided: #GP(0), applies to es/ds/fs/gs, access violation {0, 0xffffffff}, priv opcode set from Wine, lines cited",
           (lambda o: o.get("status") is None and (o["fault"], o["error_code"], o["exception_code"], o["flags"], o["parameters"], o["eip_advance"], o["applies_to"]) == ("#GP", 0, 0xC0000005, 0, [0, 0xffffffff], 0, ["ds", "es", "fs", "gs"])
            and o["privileged_check_first"]["result"] == "EXCEPTION_PRIV_INSTRUCTION" and "0x6c" in o["privileged_check_first"]["priv_instruction_opcodes"] and "0xcd" in o["privileged_check_first"]["priv_instruction_opcodes"] and o["privileged_check_first"]["prefix_limit"] == 15
            and "Intel SDM Vol 3 3.4.2" in o["source"] and "c8373ee49fb6" in o["source"])(rec["null_seg_access"])),
          ("selectors.gs = 0, derived from both pinned kernels with their addresses (KeContextToKframes `and [tf+SegGs], 0`, KiInitializeContextThread call, pop gs / iretd, PspCreateUserContext stores)",
           (lambda g: g.get("status") is None and g["value"] == 0 and sorted(g["kernels"]) == ["ntkrnlpa.exe", "ntoskrnl.exe"]
            and all(k["value"] == 0 and k["KeContextToKframes"]["and_SegGs_0"].startswith("0x") and k["KiThreadStartup"]["pop_gs"].startswith("0x") and k["PspCreateUserContext"]["stores"]["gs"][1] == 0
                    and k["SegGs_offset"] == 0x30 and k["SegCs_offset"] == 0x6c for k in g["kernels"].values())
            and g["tool"]["capstone"] == "5.0.7")(rec["selectors"]["gs"])),
          ("the other selectors equal PspCreateUserContext's constants in both kernels",
           all({r: v[1] for r, v in k["PspCreateUserContext"]["stores"].items() if r != "gs"} == {r: rec["selectors"][r]["value"] for r in ("cs", "ds", "es", "ss", "fs")} for k in rec["selectors"]["gs"]["kernels"].values())),
          ("0x29 is decided from the 7601 IDT: not excluded, entry not present with gate type 0, result #GP(0x14a)",
           "41" not in rec["int_ring3"]["excluded"] and (lambda v: (v["present"], v["type"], v["dpl"], v["handler"], v["access"]) == (0, 0, 0, None, 0) and "0x14a" in v["result"])(rec["idt"]["vector_0x29"])
           and "0x29" not in rec["idt"]["ring3_gates"] and "other_vectors" in rec["idt"]["vector_0x29"]["result"]),
          ("every excluded vector is a present DPL-3 gate with no vector row", all(int(k) not in rec["int_ring3"]["vectors"] for k in rec["int_ring3"]["excluded"])),
          ("idt: limit 0x7ff, 256 entries, 0x2a-0x2e are ring-3 gates, both kernels' addresses", (rec["idt"]["limit"], rec["idt"]["entries"]) == (0x7ff, 256) and "0x2a-0x2e" in rec["idt"]["ring3_gates"] and sorted(rec["idt"]["addresses"]) == ["ntkrnlpa.exe", "ntoskrnl.exe"]),
          ("gdt: limit 0x3ff; ring-3 code 0x18 type 0xA DPL3, data 0x20 and TEB 0x38 type 2 DPL3, all present, S=1",
           rec["gdt"]["limit"] == 0x3ff and all((d["p"], d["s"], d["dpl"], d["type"]) == (1, 1, 3, t) for d, t in ((rec["gdt"]["descriptors"]["0x18"], 0xa), (rec["gdt"]["descriptors"]["0x20"], 2), (rec["gdt"]["descriptors"]["0x38"], 2)))),
          ("mov_to_sreg: ds/es/fs/gs load 0x18-0x1b (readable code), 0x20-0x23, 0x38-0x3b, 0x40-0x43 and null; ss only 0x23, 0x3b, 0x43; nothing undecided",
           all(rec["mov_to_sreg"][r]["selectors"] == sorted(list(range(0x18, 0x1c)) + list(range(0x20, 0x24)) + list(range(0x38, 0x3c)) + list(range(0x40, 0x44))) and rec["mov_to_sreg"][r]["null"] for r in ("es", "ds", "fs", "gs"))
           and rec["mov_to_sreg"]["ss"]["selectors"] == [0x23, 0x3b, 0x43] and not rec["mov_to_sreg"]["ss"]["null"] and rec["mov_to_sreg"]["undecided"]["selectors"] == []),
          ("jmp_far: 0x18-0x1b jump; null #GP(0); TI=1 and beyond-limit and every other known descriptor #GP(selector)",
           (lambda j: j["jumps"]["selectors"] == [0x18, 0x19, 0x1a, 0x1b] and j["null_selector"]["error_code"] == 0 and j["ti1"]["fault"] == "#GP" and j["beyond_gdt_limit"]["fault"] == "#GP"
            and j["gp_selector_descriptors"]["fault"] == "#GP" and "0x08" in j["gp_selector_descriptors"]["descriptors"] and "0x18" not in j["gp_selector_descriptors"]["descriptors"])(rec["jmp_far"])),
          ("ldt: a new process has the null LDTR (sldt 0)", rec["sldt"]["value"] == 0 and "rule" in rec["ldt"] and rec["idtr_limit"]["value"] == 0x7ff and rec["sgdt"]["limit"]["value"] == 0x3ff and rec["sgdt"]["base"]["status"] == "undecided"),
          ("0x29 is not a ring-3 vector", 0x29 not in rec["int_ring3"]["vectors"]), ("no opcode list in tss_io_bitmap", "opcodes" not in rec["tss_io_bitmap"]),
          ("every CR0/CR4 SSE row is undecided with a why", all(v.get("status") == "undecided" and v.get("why") for k in ("cr0", "cr4_sse") for v in rec[k].values()))]
for n, c in checks:
    print(("PASS " if c else "FAIL ") + n)
sys.exit(0 if all(c for _, c in checks) else 1)
