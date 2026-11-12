#!/usr/bin/env python3
"""The boot stage CLI: runs the guest ELF under bootmachine.Machine, watches the milestone table, counts instructions.

  boot.py run --elf GUEST.elf --reserved modules.tsv --keys LO:HI --heap-size N --root SDROOT [--game DIR]
              --milestones TABLE --manifest entries.tsv [--icount-spec SPEC.json] [--limit-instructions N]
              [--hc-slice N] [--declared-inputs PINS [--declared-src NAME=DIR]] [--profile handheld|docked] [--watch-log SYMS] --out OUT.json

plan = bootlayout (same rules as its CLI), watch points = bootmiles.resolve(table, manifest, ELF symbols, bias = image
base). OUT.json ("fnvnx-boot/1", sorted keys, indent 1, no wall time) holds the plan, the run record, the milestone state
and log, and the Icount report; the console text is OUT.console.txt. The tool only reports: which milestone must be
reached is the caller's verdict. Exit 0: run reason "exit" with code 0, or "limit"; 2: fault / deadlock /
ext-module-missing / scenario-error (a --scenario file that is not valid, or a --text-entry text longer than the call's max_len) / nonzero exit / reach-violation (guest exit code 0x52000000|fact_id, reported with its fact_id); 1: usage or input error (message on stderr).

Watch log: --watch-log SYMS (text file, one ELF symbol per line, # starts a comment) logs every execution of each named function's
first instruction: OUT.json "watch_log" = [{"seq", "tid", "t_ns" (virtual clock), "kind", "sym", "pc" (guest-relative), "sp", ...}] with
"watch_schema": "fnvnx-watch/2" (a superset of /1: the keys seq, tid, sym, pc, sp are kept), seq from 0 in machine order, no sampling, no cap,
two names at one address in file order. Kinds: "call" (x0, x30 = the call site, guest-relative); "return" for a name marked `NAME ret`
in the file: taken when pc = that call's entry x30 on the same tid with sp = the entry sp, carries w0 (calls nest: a per-tid stack matches
them); "unwound": a pending `ret` frame whose entry sp is below the tid's current sp was left by a longjmp (a return the code never makes);
it is detected at the tid's next record (any call, return or game-entry). A longjmp whose target is the frame that called the watched
function leaves sp equal to the entry sp, which a leaf callee also has, so it is told only when sp later rises above the entry sp (the
caller's own return or a further longjmp); a target above an intermediate frame is told at the next record, carries sp (the current one) and entry_sp; "game-entry" (only if the ELF
has the section .text.fnvl): a BL/BLR whose source is outside the section and whose landing is inside it (src, dst, decoded from the
instruction word, no names). A name that is absent or not STT_FUNC is an input error naming it.
Without the option the key is absent.

Host parts: the table's `host` target is an ID word, so the exact console line a part waits for is spelled
`console.<hex>` (hex of the line's UTF-8 bytes, no newline; helper console_part(line)). The part fires when the guest
writes exactly that line. Two more host events exist: `present` (every present of the renderer) and `gpu-first-call` (the first
guest call of ext module 10); they only fire with --gpu. Any other host name is an input error listing the known forms.

Renderer: --gpu LIB registers ext module 10 and forwards it to the host library LIB (bootgpu.py); --frames DIR is its
$NXGL4_FRAMES (the library writes frame_%06u.rgba/.txt there). The three GL variables for software rendering are set for it.
Without --gpu module 10 is not registered (a guest call answers NO_MODULE). The first 3 presents are icount marks `frame-<n>`;
OUT.json "gpu" = {library_sha256, presents, first_module10_call, last_error}.
"""
import argparse
import hashlib
import json
import os
import sys
import time

from unicorn.arm64_const import UC_ARM64_REG_LR, UC_ARM64_REG_X0

import bootinput
import bootdecl
import bootgpu
import bootlayout
import bootmachine
import bootmiles
import boothost

FORMAT = "fnvnx-boot/1"
HOST_PREFIX = "console."
HOST_EVENTS = ("present", "gpu-first-call")
GPU_ENV = {"LIBGL_ALWAYS_SOFTWARE": "1", "GALLIUM_DRIVER": "llvmpipe", "EGL_PLATFORM": "surfaceless"}
FRAME_MARKS = 3   # presents that get an icount mark; later ones are only counted
INPUT_ERRORS = (bootdecl.InputError, bootlayout.LayoutError, bootmiles.TableError, bootmiles.ManifestError, bootmiles.ResolveError,
                bootmiles.ElfError, bootmachine.MachineError, bootgpu.GpuError, ValueError, OSError)


