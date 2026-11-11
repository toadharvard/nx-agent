#!/usr/bin/env python3
"""The boot stage CLI: runs the guest ELF under bootmachine.Machine, watches the milestone table, counts instructions.

  boot.py run --elf GUEST.elf --reserved modules.tsv --keys LO:HI --heap-size N --root SDROOT [--game DIR]
              --milestones TABLE --manifest entries.tsv [--icount-spec SPEC.json] [--limit-instructions N]
              [--hc-slice N] --out OUT.json

plan = bootlayout (same rules as its CLI), watch points = bootmiles.resolve(table, manifest, ELF symbols, bias = image
base). OUT.json ("fnvnx-boot/1", sorted keys, indent 1, no wall time) holds the plan, the run record, the milestone state
and log, and the Icount report; the console text is OUT.console.txt. The tool only reports: which milestone must be
reached is the caller's verdict. Exit 0: run reason "exit" with code 0, or "limit"; 2: fault / deadlock /
ext-module-missing / nonzero exit / reach-violation (guest exit code 0x52000000|fact_id, reported with its fact_id); 1: usage or input error (message on stderr).

Host parts: the table's `host` target is an ID word, so the exact console line a part waits for is spelled
`console.<hex>` (hex of the line's UTF-8 bytes, no newline; helper console_part(line)). The part fires when the guest
writes exactly that line. Any other host name is an input error listing the known form.
"""
import argparse
import hashlib
import json
import os
import sys
import time

import bootlayout
import bootmachine
import bootmiles
import boothost

FORMAT = "fnvnx-boot/1"
HOST_PREFIX = "console."
INPUT_ERRORS = (bootlayout.LayoutError, bootmiles.TableError, bootmiles.ManifestError, bootmiles.ResolveError,
                bootmiles.ElfError, bootmachine.MachineError, ValueError, OSError)


def console_part(line):
    """the `host` target that fires when the guest writes exactly `line`"""
    return HOST_PREFIX + line.encode().hex()


def _host_lines(host):
    lines, bad = {}, []
    for name, ids in host.items():
        try:
            if not name.startswith(HOST_PREFIX) or not name[len(HOST_PREFIX):]:
                raise ValueError
            lines.setdefault(bytes.fromhex(name[len(HOST_PREFIX):]).decode(), []).extend(ids)
        except ValueError:
            bad.append(name)
    if bad:
        raise bootmiles.ResolveError("unknown host names %s; the only host event is %s<hex of the exact console line>"
                                     % (sorted(bad), HOST_PREFIX))
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
    return bootlayout.plan(bootlayout.parse_elf(raw), sorted(rows, key=lambda r: r[1]), a.heap_size)


def run(a):
    t0 = time.time()
    with open(a.elf, "rb") as f:
        raw = f.read()
    plan = _plan(a, raw)
    base = plan["image"]["base"]
    with open(a.milestones) as f:
        table = bootmiles.load_table(f.read())
    with open(a.manifest) as f:
        manifest = bootmiles.load_manifest(f.read())
    watch = bootmiles.resolve(table, manifest, bootmiles.read_symtab(a.elf), base)
    host = _host_lines(watch["host"])
    kw = {"hc_slice": a.hc_slice} if a.hc_slice else {}
    m = bootmachine.Machine(raw, plan, boothost.Services(a.root, a.game), limit_instructions=a.limit_instructions, **kw)
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
    m.on_console(lambda line: [fire(i, m.tid()) for i in host.get(line, ())])
    r = m.run()
    ic.stop(r["reason"])
    console = r.pop("console")
    rep = ic.report()
    doc = {"elf_sha256": hashlib.sha256(raw).hexdigest(), "plan": plan, "run": r,
           "console_sha256": hashlib.sha256(console.encode()).hexdigest(), "milestones": prog.state(),
           "milestone_log": log, "last_reached": prog.last_reached(), "icount": rep}
    with open(a.out, "w") as f:
        f.write(json.dumps(doc, sort_keys=True, indent=1) + "\n")
    with open(os.path.splitext(a.out)[0] + ".console.txt", "w", encoding="utf-8") as f:
        f.write(console)
    tot = rep["totals"]
    print("boot: reason=%s last=%s instr=%d main=%d threads=%d vns=%d wall=%.1f" % (
        r["reason"], prog.last_reached() or "none", tot["all"]["total"], tot.get("1", {"total": 0})["total"],
        r["threads"], r["virtual_ns"], time.time() - t0))
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
    a = ap.parse_args(argv)
    try:
        return run(a)
    except INPUT_ERRORS as e:
        print("boot run: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
