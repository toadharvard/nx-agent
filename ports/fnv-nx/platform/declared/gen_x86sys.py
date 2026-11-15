#!/usr/bin/env python3
"""gen_x86sys - the x86 system-state record x86sys.json, from the declared-OS record and the pinned sources alone.

    gen_x86sys.py --os os.json --sources x86sys_sources.json --mirror DIR --out x86sys.json

DIR is a checkout of the mirror named in the sources record (fnv-nx-build tools/wdk71-headers). The sha256 of every file read is checked against the
record. Defined only for a native 32-bit x86 machine record (native_arch 0, process_wow64 0): any other record is refused, so a WOW64 record needs
sources of its own. The output is a pure function of its inputs; every row carries its source; a state with no pinned source is written as
{"status": "undecided", "why": ...} and is never given a value.

Rows:
  selectors      cs, ds, es, ss, fs, gs of a user thread: KGDT_R3_* of ks386.inc | RPL_MASK (the user privilege level), the assignment of
                 registers to KGDT constants and gs = 0 from RtlInitializeContext (thread.c); tr = KGDT_TSS (ks386.inc).
  mov_to_sreg    the selectors a `mov sreg, r` can load at CPL 3: null for es/ds/fs/gs; the KGDT_R3_DATA and KGDT_R3_TEB selectors (ring-3 data
                 segments by the WDK's naming) for every register, ss included; the KGDT_R3_CODE selector for no register until the descriptor's
                 readability is pinned (undecided).
  int_ring3      the IDT vectors whose gate is DPL 3 in the pinned table (trap.s, expanded by its REPEAT blocks), those the OS build has (gate_since);
                 every other vector is #GP for `int n`.
  idtr_limit     8 * (number of gates of the table) - 1.
  sldt, sgdt, sidt base: undecided where the pinned sources do not state them."""
import argparse
import hashlib
import json
import os
import re
import sys


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def ks386_equ(text):
    return {m.group(1): int(m.group(2), 16) for m in re.finditer(r"^(\w+)\s+equ\s+([0-9A-Fa-f]+)H\s*$", text, re.M)}


def idt_gates(text):
    """The gates of _KiIdt in order: [(handler, dpl)], REPEAT blocks expanded, GENERATE_IDT_STUB lines the DPL 0 stubs of the macro."""
    body = text.split("_KiIdt:", 1)[1]
    stub = re.search(r"MACRO\(GENERATE_IDT_STUB.*?\nidt (\w+)&Vector, INT_32_DPL(\d)", text, re.S)
    gates = []
    stack = [[]]
    counts = []
    for ln in body.splitlines():
        s = ln.split("/*")[0].strip()
        m = re.fullmatch(r"idt\s+(\w+),\s*INT_32_DPL(\d)", s)
        if m:
            stack[-1].append((m.group(1), int(m.group(2))))
        elif re.fullmatch(r"GENERATE_IDT_STUB\s+%i", s):
            stack[-1].append((stub.group(1) + "<vector>", int(stub.group(2))))
        elif (m := re.fullmatch(r"REPEAT\s+(\d+)", s)):
            counts.append(int(m.group(1)))
            stack.append([])
        elif s == "ENDR":
            blk = stack.pop()
            stack[-1].extend(blk * counts.pop())
        elif s.startswith((".endcode", "END")) and not stack[1:]:
            break
    return stack[0]