def console_part(line):
    """the `host` target that fires when the guest writes exactly `line`"""
    return HOST_PREFIX + line.encode().hex()


def _host_lines(host):
    lines, bad = {}, []
    for name, ids in host.items():
        if name in HOST_EVENTS:
            continue
        try:
            if not name.startswith(HOST_PREFIX) or not name[len(HOST_PREFIX):]:
                raise ValueError
            lines.setdefault(bytes.fromhex(name[len(HOST_PREFIX):]).decode(), []).extend(ids)
        except ValueError:
            bad.append(name)
    if bad:
        raise bootmiles.ResolveError("unknown host names %s; the host events are %s and %s<hex of the exact console line>"
                                     % (sorted(bad), ", ".join(HOST_EVENTS), HOST_PREFIX))
    return lines


def _plan(a, raw):
    rows = bootlayout.normalize_reserved(bootlayout.read_tsv(a.reserved))
    try:
        lo, hi = (int(x, 16) for x in a.keys.split(":"))
    except ValueError:
        raise bootlayout.LayoutError("--keys %r: need LO:HI in hex" % a.keys)
    if not lo < hi or lo < bootlayout.MIN_ADDR:
        raise bootlayout.LayoutError("--keys: need %x <= lo < hi, got 0x%x..0x%x" % (bootlayout.MIN_ADDR, lo, hi))
    rows.append(("keys", lo // 4096 * 4096, (hi + 4095) // 4096 * 4096))
    return bootlayout.plan(bootlayout.parse_elf(raw), sorted(rows, key=lambda r: r[1]), a.heap_size, n_stacks=a.threads)


def _watch_names(path, syms):
    """-> (names in file order, the names marked `ret`); a line is `NAME` or `NAME ret`"""
    names, rets = [], set()
    with open(path) as f:
        for line in f:
            t = line.split("#", 1)[0].split()
            if not t:
                continue
            if len(t) > 2 or (len(t) == 2 and t[1] != "ret"):
                raise bootmiles.ResolveError("--watch-log: a line is `NAME` or `NAME ret`, got: %s" % line.strip())
            names.append(t[0])
            if len(t) == 2:
                rets.add(t[0])
    bad = [n for n in names if n not in syms or n not in syms.funcs]
    if bad:
        raise bootmiles.ResolveError("--watch-log: not a function symbol of the ELF: %s" % ", ".join(sorted(set(bad))))
    return names, rets


def run(a):
    t0 = time.time()
    with open(a.elf, "rb") as f:
        raw = f.read()
    plan = _plan(a, raw)
    declared = []
    if a.declared_inputs:   # verified and mounted read-only on the SD root before anything runs; a mismatch is an input error
        declared = bootdecl.mount_all(a.declared_inputs, a.root, dict(s.split("=", 1) for s in a.declared_src))
    base = plan["image"]["base"]
    with open(a.milestones) as f:
        table = bootmiles.load_table(f.read())
    with open(a.manifest) as f:
        manifest = bootmiles.load_manifest(f.read())
    syms = bootmiles.read_symtab(a.elf)
    watch = bootmiles.resolve(table, manifest, syms, base)
    host = _host_lines(watch["host"])
    wl, wl_ret = _watch_names(a.watch_log, syms) if a.watch_log else (None, set())
    sc, text_entries = None, a.text_entry
    if a.scenario:
        try:
            with open(a.scenario, encoding="utf-8") as f:
                sc = bootinput.Scenario(f.read())
            if sc.text_entries is not None:
                if a.text_entry:
                    raise bootinput.ScenarioError("text_entries in the scenario together with --text-entry")
                text_entries = sc.text_entries
        except bootinput.ScenarioError as e:   # a range or format error ends the run as scenario-error, nothing is clamped
            doc = {"elf_sha256": hashlib.sha256(raw).hexdigest(), "plan": plan, "run": {"reason": "scenario-error", "scenario": str(e)},
                   "inputs": {"scenario_error": str(e)}}
            with open(a.out, "w") as f:
                f.write(json.dumps(doc, sort_keys=True, indent=1) + "\n")
            print("boot: reason=scenario-error %s" % e)
            return 2
    kw = {"hc_slice": a.hc_slice} if a.hc_slice else {}
    m = bootmachine.Machine(raw, plan, boothost.Services(a.root, a.game, sc, watch_reads=wl is not None), limit_instructions=a.limit_instructions,
                            profile=a.profile, text_entries=text_entries, n_stacks=a.threads, **kw)
    ic = m.attach_icount(a.icount_spec if a.icount_spec else {}, base)
    prog, log = bootmiles.Progress(table), []

    def fire(pid, tid):
        for name, ooo in prog.event(pid, tid):
            ic.mark(name, ooo)
            log.append({"name": name, "out_of_order": ooo, "tid": tid, "virtual_ns": m.clock.now_ns(),
                        "host_calls": m.calls, "instructions_total": m.instr})

    m.on_thread(ic.thread)
    for addr, ids in watch["pc"].items():
        m.watch_pc(addr, lambda tid, pc, ids=ids: [fire(i, tid) for i in ids])
    for addr, ids in watch["ret"].items():
        def returned(tid, x0, ids=ids):
            for i in ids:
                k = table.parts[i]["kind"]
                if k == "ret" or (k == "retzero" and x0 & 0xFFFFFFFF == 0) or (k == "rettrue" and x0 & 0xFF):
                    fire(i, tid)
        m.watch_ret(addr, returned)
    for addr, ids in watch["store"].items():
        def stored(tid, at, size, value, addr=addr, ids=ids):
            if at == addr and size == 4 and value:
                for i in ids:
                    fire(i, tid)
        m.watch_store(addr, stored)
    wlog, frames, rets_hooked = [], {}, set()   # frames: tid -> pending calls of `ret` names, innermost last

    def rec(tid, kind, **kw):
        wlog.append(dict(seq=len(wlog), tid=tid, t_ns=m.clock.now_ns(), kind=kind, **kw))

    def unwind(tid):
        """a longjmp (or any other transfer out of a watched call) is a return the code never makes: a pending frame whose entry sp is below
        the current sp is dead (a live frame's callee runs at or below its entry sp). It is recorded as "unwound" at the next record of the tid"""
        st, sp = frames.get(tid, []), m.sp()
        while st and st[-1]["sp"] < sp:
            f = st.pop()
            rec(tid, "unwound", sym=f["sym"], pc=f["pc"], sp=sp, entry_sp=f["sp"], x30=f["x30"])

    def returned(pc_abs):
        tid, sp = m.tid(), m.sp()
        unwind(tid)
        st = frames.get(tid, [])
        for i in range(len(st) - 1, -1, -1):
            if st[i]["ret"] == pc_abs and st[i]["sp"] == sp:
                f = st.pop(i)
                rec(tid, "return", sym=f["sym"], pc=pc_abs - base, sp=sp, w0=m.uc.reg_read(UC_ARM64_REG_X0) & 0xFFFFFFFF, x0=f["x0"], x30=f["x30"])
                return
    for name in wl or ():
        def logged(tid, pc, name=name):
            unwind(tid)
            x0, x30 = m.uc.reg_read(UC_ARM64_REG_X0), m.uc.reg_read(UC_ARM64_REG_LR) & 0xFFFFFFFF
            rec(tid, "call", sym=name, pc=pc - base, sp=m.sp(), x0=x0, x30=(x30 - base) & 0xFFFFFFFF)
            if name in wl_ret:
                frames.setdefault(tid, []).append({"sym": name, "pc": pc - base, "sp": m.sp(), "ret": x30, "x0": x0, "x30": (x30 - base) & 0xFFFFFFFF})
                if x30 not in rets_hooked:
                    rets_hooked.add(x30)
                    m.watch_pc(x30, lambda tid_, pc_, x30=x30: returned(x30))
        m.watch_pc(syms[name] + base, logged)
    fnvl = bootmiles.read_section(a.elf, ".text.fnvl") if wl is not None else []
    for lo, size in fnvl:   # game entry: a call from outside the lifted section into it (the section's own range, no list of functions)
        def entered(tid, src, dst):
            unwind(tid)
            rec(tid, "game-entry", pc=dst - base, sp=m.sp(), src=src - base, dst=dst - base)
        m.watch_calls_into(lo + base, lo + base + size, entered)
    m.on_console(lambda line: [fire(i, m.tid()) for i in host.get(line, ())])
    gpu = None
    if a.gpu:
        os.environ.update(GPU_ENV)
        if a.frames:
            os.makedirs(a.frames, exist_ok=True)
            os.environ["NXGL4_FRAMES"] = a.frames
        else:
            os.environ.pop("NXGL4_FRAMES", None)

        def presented(frame):
            n = gpu.presents
            if n <= FRAME_MARKS:
                ic.mark("frame-%d" % n)
            for i in watch["host"].get("present", ()):
                fire(i, m.tid())
        gpu = bootgpu.Gpu(a.gpu, m, a.profile, on_present=presented,
                          on_first_call=lambda: [fire(i, m.tid()) for i in watch["host"].get("gpu-first-call", ())])
    try:
        r = m.run()
    finally:
        if gpu:
            gpu_report = gpu.report()
            gpu.close()
    ic.stop(r["reason"])
    console = r.pop("console")
    rep = ic.report()
    doc = {"elf_sha256": hashlib.sha256(raw).hexdigest(), "plan": plan, "run": r,
           "console_sha256": hashlib.sha256(console.encode()).hexdigest(), "milestones": prog.state(),
           "milestone_log": log, "inputs": {"text_entry": list(text_entries)}, "manifest_id": manifest.table_id, "last_reached": prog.last_reached(), "icount": rep}
    if declared:
        doc["inputs"]["declared"] = declared
    if sc:
        doc["inputs"]["scenario"] = {"sha256": sc.sha256, "content": sc.text}
    diff = sc.check_expect(r["events"]) if sc else []
    if sc:
        doc["inputs"]["expect_events"] = {"ok": not diff, "diff": diff}
    if wl is not None:
        doc["watch_log"] = wlog
        doc["watch_schema"] = "fnvnx-watch/2"
        doc["watch_spec"] = {"names": sorted(set(wl)), "ret": sorted(wl_ret)}
    if gpu:
        doc["gpu"] = gpu_report
    with open(a.out, "w") as f:
        f.write(json.dumps(doc, sort_keys=True, indent=1) + "\n")
    with open(os.path.splitext(a.out)[0] + ".console.txt", "w", encoding="utf-8") as f:
        f.write(console)
    tot = rep["totals"]
    print("boot: reason=%s last=%s instr=%d main=%d threads=%d vns=%d wall=%.1f" % (
        r["reason"], prog.last_reached() or "none", tot["all"]["total"], tot.get("1", {"total": 0})["total"],
        r["threads"], r["virtual_ns"], time.time() - t0))
    if diff:
        print("boot: expect_events differ: %s" % "; ".join(diff))
        return 2
    return 0 if r["reason"] == "limit" or (r["reason"] == "exit" and r["exit_code"] == 0) else 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("run", help="run the guest and write OUT.json (format %s)" % FORMAT)
    for opt in ("--elf", "--reserved", "--keys", "--root", "--milestones", "--manifest", "--out"):
        sp.add_argument(opt, required=True)
    sp.add_argument("--heap-size", required=True, type=lambda s: int(s, 0))
    sp.add_argument("--game")
    sp.add_argument("--icount-spec")
    sp.add_argument("--limit-instructions", type=lambda s: int(s, 0))
    sp.add_argument("--hc-slice", type=lambda s: int(s, 0))
    sp.add_argument("--gpu", help="host renderer library libnxgl4.so: registers ext module 10 (bootgpu.py)")
    sp.add_argument("--frames", help="directory for the library's frame files ($NXGL4_FRAMES); needs --gpu")
    sp.add_argument("--threads", type=int, default=256,
                    help="guest thread stacks the stage provides (a number of the run, not of the contract; beyond it "
                         "thread_create returns 0 as the header says for exhaustion); default 256")
    sp.add_argument("--profile", choices=("handheld", "docked"), default="handheld",
                    help="what FNVNX_PLAT_DISPLAY_DOCKED answers: docked 1, handheld 0 (default handheld)")
    sp.add_argument("--watch-log", metavar="SYMS", help="text file of ELF function names (NAME or NAME ret); OUT.json gets watch_log (fnvnx-watch/2), every hit of each")
    sp.add_argument("--scenario", metavar="FILE", help="device states over virtual time, schema fnvnx-scenario/1 (bootinput.py, rule R-INPUT)")
    sp.add_argument("--text-entry", action="append", default=[], metavar="TEXT",
                    help="the answer of one TEXT_ENTRY call (the system keyboard), repeatable, consumed in order; none left: the call "
                         "is cancelled (0); a text longer than the call's max_len ends the run as scenario-error")
    sp.add_argument("--declared-inputs", metavar="PINS", help="verify/boot_inputs.json: each pinned port input (the font set) is fetched by its commit "
                    "(or taken from --declared-src), verified against its checksum list and mounted read-only under --root (bootdecl.py); "
                    "any mismatch is an input error and nothing runs; OUT.json inputs.declared holds {name, commit, sums_sha256, files}")
    sp.add_argument("--declared-src", action="append", default=[], metavar="NAME=DIR",
                    help="a checkout holding the input's path and sums file, in place of the fetch (needs --declared-inputs)")
    a = ap.parse_args(argv)
    if a.declared_src and not a.declared_inputs:
        ap.error("--declared-src needs --declared-inputs")
    if a.frames and not a.gpu:
        ap.error("--frames needs --gpu")
    try:
        return run(a)
    except INPUT_ERRORS as e:
        print("boot run: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
