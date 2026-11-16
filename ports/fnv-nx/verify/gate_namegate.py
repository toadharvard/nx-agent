#!/usr/bin/env python3
"""Gate "namegate" (fnvnx-gate/1, VERDICT.md class 8): a native body that stands for an exe function carries that function's MSVC name, certified
against the cl object of the same source (link/gate_names.py rows(): the plugin's name is a definition of the unit's cl object).
    python3 -I gate_namegate.py        cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/namegate.json
Universe: the rows of gate_names.rows() whose symbol bodysel selects as the NATIVE body of an exe function, in each admitted product root with
"target": "guest", "cxx_origin": "native" and a `game` directory (the join and its inputs: gate_l2native.py). A root without `game`: universe 0 for it;
no such root: universe 0, green. An uncertified row whose symbol bodysel turns LIFTED is outside the universe, not red.
Certificate of a row: gate_names.rows() gives it an empty reason against the cl reference objects of the `msvc` external.
Red iff a row of the universe is uncertified, or the root needs the bodysel verdict or the cl reference and they are missing (red item `<root>:inputs`
naming the input; owner L1 for bodysel, L2 for the cl side), or the root's build is red."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_l2native as L  # noqa: E402
import gate_names  # noqa: E402


def judge_root(repo, cache, root):
    return L.judge_native_root("namegate", repo, cache, root, gate_names.rows,
                               lambda rows, native: L.select(rows, lambda r: r[0], lambda r: r[1], native),
                               lambda r: not r[3], gate_names.row_id)


def main():
    return L.run_gate("namegate", judge_root)


if __name__ == "__main__":
    sys.exit(main())
