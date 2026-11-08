#!/usr/bin/env python3
"""callwatch.py - the call watcher (D25 #10 script errors; tools/report/scripterr.py reads its lines): log every entry
of chosen guest functions with chosen argument values, in linhost and in the emulator.

    callwatch.py resolve <config> --elf <guest.elf> --out <file>     -> linhost --calls <file>
    callwatch.py --calls <config> <emu_guest.py arguments...>         the emulator, as a wrapper
    callwatch.install(emu_guest, <config>)                            from another wrapper (tools/scenario/emu_scenario.py)

Config (one function per line, '#' comments): <target> <name> [<label>=<spec>]...
  target  0xORIG (an exe address: the image function's native symbol __fnv_<orig>), re:REGEX (every ELF function
          symbol matching), or a symbol name
  name    the kind the reports use (e.g. the error class this entry point reports)
  spec    xN (the argument register, 32 bits) or *xN+OFF (the 32-bit word at xN + OFF, e.g. a form's id field)
          labels are free; scripterr.py reads `form` (the script's form id) and `code`
The config names which game functions report script and console errors and where their arguments hold the form; it
is game knowledge, so it lives with the farm's private data (the owner / S18 / S5 write it), never in this
repository. Lines carry values only, never strings:
  linhost:  linhost: call <name> caller 0x<lr> <label> 0x<value> ...      (addresses: scripterr.py maps them with nm)
  emulator: emu: call <name> caller <function> <label> 0x<value> ...     (function: orig:XXXXXXXX or the native name)
The first 5000 calls are logged (both sides), then counted: "... calls <name> n <count>" at the end.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.dirname(os.path.dirname(HERE))
S5 = os.environ.get("SCENARIO_EMU_DIR", os.path.join(P, "tests", "s5"))
SPEC = re.compile(r"^(\w+)=(\*?)x(\d)(?:\+(0x[0-9a-fA-F]+|\d+))?$")
MAX_LINES = 5000


def parse_config(path):
    out = []
    for line in open(path):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        f = line.split()
        if len(f) < 2:
            continue
        specs = []
        for t in f[2:]:
            m = SPEC.match(t)
            if not m:
                raise SystemExit("callwatch: bad spec %r in %s" % (t, path))
            specs.append((m.group(1), int(m.group(3)), m.group(2) == "*", int(m.group(4) or "0", 0)))
        out.append((f[0], f[1], specs))
    return out


def spec_text(specs):
    return " ".join("%s=%sx%d%s" % (l, "*" if d else "", r, "+%#x" % o if o else "") for l, r, d, o in specs)


def symbols(elf):
    nm = next((t for t in ("aarch64-none-elf-nm", "aarch64-linux-gnu-nm", "llvm-nm", "nm") if shutil.which(t)), None)
    syms = {}
    if nm and elf:
        for line in subprocess.run([nm, "--defined-only", elf], capture_output=True, text=True).stdout.splitlines():
            f = line.split()
            if len(f) >= 3 and f[1] in "TtWw":
                syms.setdefault(f[2], int(f[0], 16))
    return syms


def targets(target, lookup):
    """addresses of one config target; lookup(name) -> address or None, names() -> iterable of symbol names"""
    get, names = lookup
    if target.startswith("re:"):
        rx = re.compile(target[3:])
        return sorted({a for a in (get(n) for n in names() if rx.search(n)) if a is not None})
    if re.fullmatch(r"0x[0-9a-fA-F]+", target):
        a = get("__fnv_%08x" % int(target, 16))
        return [a] if a is not None else []
    a = get(target)
    return [a] if a is not None else []


def resolve(config, elf, out):
    syms = symbols(elf)
    lines, missing = ["# fnvnx-callwatch/1 (linhost --calls)"], []
    for target, name, specs in parse_config(config):
        addrs = targets(target, (syms.get, syms.keys))
        if not addrs:
            missing.append(target)
        lines += ["call %#x %s %s" % (a, name, spec_text(specs)) for a in addrs]
    open(out, "w").write("\n".join(lines) + "\n")
    print("callwatch: %d calls watched; not in the link: %s" % (len(lines) - 1, ", ".join(missing) or "-"),
          file=sys.stderr)
    return 0


def func_key(sym):
    """emu.sym text -> the stable function key faults.py uses"""
    m = re.search(r"\[orig ([0-9a-fA-F]{8})\]", sym)
    if m:
        return "orig:" + m.group(1).lower()
    s = re.sub(r"^0x[0-9a-fA-F]+\s*", "", sym.strip())
    return re.sub(r"\+0x[0-9a-fA-F]+$", "", s) or "?"


def install(eg, config, regs=None):
    """patch emu_guest.Emu.run: before the guest runs, a code hook on every watched function"""
    try:
        from unicorn import UC_HOOK_CODE
        from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X30
        X = [UC_ARM64_REG_X0 + i for i in range(9)]
        LR = UC_ARM64_REG_X30
    except ImportError:   # tests with a stand-in emulator
        UC_HOOK_CODE, X, LR = "code", ["x%d" % i for i in range(9)], "x30"
    if regs:
        X, LR = regs
    cfg = parse_config(config) if isinstance(config, str) else config
    Emu = eg.Emu
    orig_run = Emu.run
    state = {"lines": 0, "count": {}}

    def on_call(uc, addr, size, data):
        emu, name, specs = data
        state["count"][name] = state["count"].get(name, 0) + 1
        state["lines"] += 1
        if state["lines"] > MAX_LINES:
            return
        parts = ["emu: call %s caller %s" % (name, func_key(emu.sym(uc.reg_read(LR))) if hasattr(emu, "sym")
                                              else "%#x" % uc.reg_read(LR))]
        for label, r, deref, off in specs:
            v = uc.reg_read(X[r]) & 0xFFFFFFFF
            if deref:
                try:
                    v = int.from_bytes(bytes(uc.mem_read(v + off, 4)), "little")
                    parts.append("%s %#x" % (label, v))
                except Exception:
                    parts.append("%s unreadable" % label)
            else:
                parts.append("%s %#x" % (label, v))
        print(" ".join(parts))

    def run(self, *a, **k):
        code = getattr(self, "code_syms", {}) or {}

        def get(n):
            return self.code_addr(n) if hasattr(self, "code_addr") else code.get(n)
        n = 0
        for target, name, specs in cfg:
            for addr in targets(target, (get, code.keys)):
                self.uc.hook_add(UC_HOOK_CODE, on_call, user_data=(self, name, specs), begin=addr, end=addr)
                n += 1
        print("   callwatch: %d functions watched" % n)
        try:
            return orig_run(self, *a, **k)
        finally:
            for name, c in sorted(state["count"].items()):
                print("emu: calls %s n %d" % (name, c))
    Emu.run = run
    return state


def main(argv):
    if argv[:1] == ["resolve"]:
        import argparse
        ap = argparse.ArgumentParser(prog="callwatch.py resolve")
        ap.add_argument("config")
        ap.add_argument("--elf", required=True)
        ap.add_argument("--out", required=True)
        a = ap.parse_args(argv[1:])
        return resolve(a.config, a.elf, a.out)
    if "--calls" in argv:
        i = argv.index("--calls")
        config, rest = argv[i + 1], argv[:i] + argv[i + 2:]
        sys.path.insert(0, S5)
        import emu_guest as eg   # noqa: E402
        install(eg, config)
        return eg.main(rest)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
