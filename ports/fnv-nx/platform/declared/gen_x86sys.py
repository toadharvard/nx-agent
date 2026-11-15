#!/usr/bin/env python3
"""gen_x86sys - the x86 system-state record x86sys.json, from the declared-OS record and the pinned sources alone.

    gen_x86sys.py --os os.json --sources x86sys_sources.json --mirror DIR --wine DIR --cpumodel FILE --out x86sys.json

DIR (--mirror) is a checkout of fnv-nx-build tools/wdk71-headers, --wine one of tools/wine-9.0, FILE the cpuid_model.json of the sources record. The sha256 of every
file read is checked against the record. Defined only for a native 32-bit x86 machine record (native_arch 0, process_wow64 0). The output is a pure function
of its inputs; every row cites its source; a state with no pinned source is written {"status": "undecided", "why": ...} and never given a value.
Trust chain / precedence (header of the record): (1) documented Win32 for build 7601 (MSDN, WDK 7.1) and the Intel SDM, (2) Wine 9.0 only where it contradicts neither (1) nor the declared OS, (3) nothing else.
A row with both a Wine and a documented source takes the documented one (sources `doc_gates`: a documented since-build above the declared build excludes the Wine value).

Rows:
  selectors      cs ds es ss fs: KGDT_R3_CODE / KGDT_R3_DATA / KGDT_R3_TEB of ks386.inc | RPL_MASK, by the role the WDK names (code segment -> cs, data segment
                 -> ds es ss, the TEB's segment -> fs); gs: undecided (no WDK role); tr = KGDT_TSS.
  mov_to_sreg    the selectors a `mov sreg, r` can load at CPL 3: null for es/ds/fs/gs; the ring-3 data selectors for every register; the code selector: undecided.
  int_ring3      the vectors Wine 9.0 handles, minus those a documented gate excludes at build 7601 (0x29: MSDN __fastfail, Windows 8+; undecided in `excluded`) for a user-mode `int n` (handle_interrupt cases) and the traps it maps to #BP / #OF (vectors 3, 4); every other
                 vector: undecided (no pinned source says whether Windows 7 has a ring-3 gate there).
  cr4            tsd 0 (Wine: rdtsc is not intercepted); umip 0 (SDM CR4.UMIP is reserved unless CPUID.07H:ECX[2], and the declared CPU's max basic leaf is below 7);
                 pce undecided.
  eflags         the initial user EFLAGS of Wine 9.0 and its IOPL; IOPL is not user-modifiable (WDK EFLAGS_USER_SANITIZE).
  idtr_limit, sldt, sgdt, sidt base: undecided."""
import argparse
import hashlib
import json
import os
import re
import sys


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def pinned(path, rec, what):
    if sha(path) != rec["sha256"]:
        raise SystemExit("gen_x86sys: %s: sha256 is not the pinned one" % what)
    return open(path, errors="replace").read()


def ks386_equ(text):
    return {m.group(1): int(m.group(2), 16) for m in re.finditer(r"^(\w+)\s+equ\s+([0-9A-Fa-f]+)H\s*$", text, re.M)}


