#!/usr/bin/env python3 -I
"""test_gen_x86sys: x86sys.json is exactly what gen_x86sys.py gives from os.json and the pinned sources (a pure function), and the rows carry
their sources. Usage: test_gen_x86sys.py MIRROR_DIR WINE_DIR CPUID_MODEL_JSON"""
import json
import os
import subprocess
import sys
import tempfile

H = os.path.dirname(os.path.abspath(__file__))
mirror, wine, cpum = sys.argv[1:4]
out = os.path.join(tempfile.mkdtemp(), "x86sys.json")
subprocess.run([sys.executable, "-I", os.path.join(H, "gen_x86sys.py"), "--os", os.path.join(H, "os.json"), "--sources", os.path.join(H, "x86sys_sources.json"),
                "--mirror", mirror, "--wine", wine, "--cpumodel", cpum, "--out", out], check=True)
ok = open(out).read() == open(os.path.join(H, "x86sys.json")).read()
rec = json.load(open(out))
fp = rec["thread_fp_initial"]
checks = [("x86sys.json is the generator's output", ok), ("precedence is in the header", bool(rec.get("precedence"))),
          ("initial MXCSR 0x1F80 / x87 CW 0x27F", (fp["mxcsr"]["value"], fp["x87_control_word"]["value"]) == (0x1f80, 0x27f)),
          ("other_vectors is decided: #GP vector*8+2, access violation {0, 0xffffffff}, flags 0, eip_advance 0, Wine line numbers cited",
           (lambda o: o.get("status") is None and (o["fault"], o["error_code"], o["exception_code"], o["flags"], o["parameters"], o["eip_advance"]) == ("#GP", "vector*8+2", 0xC0000005, 0, [0, 0xffffffff], 0)
            and "segv_handler TRAP_x86_PROTFLT lines" in o["source"] and "c8373ee49fb6" in o["source"])(rec["int_ring3"]["other_vectors"])),
          ("0x29 stays excluded and undecided", "41" in rec["int_ring3"]["excluded"] and rec["int_ring3"]["excluded"]["41"]["status"] == "undecided"),
          ("0x29 is not a ring-3 vector", 0x29 not in rec["int_ring3"]["vectors"]), ("no opcode list in tss_io_bitmap", "opcodes" not in rec["tss_io_bitmap"]),
          ("every CR0/CR4 SSE row is undecided with a why", all(v.get("status") == "undecided" and v.get("why") for k in ("cr0", "cr4_sse") for v in rec[k].values()))]
for n, c in checks:
    print(("PASS " if c else "FAIL ") + n)
sys.exit(0 if all(c for _, c in checks) else 1)
