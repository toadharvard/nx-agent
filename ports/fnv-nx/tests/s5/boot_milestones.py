#!/usr/bin/env python3
"""R0's boot milestones file (link/<commit12>/boot/milestones.json, the format agreed in J-0131) from S5's boot
summaries (tests/s5/emu_guest.py --summary-json).

    python3 -I tests/s5/boot_milestones.py <out.json> <raw boot summary.json> [<stub boot summary.json>]
        [--image "player exe"|synthesized|none]

Top level: the raw boot (no fault stubs: what the guest does by itself), one boolean per milestone of
tests/s5/milestones.txt with '-' as '_' (winmain, renderer_init, main_menu, first_frame, first_cell), the last one
reached (milestone), boot_peak_heap_bytes and the stop. "stub": the same for the stub-fault boot
(tests/s5/known_faults.txt), which shows how far the boot gets past the known faults; a gate must not count it as
the guest's own progress. A missing summary gives null values, never false ones.
"""
import argparse
import json
import sys

KEYS = ("winmain", "ini-read", "renderer-init", "archive-list", "bsa-opened", "esm-header", "masters-loaded",
        "main-menu", "first-frame", "first-cell")


def flat(summary):
    if summary is None:
        out = {k.replace("-", "_"): None for k in KEYS}
        out.update(milestone=None, boot_peak_heap_bytes=None, stop=None)
        return out
    got = {m["name"]: bool(m.get("reached")) for m in summary.get("milestones", [])}
    out = {k.replace("-", "_"): got.get(k) for k in KEYS}
    for k, v in got.items():   # milestones a later table adds
        out.setdefault(k.replace("-", "_"), v)
    st = summary.get("stop") or {}
    out.update(milestone=summary.get("milestone"), boot_peak_heap_bytes=summary.get("peak_heap_bytes"),
               stop=" / ".join([st.get("reason") or "?"] + (st.get("messages") or [])[:3]))
    return out


def load(path):
    if not path:
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out")
    ap.add_argument("raw")
    ap.add_argument("stub", nargs="?")
    ap.add_argument("--image", default=None)
    a = ap.parse_args(argv)
    doc = {"schema": "fnvnx-boot-milestones/1", "image": a.image}
    doc.update(flat(load(a.raw)))
    if a.stub:
        doc["stub"] = flat(load(a.stub))
    with open(a.out, "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
