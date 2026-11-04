#!/usr/bin/env python3
"""Write the response file of the game objects for the guest link, optionally taking the global allocation
functions away from them.

    objlist.py <fixed dir> <out dir> <runtime|game> <nm> <objcopy>

Writes <out>/game_objects.list (quoted paths, the objects in glob order). With `runtime`, every object that defines
one of the replaceable global allocation functions (operator new/new[]/delete/delete[], their sized and nothrow
forms) is copied to <out>/obj_new/ with those symbols weakened, so the strong definitions of global_new.cpp win;
<out>/global_new.txt lists what was weakened where."""
import glob
import os
import subprocess
import sys

ALLOC = {"_Znwm", "_Znam", "_ZdlPv", "_ZdaPv", "_ZdlPvm", "_ZdaPvm", "_ZnwmRKSt9nothrow_t", "_ZnamRKSt9nothrow_t",
         "_ZdlPvRKSt9nothrow_t", "_ZdaPvRKSt9nothrow_t"}


def defined_alloc(nm, objs):
    """{object path: [allocation symbols it defines strongly]}"""
    found = {}
    for i in range(0, len(objs), 256):
        r = subprocess.run([nm, "-g", "--defined-only", "-A", "--format=posix"] + objs[i:i + 256],
                           capture_output=True, text=True, check=True)
        for line in r.stdout.splitlines():
            k = line.rfind(".o: ")
            if k < 0:
                continue
            f = line[k + 4:].split()
            if len(f) >= 2 and f[0] in ALLOC and f[1] in "TDRB":
                found.setdefault(line[:k + 2], []).append(f[0])
    return found


def main():
    fixed, out, mode, nm, objcopy = sys.argv[1:6]
    objs = sorted(glob.glob(os.path.join(fixed, "*.o")))
    weak = defined_alloc(nm, objs) if mode == "runtime" else {}
    wdir = os.path.join(out, "obj_new")
    os.makedirs(wdir, exist_ok=True)
    report = []
    with open(os.path.join(out, "game_objects.list"), "w") as lst:
        for o in objs:
            if o in weak:
                dst = os.path.join(wdir, os.path.basename(o))
                args = [objcopy] + [f"--weaken-symbol={s}" for s in sorted(weak[o])] + [o, dst]
                subprocess.run(args, check=True)
                report.append(f"{os.path.basename(o)}\t{' '.join(sorted(weak[o]))}")
                o = dst
            lst.write(f'"{o}"\n')
    with open(os.path.join(out, "global_new.txt"), "w") as f:
        f.write("\n".join(report) + ("\n" if report else ""))
    print(f"global operator new/delete: {mode}" + (f", weakened in {len(report)} game objects" if mode == "runtime" else ""))


if __name__ == "__main__":
    main()
