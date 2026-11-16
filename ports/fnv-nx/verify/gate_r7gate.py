#!/usr/bin/env python3
"""Gate "r7" (fnvnx-gate/1, VERDICT.md class 8): every type that a native exe-function body uses has the exe's layout, certified by link/gate_r7.py rows()
against the cl /Z7 CodeView types of the same source.
    python3 -I gate_r7gate.py        cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/r7.json
Universe: the rows of gate_r7.rows() of the NATIVE-BEARING units (a unit with at least one symbol that bodysel selects as the native body of an exe function;
the types such a body uses are among the unit's complete records), in each admitted product root with "target": "guest", "cxx_origin": "native" and a `game`
directory (gate_l2native.py). A root without `game`: universe 0 for it; no such root: universe 0, green.
Certificate of a row: its verdict is `equal` (size, members with offsets and sizes, bases, vfptr equal on both sides). Red iff a row of the universe is
differs, undecided, only-gcc or error, or the bodysel verdict or the cl reference is missing (item `<root>:inputs` naming it; owner L1 for bodysel, L2 for
the cl side), or the root's build is red."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_l2native as L  # noqa: E402
import gate_r7  # noqa: E402


def judge_root(repo, cache, root):
    return L.judge_native_root("r7", repo, cache, root, gate_r7.rows, lambda rows, native: L.select_units(rows, lambda r: r[0], native),
                               lambda r: r[2] == "equal", gate_r7.row_id)


def main():
    return L.run_gate("r7", judge_root)


if __name__ == "__main__":
    sys.exit(main())
