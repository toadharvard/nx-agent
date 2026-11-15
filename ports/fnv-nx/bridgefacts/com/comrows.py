#!/usr/bin/env python3
"""comrows.py SHIM_DIR AUDIO_DIR SPEC ROWS WORDS: derives the COM spec from the interface declarations (spec_extract.py, no tracked spec) and writes it as SPEC (the output COM_SPEC); the bridge rows of the platform's COM interfaces, derived once from that spec (the one derivation of the
signature of a COM thunk: the C++ half, platform/com/gen_combridge.py, reads these files and never recomputes them).

  ROWS   rows of the bridge table (8 TAB columns, no header; the `extra` rows of contract/bridgegen.py): one per method of every non-hidden
         interface (dll `!<Interface>`, name, ordinal = slot, stdcall, ret v|i, args = the x86 stack-word letters with the `this` first,
         native symbol nxb_<Interface>_<Method>, bind sym) and one per export (dll, name, ordinal, stdcall, ret, args, nxb_<Export>, sym).
  WORDS  `Interface TAB Method TAB words` for every method of every interface, hidden ones included (this counted): the proxy width check.

The vocabulary of the spec is documented in platform/com/gen_combridge.py. A spec item without a rule stops the tool (exit 2)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spec_extract


def die(msg):
    sys.stderr.write("comrows: " + msg + "\n")
    sys.exit(2)


def words_of(p, what):
    """the x86 stack words of a parameter: list of letters"""
    k = p["kind"]
    if k in ("value", "flags", "enum", "buffer", "callback", "handle", "giface", "handle_array"):
        return ["i"]
    if k == "struct":
        if p["byref"]:
            return ["i"]
        if p["size"] % 4:
            die(f"{what}: struct {p['name']} by value of {p['size']} bytes has no rule")
        return ["i"] * (p["size"] // 4)
    if k == "float":
        return ["f"]
    if k == "double":
        return ["d"]
    if k == "int64":
        return ["q"]
    die(f"{what}: kind {k} has no rule")


def ret_letter(what, m):
    rk = m["ret_kind"]
    if rk == "void":
        return "v"
    if rk in ("hresult", "value"):
        return "i"
    die(f"{what}: return kind {rk} has no rule")


def signature(what, m, with_this):
    letters = ["i"] if with_this else []
    for p in m["params"]:
        letters += words_of(p, what)
    return "".join(letters), ret_letter(what, m)


def derive(spec):
    names = sorted(spec["interfaces"])
    by = {}
    for m in spec["methods"]:
        by.setdefault(m["iface"], []).append(m)
    rows, words = [], []
    for n in names:
        info = spec["interfaces"][n]
        ms = sorted(by.get(n, []), key=lambda m: m["slot"])
        if [m["slot"] for m in ms] != list(range(info["slots"])):
            die(f"{n}: methods do not cover slots 0..{info['slots'] - 1}")
        for m in ms:
            what = f"{n}::{m['name']}"
            letters, rl = signature(what, m, True)
            words.append((n, m["name"], str(len(letters))))
            if info["hidden"]:
                continue
            if m.get("role") in ("addref", "release"):
                letters, rl = "i", "i"
            rows.append((f"!{n}", m["name"], str(m["slot"]), "stdcall", rl, letters, f"nxb_{n}_{m['name']}", "sym"))
    for e in spec["exports"]:
        letters, rl = signature(e["name"], e, False)
        rows.append((e["dll"], e["name"], str(e["ordinal"]), "stdcall", rl, letters, f"nxb_{e['name']}", "sym"))
    return rows, words


def main():
    shim, audio, spec_path, rows_path, words_path = sys.argv[1:6]
    spec = spec_extract.extract({"shim": shim, "audio": audio})
    with open(spec_path, "w") as f:
        json.dump(spec, f, indent=1, sort_keys=True)
        f.write("\n")
    rows, words = derive(spec)
    open(rows_path, "w").write("".join("\t".join(r) + "\n" for r in rows))
    open(words_path, "w").write("".join("\t".join(w) + "\n" for w in words))


if __name__ == "__main__":
    main()
