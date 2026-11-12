#!/usr/bin/env python3
"""Runner of rule R-LOCKOBS (journal/inbox/l4-verify-012.md): the platform-lock observation of a boot-machine run.
    python3 lockobs.py --out OUT.json --elf ELF --cases CASES.json [--report REPORT.json]
Input: OUT.json of `boot.py run --watch-log SYMS` (watch_schema fnvnx-watch/2; SYMS lists os_lock, os_trylock ret, os_unlock, and every case runner
as `NAME ret`), the ELF (symbols, to map addresses), and the case expectations {"cases": [{"runner_sym": NAME, "violations": [{"dst": SYM|ADDR}
| {"at": "return"}]}]}. Exit 0: every case's observed violations equal its declared ones and none lies outside a case; 2: a difference; 1: input error.

Per tid, held = multiset of lock addresses (x0 of the call, low 32 bits):
  +1 at the os_lock call; +1 at the os_trylock return whose w0 is the success value, 1 (os.h:42, `int os_trylock(os_lock_t *l); /* 1 if acquired */`,
  platform/os/os.h of claude/fnv-l5-platform); -1 at the os_unlock call of an address the tid holds; an unlock of an address the tid does not hold is
  counted (foreign_unlocks) and ignored.
A1: at every game-entry record, held(tid) must be empty (a violation {"dst": the entry target} with the acquisition sites of the held locks).
A2: at every return (or unwound record) of a case runner, held(tid) must be empty (a violation {"at": "return"}).
A violation belongs to the innermost case-runner call pending on its tid; one outside every case is `outside` and always a failure. A case's
violations are compared exactly (as a multiset) with its declared ones; a missing and an extra one are both listed. Cases of one runner_sym
(several runs of the same runner) are one case. Acquisition sites are the x30 of the acquiring call, as symbol+offset in the ELF."""
import argparse
import bisect
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bootmiles  # noqa: E402

LOCK, TRYLOCK, UNLOCK = "os_lock", "os_trylock", "os_unlock"
TRYLOCK_OK = 1   # os.h:42
MASK = 0xFFFFFFFF


class InputError(Exception):
    pass


class Symtab:
    def __init__(self, elf):
        syms = bootmiles.read_symtab(elf)
        self.addr = {n: syms[n] for n in syms.funcs if n in syms}
        pairs = sorted((a, n) for n, a in self.addr.items())
        self.starts, self.names = [a for a, _ in pairs], [n for _, n in pairs]

    def where(self, a):
        i = bisect.bisect_right(self.starts, a) - 1
        return "0x%x" % a if i < 0 else "%s+0x%x" % (self.names[i], a - self.starts[i])

    def dst(self, x):
        if isinstance(x, int) and not isinstance(x, bool):
            return x
        if x not in self.addr:
            raise InputError("declared dst %r is not a function symbol of the ELF" % (x,))
        return self.addr[x]


def analyze(doc, cases, syms):
    if doc.get("watch_schema") != "fnvnx-watch/2":
        raise InputError("OUT.json has no watch_log of schema fnvnx-watch/2 (run boot.py with --watch-log)")
    spec = doc["watch_spec"]
    need = {LOCK: False, TRYLOCK: True, UNLOCK: False}
    for c in cases:
        need[c["runner_sym"]] = True
    for n, ret in need.items():
        if n not in spec["names"] or (ret and n not in spec["ret"]):
            raise InputError("the watch file must list %s%s" % (n, " ret" if ret else ""))
    held, sites, pending = {}, {}, {}   # tid -> {addr: count}; tid -> {addr: [x30 of each acquisition]}; tid -> [runner syms pending]
    obs = {c["runner_sym"]: {"instances": 0, "violations": []} for c in cases}
    outside, foreign = [], 0
    runners = set(obs)

    def violate(tid, v):
        st = pending.get(tid, [])
        (obs[st[-1]]["violations"] if st else outside).append(v)

    def held_list(tid):
        return [{"lock": a, "sites": [syms.where(x) for x in sites[tid][a]]} for a, n in sorted(held.get(tid, {}).items()) if n]

    for r in doc["watch_log"]:
        tid, kind, sym = r["tid"], r["kind"], r.get("sym")
        h, s = held.setdefault(tid, {}), sites.setdefault(tid, {})
        if kind == "call" and sym == LOCK:
            a = r["x0"] & MASK
            h[a] = h.get(a, 0) + 1
            s.setdefault(a, []).append(r["x30"])
        elif kind == "return" and sym == TRYLOCK and r["w0"] == TRYLOCK_OK:
            a = r["x0"] & MASK
            h[a] = h.get(a, 0) + 1
            s.setdefault(a, []).append(r["x30"])
        elif kind == "call" and sym == UNLOCK:
            a = r["x0"] & MASK
            if h.get(a, 0):
                h[a] -= 1
                s[a].pop()
            else:
                foreign += 1
        elif kind == "game-entry" and any(h.values()):
            violate(tid, {"seq": r["seq"], "tid": tid, "dst": r["dst"], "dst_sym": syms.where(r["dst"]), "src": syms.where(r["src"]),
                          "held": held_list(tid)})
        if sym in runners and kind == "call":
            pending.setdefault(tid, []).append(sym)
            obs[sym]["instances"] += 1
        elif sym in runners and kind in ("return", "unwound"):
            if any(h.values()):
                violate(tid, {"seq": r["seq"], "tid": tid, "at": "return", "unwound": kind == "unwound", "held": held_list(tid)})
            st = pending.get(tid, [])
            for i in range(len(st) - 1, -1, -1):
                if st[i] == sym:
                    del st[i]
                    break
    out = []
    for c in cases:
        o = obs[c["runner_sym"]]
        decl = sorted(("ret",) if d.get("at") == "return" else ("dst", syms.dst(d["dst"])) for d in c.get("violations", []))
        got = sorted(("ret",) if v.get("at") == "return" else ("dst", v["dst"]) for v in o["violations"])
        missing, extra = list(decl), []
        for g in got:
            if g in missing:
                missing.remove(g)
            else:
                extra.append(g)
        out.append({"runner_sym": c["runner_sym"], "instances": o["instances"], "violations": o["violations"],
                    "missing": [list(m) for m in missing], "extra": [list(e) for e in extra], "ok": not missing and not extra})
    ok = all(c["ok"] for c in out) and not outside
    return {"format": "fnvnx-lockobs/1", "ok": ok, "cases": out, "outside": outside, "foreign_unlocks": foreign}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--elf", required=True)
    ap.add_argument("--cases", required=True)
    ap.add_argument("--report")
    a = ap.parse_args(argv)
    try:
        with open(a.out) as f:
            doc = json.load(f)
        with open(a.cases) as f:
            cases = json.load(f)["cases"]
        rep = analyze(doc, cases, Symtab(a.elf))
    except (InputError, OSError, ValueError, KeyError, bootmiles.ElfError) as e:
        print("lockobs: input error: %s" % e, file=sys.stderr)
        return 1
    if a.report:
        with open(a.report, "w") as f:
            f.write(json.dumps(rep, sort_keys=True, indent=1) + "\n")
    print("lockobs: %s cases=%d outside=%d foreign_unlocks=%d" % ("ok" if rep["ok"] else "DIFFERENT", len(rep["cases"]), len(rep["outside"]),
                                                                  rep["foreign_unlocks"]))
    return 0 if rep["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
