#!/usr/bin/env python3
"""tests/nx1: check 7's s4.vm_blocks on mutated copies of the port (V1 VM-1..VM-3).
The extent bound is FNVNX_VM_EXTENTS as C evaluates it (decimal, hex, suffixes, expressions over other defines);
one that cannot be evaluated fails; system_resource_size lifts the bound only in a 39-bit (64Bit39) space, never in
the game's 32bit_no_reserved one."""
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import check_platform  # noqa: E402


def vm_blocks(t, tag, extents=None, npdm=None, extra=""):
    root = os.path.join(t, tag)
    for r in ("toolchain", "platform/audio"):
        shutil.copytree(os.path.join(PORT, r), os.path.join(root, r), ignore=shutil.ignore_patterns("__pycache__"))
    p = os.path.join(root, "toolchain", "host", "host_vm.inc")
    s = open(p).read()
    if extents is not None:
        s2 = re.sub(r'#define FNVNX_VM_EXTENTS [^\n]*', extra + '#define FNVNX_VM_EXTENTS ' + extents, s)
        assert re.search(r"#define FNVNX_VM_EXTENTS ", s)
        open(p, "w").write(s2)
    if npdm:
        q = os.path.join(root, "toolchain", "npdm", "fnvnx-app.json")
        j = json.load(open(q))
        j.update(npdm)
        json.dump(j, open(q, "w"))
    old = check_platform.ROOT
    check_platform.ROOT = root
    try:
        return {cid: good for cid, good, _ in check_platform.check(False).rows}.get("s4.vm_blocks")
    finally:
        check_platform.ROOT = old


def main():
    bad = 0
    with tempfile.TemporaryDirectory() as t:
        cases = [
            ("the port as it is", vm_blocks(t, "a"), True),
            ("3072 (decimal)", vm_blocks(t, "b", "3072"), True),
            ("8192 (decimal)", vm_blocks(t, "c", "8192"), False),
            ("0x2000 = 8192 (VM-2)", vm_blocks(t, "d", "0x2000"), False),
            ("0xC00u = 3072", vm_blocks(t, "e", "0xC00u"), True),
            ("(4 * 1024) = 4096 > 4000 extents", vm_blocks(t, "f", "(4 * 1024)"), False),
            ("an expression over another define = 8192", vm_blocks(t, "g", "(FNVNX_X * 2)", None,
                                                                    "#define FNVNX_X 4096\n"), False),
            ("a value that cannot be evaluated", vm_blocks(t, "h", "some_function()"), False),
            ("8192 + system_resource_size in 32bit_no_reserved (VM-3)",
             vm_blocks(t, "i", "8192", {"system_resource_size": "0x1000000", "address_space_type": "32bit_no_reserved"}),
             False),
            ("8192 + system_resource_size in 64bit (39-bit: its own slab)",
             vm_blocks(t, "j", "8192", {"system_resource_size": "0x1000000", "address_space_type": "64bit"}), True),
            ("8192 + system_resource_size in 64bit_old (36-bit)",
             vm_blocks(t, "k", "8192", {"system_resource_size": "0x1000000", "address_space_type": "64bit_old"}), False),
        ]
    for what, got, want in cases:
        ok = got is want
        bad += not ok
        print(("ok   " if ok else "FAIL ") + f"{what}: s4.vm_blocks {got}, want {want}")
    print(f"test_check_platform: {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