def build(osrec, src, mirror, wine, cpumodel, cpuref):
    undec = lambda why: {"status": "undecided", "why": why}
    fl = osrec["fields"]
    if fl["native_arch"]["value"] != 0 or fl["process_wow64"]["value"] != 0:
        raise SystemExit("gen_x86sys: the sources describe a native 32-bit x86 machine; this record is not one")
    kf = src["files"]["ks386"]
    kg = ks386_equ(pinned(os.path.join(mirror, kf["path"]), kf, kf["path"]))
    need = ["TssIoMapBase", "PrIopmOffset", "KGDT_R3_CODE", "KGDT_R3_DATA", "KGDT_R3_TEB", "KGDT_TSS", "RPL_MASK", "EFLAGS_USER_SANITIZE", "CR4_FXSR"]
    if any(n not in kg for n in need):
        raise SystemExit("gen_x86sys: ks386.inc lacks " + ", ".join(n for n in need if n not in kg))
    rpl = kg["RPL_MASK"]
    ks = "WDK 7.1.0 ks386.inc (%s)" % kf["path"]
    role = {"cs": ("KGDT_R3_CODE", "the ring-3 code segment"), "ds": ("KGDT_R3_DATA", "the ring-3 data segment"), "es": ("KGDT_R3_DATA", "the ring-3 data segment"),
            "ss": ("KGDT_R3_DATA", "the ring-3 data segment"), "fs": ("KGDT_R3_TEB", "the TEB's segment")}
    sel = {r: {"value": kg[k] | rpl, "source": "%s: %s = 0x%02X (%s) | RPL_MASK %d" % (ks, k, kg[k], why, rpl)} for r, (k, why) in sorted(role.items())}
    sel["gs"] = undec("no pinned source gives the initial gs of a Windows 7 user thread (Wine loads the host's gs, the WDK has no KGDT constant for it)")
    sel["tr"] = {"value": kg["KGDT_TSS"], "source": "%s: KGDT_TSS (the task state segment selector)" % ks}
    data = sorted({sel["ds"]["value"], sel["fs"]["value"]})
    why_ds = "KGDT_R3_DATA and KGDT_R3_TEB are the ring-3 data segments of the WDK's GDT constants (ks386.inc); SDM Vol 3 MOV Sreg: a data segment of DPL 3 is loadable at CPL 3 with RPL 3"
    mov = {r: {"null": r != "ss", "selectors": data, "source": why_ds + ("; ss needs a writable data segment, not the null selector" if r == "ss" else "")} for r in ("es", "ds", "fs", "gs", "ss")}
    mov["undecided"] = {"selectors": [sel["cs"]["value"]], "why": "the ring-3 code descriptor's readable bit is not stated in a pinned source, so whether es/ds/fs/gs may load it is undecided"}
    wf = src["wine"]["files"]["signal_i386"]
    wt = pinned(os.path.join(wine, wf["path"]), wf, wf["path"])
    wsrc = "Wine 9.0 %s @ %s" % (wf["path"], src["wine"]["commit"][:12])
    m = re.search(r"context\.EFlags\s*=\s*(0x[0-9a-fA-F]+);", wt)
    eflags0 = int(m.group(1), 16) if m else None
    fn = re.search(r"static inline DWORD is_privileged_instr\(.*?\n}\n", wt, re.S)
    hi = re.search(r"static BOOL handle_interrupt\(.*?\n}\n", wt, re.S)
    segv = re.search(r"static void segv_handler\(.*?(?=\nstatic void trap_handler)", wt, re.S)
    trap = re.search(r"static void trap_handler\(.*?(?=\nstatic void fpe_handler)", wt, re.S)
    if not (m and fn and hi and segv and trap):
        raise SystemExit("gen_x86sys: signal_i386.c lacks the initial EFlags, is_privileged_instr, handle_interrupt or segv_handler / trap_handler")
    ext = fn.group(0).split("case 0x0f:", 1)[1].split("case 0x6c:", 1)[0].split("switch", 1)[1]
    ext_ops = sorted(int(x, 16) for x in re.findall(r"case (0x[0-9a-fA-F]+):\s*/\*", ext))
    ints = sorted(int(x, 16) for x in re.findall(r"^    case (0x[0-9a-fA-F]+):", hi.group(0), re.M))
    traps = {n for n, t in (("TRAP_x86_OFLOW", segv), ("TRAP_x86_BPTFLT", trap)) if "case %s:" % n in t.group(0)}
    gates = {x["key"]: x for x in src["doc_gates"] if x["row"] == "int_ring3.vectors"}
    build_no = fl["build"]["value"]
    gated = sorted(v for v, x in gates.items() if build_no < x["since_build"])   # a documented gate that the declared build is below: the vector is out (precedence 1 over Wine)
    ints = [i for i in ints if i not in gated]
    vec = sorted(set(ints) | ({3} if "TRAP_x86_BPTFLT" in traps else set()) | ({4} if "TRAP_x86_OFLOW" in traps else set()))
    tail = fn.group(0).split("case 0x6c:", 1)[1].split("default:", 1)[0]
    io_ops = sorted(int(o, 16) for o, c in re.findall(r"case (0x[0-9a-fA-F]+):\s*/\*\s*([^*]*?)\s*\*/", "case 0x6c:" + tail) if re.match(r"(ins|outs|in|out)[bl( ]", c))
    if io_ops != [0x6c, 0x6d, 0x6e, 0x6f, 0xe4, 0xe5, 0xe6, 0xe7, 0xec, 0xed, 0xee, 0xef] or "return EXCEPTION_PRIV_INSTRUCTION" not in tail:
        raise SystemExit("gen_x86sys: is_privileged_instr does not treat in/out/ins/outs as privileged the way the row tss_io_bitmap needs")
    fp = {}
    for k in ("winnt", "ntddk"):
        f = src["files"][k]
        lines = pinned(os.path.join(mirror, f["path"]), f, f["path"]).split("\n")
        for n, pat in (("mxcsr", r"#define INITIAL_MXCSR (0x[0-9a-fA-F]+)"), ("fpcsr", r"#define INITIAL_FPCSR (0x[0-9a-fA-F]+)")):
            hit = [(i + 1, int(re.match(pat, l).group(1), 16)) for i, l in enumerate(lines) if re.match(pat, l)]
            if len(hit) != 1:
                raise SystemExit("gen_x86sys: %s has %d definitions of INITIAL_%s" % (f["path"], len(hit), n.upper()))
            fp.setdefault(n, {})[k] = hit[0]
    wl = wt.split("\n")
    wine_cw = [(i + 1, int(re.search(r"=\s*(0x[0-9a-fA-F]+);", l).group(1), 16)) for i, l in enumerate(wl) if re.search(r"^\s*context\.FloatSave\.ControlWord\s*=", l)]
    wine_mx = [(i + 1, int(re.search(r"=\s*(0x[0-9a-fA-F]+);", l).group(1), 16)) for i, l in enumerate(wl) if re.search(r"^\s*\(\(XSAVE_FORMAT \*\)context\.ExtendedRegisters\)->MxCsr\s*=", l)]
    if len(wine_cw) != 1 or len(wine_mx) != 1:
        raise SystemExit("gen_x86sys: call_init_thunk does not set the initial control word and MXCSR once each")
    docv = {n: {v for _, v in fp[n].values()} for n in fp}
    if len(docv["mxcsr"]) != 1 or len(docv["fpcsr"]) != 1:
        raise SystemExit("gen_x86sys: the WDK headers disagree on the initial MXCSR / FPCSR")
    if {wine_mx[0][1]} != docv["mxcsr"] or {wine_cw[0][1]} != docv["fpcsr"]:   # precedence: the documentation wins; Wine is cited only where it agrees
        raise SystemExit("gen_x86sys: Wine 9.0 contradicts the WDK initial MXCSR / FPCSR")
    cites = lambda n: "WDK 7.1.0 winnt.h line %d and ntddk.h line %d (INITIAL_%s); Wine 9.0 %s line %d (call_init_thunk, initial context)" % (fp[n]["winnt"][0], fp[n]["ntddk"][0], n.upper(), wf["path"], (wine_mx if n == "mxcsr" else wine_cw)[0][0])
    cm = src["cpumodel"]
    ct = pinned(cpumodel, cm, cm["path"])
    if any(v in vec for v in gated) or not src.get("precedence"):   # precedence check: a value that a documented gate excludes never comes from Wine
        raise SystemExit("gen_x86sys: a Wine-sourced vector contradicts a documented gate, or the sources record has no precedence")
    max_basic = json.loads(ct).get("max_basic")
    if not isinstance(max_basic, int):
        raise SystemExit("gen_x86sys: cpuid_model.json has no max_basic")
    umip = 0 if max_basic < 7 else None
    cpu = json.loads(ct)
    ref = pinned(cpuref, src["cpuref"], src["cpuref"]["path"])
    vendor = json.loads(ref)["vendor_id"]
    leaf = {("1", "edx"): int(cpu["leaf1_edx_raw"], 16) & ~int(cpu["leaf1_edx_clear"], 16), ("1", "ecx"): int(cpu["leaf1_ecx_raw"], 16) & int(cpu["leaf1_ecx_keep"], 16),
            ("ext", "edx"): int(cpu["ext1_edx_raw"], 16) & int(cpu["ext1_edx_keep"], 16), ("ext", "ecx"): int(cpu["ext1_ecx_raw"], 16) & int(cpu["ext1_ecx_keep"], 16)}   # the declared masks (cpuid_model.json masks_doc)
    pfdoc = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pf.json")))
    wn = pinned(os.path.join(mirror, src["files"]["winnt"]["path"]), src["files"]["winnt"], "winnt.h").split("\n")
    pfn = {int(m.group(2)): (m.group(1), i + 1) for i, l in enumerate(wn) for m in [re.match(r"#define (PF_[A-Z0-9_]+)\s+(\d+)\s*$", l)] if m}
    if sorted(pfn) != list(range(max(pfn) + 1)) or sorted(map(int, pfdoc["meanings"])) != sorted(pfn):
        raise SystemExit("gen_x86sys: pf.json does not have exactly one meaning entry per PF_* index of winnt.h")
    wsrc_of = lambda k: (src["wine"]["files"][k], "Wine 9.0 %s @ %s" % (src["wine"]["files"][k]["path"], src["wine"]["commit"][:12]))
    def wread(k):
        f = src["wine"]["files"][k]
        return pinned(os.path.join(wine, f["path"]), f, f["path"]).split("\n")
    wb, sy, mp = wread("wineboot"), wread("sysinfo"), wread("usd_mapping")
    # (1) wineboot.c: the PROCESSOR_ARCHITECTURE_INTEL case: features[PF_X] = !!(sci.ProcessorFeatureBits & CPU_FEATURE_Y);  or  = TRUE;
    i0 = next(i for i, l in enumerate(wb) if l.strip() == "case PROCESSOR_ARCHITECTURE_INTEL:")
    i1 = next(i for i in range(i0, len(wb)) if wb[i].strip() == "break;")
    setby = {}
    for i in range(i0, i1):
        m = re.match(r"\s*features\[(PF_\w+)\]\s*=\s*(?:!!\(sci\.ProcessorFeatureBits & CPU_FEATURE_(\w+)\)|(TRUE));", wb[i])
        if m:
            setby[m.group(1)] = (m.group(2) or "TRUE", i + 1)
    # (2) system.c get_cpuinfo: the CPUID bits of each CPU_FEATURE_Y in the declared vendor's branch (leaf 1 first, then 0x80000001 after its do_cpuid)
    g0 = next(i for i, l in enumerate(sy) if l.startswith("static void get_cpuinfo("))
    g1 = next(i for i in range(g0, len(sy)) if sy[i] == "}")
    vend = {"GenuineIntel": "GENU && regs[3] == INEI && regs[2] == NTEL", "AuthenticAMD": "AUTH && regs[3] == ENTI && regs[2] == CAMD"}[vendor]
    vstart = next(i for i in range(g0, g1) if vend in sy[i])
    vend_end = next(i for i in range(vstart + 1, g1) if re.match(r"\s*(else if|else)\b", sy[i]))
    bits = {}   # CPU_FEATURE_Y -> [(leaf, reg, bit, line)]
    def scan(lo, hi, lf):
        for i in range(lo, hi):
            if re.search(r"do_cpuid\(\s*0x80000001", sy[i]):
                lf = "ext"
            m = re.match(r"\s*if\s*\(\s*regs2\[(\d)\]\s*&\s*\(1u?\s*<<\s*(\d+)\s*\)\s*\)\s*features \|= CPU_FEATURE_(\w+);", sy[i])
            if m:
                bits.setdefault(m.group(3), []).append((lf, {2: "ecx", 3: "edx"}[int(m.group(1))], int(m.group(2)), i + 1))
    l1 = next(i for i in range(g0, g1) if re.search(r"do_cpuid\(\s*0x00000001", sy[i]))
    scan(l1, next(i for i in range(l1, g1) if "have_sse_daz_mode()" in sy[i]), "1")
    scan(vstart, vend_end, "1")
    daz_line = next(i for i in range(g0, g1) if "have_sse_daz_mode()" in sy[i]) + 1
    z0 = next(i for i, l in enumerate(mp) if "user_shared_data = ptr;" in l)
    zline = "%s line %d (the section is created SEC_COMMIT, zero-filled, line %d; the server writes only SystemCall = 1 at line %d); no assignment of the index in wineboot.c PROCESSOR_ARCHITECTURE_INTEL case lines %d-%d" % (src["wine"]["files"]["usd_mapping"]["path"], z0 + 1, z0 - 4, z0 + 2, i0 + 1, i1)
    pf = {}
    for idx in sorted(pfn):
        nm, ln = pfn[idx]
        row = {"name": nm, "winnt_h_line": ln}
        mean = pfdoc["meanings"][str(idx)]
        if mean:
            row.update({"meaning": mean, "meaning_source": pfdoc["meaning_source"]})
        if nm not in setby:
            row.update({"value": 0, "source": "Wine 9.0 never sets it on x86: %s" % zline})
        else:
            how, wln = setby[nm]
            wbs = "Wine 9.0 %s line %d" % (src["wine"]["files"]["wineboot"]["path"], wln)
            if how == "TRUE":
                row.update({"value": 1, "source": "%s sets it unconditionally" % wbs})
            elif how == "DAZ":
                mm = cpu.get("mxcsr_mask")
                if mm is None:
                    row.update(undec("Wine 9.0 computes it as SSE2 && FXSAVE && MXCSR_MASK bit 6 (%s line %d, have_sse_daz_mode, fxsave); verify/cpuid_model.json lacks mxcsr_mask; blocker: the owner of cpuid_model.json (L3a) must add the reference CPU's MXCSR_MASK, which the lifted FXSAVE needs too" % (src["wine"]["files"]["sysinfo"]["path"], daz_line)))
                else:
                    sse2 = (leaf[("1", "edx")] >> 26) & 1
                    fxsr = (leaf[("1", "edx")] >> 24) & 1
                    row.update({"value": int(bool(sse2 and fxsr and (int(mm, 16) >> 6) & 1)), "source": "%s: CPU_FEATURE_DAZ = SSE2 && FXSR && MXCSR_MASK bit 6 (system.c line %d), MXCSR_MASK of cpuid_model.json" % (wbs, daz_line)})
            else:
                hits = bits.get(how, [])
                if not hits:
                    other = [i + 1 for i in range(g0, g1) if ("CPU_FEATURE_%s;" % how) in sy[i] and not (vstart <= i < vend_end)]
                    sp = src["wine"]["files"]["sysinfo"]["path"]
                    if other:
                        row.update({"value": 0, "source": "%s maps CPU_FEATURE_%s; %s sets it only in another vendor's branch (line %s), never in the %s branch (lines %d-%d) of the declared CPU, so the feature word lacks it" % (wbs, how, sp, ",".join(map(str, other)), vendor, vstart + 1, vend_end)})
                    else:
                        row.update(undec("%s maps it from CPU_FEATURE_%s, which %s get_cpuinfo never sets" % (wbs, how, sp)))
                else:
                    val = int(any((leaf[(lf, rg)] >> b) & 1 for lf, rg, b, _ in hits))
                    why = "; ".join("%s line %d: CPUID %s %s[%d] = %d" % (src["wine"]["files"]["sysinfo"]["path"], l_, "leaf 1" if lf == "1" else "0x80000001", rg.upper(), b, (leaf[(lf, rg)] >> b) & 1) for lf, rg, b, l_ in hits)
                    row.update({"value": val, "source": "%s maps CPU_FEATURE_%s; %s; under the declared masks of %s (vendor %s from %s)" % (wbs, how, why, cm["path"], vendor, src["cpuref"]["path"])})
        pf[str(idx)] = row
    wnl = pinned(os.path.join(mirror, src["files"]["winnt"]["path"]), src["files"]["winnt"], "winnt.h").split("\n")
    nth = lambda pat, after=0: next(i + 1 for i in range(after, len(wnl)) if re.search(pat, wnl[i]))
    cfp = nth(r"#define CONTEXT_FLOATING_POINT\s+\(CONTEXT_i386", 0)
    fsa = nth(r"typedef struct _FLOATING_SAVE_AREA", cfp)
    extr = nth(r"BYTE\s+ExtendedRegisters\[", fsa)
    mf, mt = nth(r"#define STATUS_FLOAT_MULTIPLE_FAULTS"), nth(r"#define STATUS_FLOAT_MULTIPLE_TRAPS")
    wl2 = wt.split("\n")
    wx0 = next(i + 1 for i, l in enumerate(wl2) if "case TRAP_x86_CACHEFLT:  /* SIMD exception */" in l)
    wx1 = next(i + 1 for i in range(wx0, len(wl2)) if "STATUS_FLOAT_MULTIPLE_TRAPS" in wl2[i])
    osfxsr = {"value": pf["6"]["value"], "source": "derived: CR4.OSFXSR = 1 iff the declared PF_XMMI_INSTRUCTIONS_AVAILABLE (row processor_features[6]) is TRUE, because MSDN IsProcessorFeaturePresent gives that value the meaning 'the SSE instruction set is available' to the calling process and Intel SDM Vol 3A 2.5 / 13.1 raises #UD for SSE instructions when CR4.OSFXSR = 0; ks386.inc CR4_FXSR = 0x%X names the bit" % kg["CR4_FXSR"]} if "value" in pf["6"] else undec("derived from processor_features[6], which is undecided")
    sanit = kg["EFLAGS_USER_SANITIZE"]
    return {
        "format": "fnvnx-x86sys/2",
        "precedence": src["precedence"],
        "os": {"sha256": None, "build": fl["build"]["value"]},
        "sources": {"mirror": src["mirror"], "ks386": kf, "wine": {"commit": src["wine"]["commit"], "file": wf}, "cpumodel": cm},
        "selectors": sel,
        "mov_to_sreg": mov,
        "int_ring3": {"vectors": vec, "other_vectors": undec("no pinned source says whether Windows 7 has a ring-3 gate at any other vector; Wine 9.0 handles only these"),
                      "source": "%s: handle_interrupt cases %s; segv_handler / trap_handler map %s to #OF / #BP" % (wsrc, [hex(i) for i in ints], sorted(traps)),
                      "excluded": {str(v): {"status": "undecided", "why": gates[v]["source"]} for v in gated}},
        "cr4": {"tsd": {"value": 0 if not {0x31, 0x33} & set(ext_ops) else None,
                        "source": "%s: is_privileged_instr intercepts only the 0F opcodes %s, so rdtsc (0F 31) runs in user mode; consistent with the owner's decision that rdtsc reads the platform timer" % (wsrc, [hex(o) for o in ext_ops])},
                "umip": {"value": umip, "source": "Intel SDM Vol 3A 2.5 CR4.UMIP: the bit is reserved (setting it is #GP) unless CPUID.(EAX=07H,ECX=0):ECX[2] is enumerated; the declared CPU's max basic leaf is %d (%s max_basic @ %s), below 7, so there is no leaf 7 and CR4.UMIP is 0" % (max_basic, cm["path"], cm["commit"][:12])},
                "pce": undec("rdpmc at CPL 3 needs CR4.PCE = 1; the SDM only defines the bit, the declared CPU's cpuid does not state it, the WDK 7.1 constants name no PCE bit and Wine 9.0 has no rdpmc case")},
        "eflags": {"initial": {"value": eflags0, "source": "%s: the initial thread context, context.EFlags" % wsrc},
                   "iopl": {"value": (eflags0 & 0x3000) >> 12, "source": "bits 12-13 of the initial EFLAGS (%s)" % wsrc},
                   "iopl_user_modifiable": {"value": bool(sanit & 0x3000), "source": "%s: EFLAGS_USER_SANITIZE = 0x%X, the EFLAGS bits user code can set; bits 12-13 are %s" % (ks, sanit, "in it" if sanit & 0x3000 else "not in it")}},
        "tss_io_bitmap": {"value": "no port permitted", "ports_permitted": [], "exception": "EXCEPTION_PRIV_INSTRUCTION",
                          "source": "%s: is_privileged_instr returns EXCEPTION_PRIV_INSTRUCTION for every in/out/ins/outs form with no per-port test (the instruction set is L3b's sdm_priv_rows.tsv, iopl class, not restated here), i.e. a Win32 user process has no permitted port; the documentation names the TSS I/O map (WDK 7.1 ks386.inc TssIoMapBase = 0x%X, PrIopmOffset = 0x%X) and the exception (MSDN EXCEPTION_PRIV_INSTRUCTION: an instruction not allowed in the current machine mode) but states no permitted port for a user process, so it does not contradict Wine (precedence 2); with IOPL 0 (row eflags) every such instruction at CPL 3 is #GP(0), delivered as that exception" % (wsrc, kg.get("TssIoMapBase", -1), kg.get("PrIopmOffset", -1))},
        "thread_fp_initial": {"x87_control_word": {"value": next(iter(docv["fpcsr"])), "source": cites("fpcsr")},
                              "mxcsr": {"value": next(iter(docv["mxcsr"])), "source": cites("mxcsr")},
                              "applies_to": "the initial context of every thread, the process's first thread and CreateThread threads alike: the documentation names one initial value with no per-kind difference (the INITIAL_* constants of the thread context)"},
        "cr0": {"em": ({"value": 0, "source": "ruling of the orchestrator (each row is what a ring-3 thread observes on the declared OS): x87 executes natively, no emulation trap is visible; derived: CR0.EM = 1 iff PF_FLOATING_POINT_EMULATED is TRUE, MSDN IsProcessorFeaturePresent gives that value the meaning 'floating-point operations are emulated using a software emulator' (WDK 7.1 winnt.h line %d), the declared value is 0 (processor_features[1]), and MSDN KeSaveFloatingPointState (learn.microsoft.com wdm/nf-wdm-kesavefloatingpointstate, read 2026-11-15) names STATUS_ILLEGAL_FLOAT_CONTEXT 'the system is configured to use floating-point emulation, rather than doing floating-point operations in the processors' as the other configuration; ks386.inc CR0_EM = 0x%X names the bit" % (pf["1"]["winnt_h_line"], kg["CR0_EM"])} if pf["1"].get("value") == 0 else undec("derived from processor_features[1], which is not 0")),
                "ts": {"value": 0, "source": "ruling of the orchestrator (what a ring-3 thread observes): a ring-3 thread never sees #NM and its FP/SSE state is preserved across context switches; the documentation makes the x87 and SSE state part of the thread context (WDK 7.1 winnt.h line %d CONTEXT_FLOATING_POINT '387 state', FLOATING_SAVE_AREA line %d, ExtendedRegisters line %d) and no documented user-mode exception for a not-available FPU exists; MS text does not state smsw's TS bit, so this is the observable behaviour only; ks386.inc CR0_TS = 0x%X names the bit" % (cfp, fsa, extr, kg["CR0_TS"])},
                "mp": undec("no pinned source states CR0.MP (ks386.inc CR0_MP is a bit constant only); user mode sees it only through smsw (undecided)")},
        "processor_features": {"records": pf, "others": pfdoc["others"]},
        "cr4_sse": {"osfxsr": osfxsr,
                    "osxmmexcpt": {"value": 1, "source": "ruling of the orchestrator: an unmasked SIMD FP exception reaches the thread as the documented SEH codes, not as #UD; WDK 7.1 winnt.h line %d STATUS_FLOAT_MULTIPLE_FAULTS 0xC00002B4 and line %d STATUS_FLOAT_MULTIPLE_TRAPS 0xC00002B5 (MS-ERREF 2.3.1, read 2026-11-15: '{EXCEPTION} Multiple floating-point faults.' / '... traps.'); LEVEL 2 for the mapping from #XM to the code: Wine 9.0 dlls/ntdll/unix/signal_i386.c fpe_handler case TRAP_x86_CACHEFLT lines %d-%d raises STATUS_FLOAT_MULTIPLE_TRAPS (0xC00002B5); the MS pages name the codes but do not state which SIMD exception maps to which; ks386.inc CR4_XMMEXCPT = 0x%X names the bit" % (mf, mt, wx0, wx1, kg["CR4_XMMEXCPT"])}},
        "idtr_limit": undec("the number of gates of the OS's IDT is not stated by a pinned source (the SDM allows 256 entries at most)"),
        "sldt": undec("no pinned source states the LDTR of a process that has no LDT"),
        "descriptors": {n: {f: undec("the %s of the GDT entry behind selector 0x%X (%s) is a kernel table value; the pinned WDK 7.1 headers name the selector (KGDT_*) but give no descriptor contents, and Wine 9.0 uses host selectors and LDT entries, which contradicts the declared OS; searched: MS Learn 'x86 Architecture' (debugger/x86-architecture, read 2026-11-15) says only that Win32 uses the x86 in 32-bit flat mode and shows cs=001b ss=0023 ds=0023 es=0023 fs=0038 in a sample; it states no type, DPL, base or limit and what 'flat' fixes is defined by the Intel SDM, which is not pinned; blocker: a documented source for the Windows 7 SP1 x86 GDT entries" % (f, sel[n]["value"], n)) for f in ("type", "readable", "writable", "dpl", "base", "limit")} for n in ("cs", "ds", "es", "ss", "fs")},
        "lar_lsl": {n: undec("lar and lsl on selector 0x%X return the access rights and limit of the descriptor above, which is undecided; ZF is therefore undecided" % sel[n]["value"]) for n in ("cs", "ds", "es", "ss", "fs", "tr")},
        "sgdt": undec("the GDT base and limit of the OS are kernel values with no documented fixed value and no pinned source"),
        "sidt_base": undec("the IDT base is a kernel address with no documented fixed value and no pinned source"),
    }


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--os", required=True)
    ap.add_argument("--sources", required=True)
    ap.add_argument("--mirror", required=True)
    ap.add_argument("--wine", required=True, help="checkout of fnv-nx-build tools/wine-9.0")
    ap.add_argument("--cpuref", required=True, help="verify/cpuref.json of the sources record")
    ap.add_argument("--cpumodel", required=True, help="verify/cpuid_model.json of the sources record")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    raw = open(a.os, "rb").read()
    rec = build(json.loads(raw), json.load(open(a.sources)), a.mirror, a.wine, a.cpumodel, a.cpuref)
    rec["os"]["sha256"] = hashlib.sha256(raw).hexdigest()
    with open(a.out, "w") as f:
        json.dump(rec, f, indent=1, sort_keys=True)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