def build(osrec, src, mirror):
    fl = osrec["fields"]
    if fl["native_arch"]["value"] != 0 or fl["process_wow64"]["value"] != 0:
        raise SystemExit("gen_x86sys: the sources describe a native 32-bit x86 machine; this record is not one")
    build_no = fl["build"]["value"]
    txt = {}
    for k, f in sorted(src["files"].items()):
        p = os.path.join(mirror, f["path"])
        if sha(p) != f["sha256"]:
            raise SystemExit("gen_x86sys: %s: sha256 is not the pinned one" % f["path"])
        txt[k] = open(p, errors="replace").read()
    kg = ks386_equ(txt["ks386"])
    need = ["KGDT_R3_CODE", "KGDT_R3_DATA", "KGDT_R3_TEB", "KGDT_TSS", "RPL_MASK"]
    if any(n not in kg for n in need):
        raise SystemExit("gen_x86sys: ks386.inc lacks " + ", ".join(n for n in need if n not in kg))
    rpl = kg["RPL_MASK"]
    thr = txt["thread"]
    reg = {}
    for r, sg in (("gs", "SegGs"), ("fs", "SegFs"), ("es", "SegEs"), ("ds", "SegDs"), ("ss", "SegSs"), ("cs", "SegCs")):
        m = re.search(r"ThreadContext->%s\s*=\s*(\w+);" % sg, thr)
        if not m:
            raise SystemExit("gen_x86sys: thread.c does not set %s" % sg)
        reg[r] = m.group(1)
    src_ks = "WDK 7.1.0 ks386.inc (%s)" % src["files"]["ks386"]["path"]
    src_th = "ReactOS RtlInitializeContext, " + src["files"]["thread"]["origin"].split(" ", 2)[1]
    sel = {}
    for r in ("cs", "ds", "es", "ss", "fs", "gs"):
        v = reg[r]
        if v == "0":
            sel[r] = {"value": 0, "source": "%s: SegGs = 0" % src_th}
        else:
            sel[r] = {"value": kg[v] | rpl, "source": "%s: %s = %s; %s: %s = 0x%02X, RPL_MASK = %d" % (src_th, r, v, src_ks, v, kg[v], rpl)}
    sel["tr"] = {"value": kg["KGDT_TSS"], "source": "%s: KGDT_TSS (the task register selector of the OS, RPL 0)" % src_ks}
    data = sorted({sel["ds"]["value"], sel["fs"]["value"]})
    code = sel["cs"]["value"]
    why_ds = "KGDT_R3_DATA and KGDT_R3_TEB are the ring-3 data segments of the WDK's GDT constants (ks386.inc); SDM Vol 3 MOV Sreg: a data segment of DPL 3 is loadable at CPL 3 with RPL 3"
    mov = {"es": {"null": True, "selectors": data, "source": why_ds}, "ds": {"null": True, "selectors": data, "source": why_ds},
           "fs": {"null": True, "selectors": data, "source": why_ds}, "gs": {"null": True, "selectors": data, "source": why_ds},
           "ss": {"null": False, "selectors": data, "source": why_ds + "; ss needs a writable data segment, not the null selector"},
           "undecided": {"selectors": [code], "why": "the KGDT_R3_CODE descriptor's readable bit is not stated in a pinned source, so whether es/ds/fs/gs may load it is undecided"}}
    gates = idt_gates(txt["trap"])
    if len(gates) != 256:
        raise SystemExit("gen_x86sys: trap.s expands to %d gates, not 256" % len(gates))
    since = src["gate_since"]
    vec = [v for v, (h, d) in enumerate(gates) if d == 3 and since.get(h, {}).get("build", 0) <= build_no]
    skipped = {"0x%02x" % v: since[h]["source"] for v, (h, d) in enumerate(gates) if d == 3 and since.get(h, {}).get("build", 0) > build_no}
    undec = lambda why: {"status": "undecided", "why": why}
    return {
        "format": "fnvnx-x86sys/1",
        "os": {"sha256": None, "build": build_no},
        "sources": {k: {"path": f["path"], "sha256": f["sha256"], "origin": f["origin"]} for k, f in sorted(src["files"].items())},
        "mirror": src["mirror"],
        "selectors": sel,
        "mov_to_sreg": mov,
        "int_ring3": {"vectors": vec, "absent_for_this_build": skipped,
                      "source": "%s: the gates with INT_32_DPL3 (a user-mode `int n` of any other vector is #GP)" % src["files"]["trap"]["origin"]},
        "idtr_limit": {"value": 8 * len(gates) - 1, "source": "%d gates of _KiIdt in trap.s, 8 bytes each" % len(gates)},
        "sldt": undec("no pinned source states the LDTR of a process that has no LDT (ReactOS ldt.c loads KGDT_LDT only when a process creates one)"),
        "sgdt": undec("the GDT base and limit of the OS are kernel values with no documented fixed value and no pinned source"),
        "sidt_base": undec("the IDT base is a kernel address with no documented fixed value and no pinned source"),
    }


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--os", required=True)
    ap.add_argument("--sources", required=True)
    ap.add_argument("--mirror", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    raw = open(a.os, "rb").read()
    rec = build(json.loads(raw), json.load(open(a.sources)), a.mirror)
    rec["os"]["sha256"] = hashlib.sha256(raw).hexdigest()
    with open(a.out, "w") as f:
        json.dump(rec, f, indent=1, sort_keys=True)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
