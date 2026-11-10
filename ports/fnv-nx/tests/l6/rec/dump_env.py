#!/usr/bin/env python3
"""dump_env.py STREAM.d9s [OUT.tsv]: every `env`-class answer an application received, as TSV (read from a D9S1 stream).
Columns: seq, method, role (arg|ret|out), item, field, value[, in_value: the application's value when the runtime rewrote it].
A record is listed when any of its items is env (ret/out); then its args are listed too (they are what the answer is for).
Struct values are split into fields by the spec; 4-byte values print as 0x%08x, char arrays as text, other as hex."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(HERE, "..", "wrap")); import d9s1_read as R  # noqa: E402
SPEC = os.path.join(HERE, "..", "..", "..", "render", "d3d9", "spec", "d3d9_spec.json")


def fmt(v, kind="int"):
    if isinstance(v, int): return "0x%08x" % v
    if v is None: return ""
    if kind == "char": return v.split(b"\0")[0].decode("latin-1")
    return ("0x%08x" % int.from_bytes(v, "little")) if len(v) <= 4 else v.hex()


def fields(spec, sname, data, pos=0, pre=""):
    """[(name, kind, bytes)] of a struct payload as laid out in STREAM.md; returns (list, new pos)."""
    out = []
    for f in spec["structs"][sname]["fields"]:
        k, n = f["kind"], f["size"]
        if k in ("ptr", "ohandle"): out.append((pre + f["name"], k, data[pos:pos + 4])); pos += 4
        elif k == "pad": pos += n
        elif k == "struct":
            for i in range(f.get("count", 1)):
                sub, pos = fields(spec, f["struct"], data, pos, pre + f["name"] + (f"[{i}]" if f.get("count", 1) > 1 else "") + ".")
                out += sub
        else: out.append((pre + f["name"], k, data[pos:pos + n])); pos += n
    return out, pos


def rows(spec, rec):
    """list of (role, item, field, value-text, in-text|None) of a record, or [] when it has no env result."""
    pm = {p["name"]: p for p in rec.m["params"]}; ins = {}; res = []
    env = any(c == 1 and r for (_, c, r, _) in rec.items)
    if not env: return res
    def put(role, label, v, p, inv=None):
        if p is not None and p["kind"] == "struct" and isinstance(v, (bytes, bytearray)):
            fi, _ = fields(spec, p["struct"], v); old = dict((a, c) for a, b, c in fields(spec, p["struct"], inv)[0]) if inv else {}
            for a, b, c in fi: res.append((role, label, a, fmt(c, b), fmt(old[a], b) if a in old and old[a] != c else None))
        else: res.append((role, label, "", fmt(v), None))
    for lab, c, r, v in rec.items:
        if lab.startswith("in:") and not lab.endswith((":present", ":when")): ins[lab[3:]] = v
    for lab, c, r, v in rec.items:
        if lab.startswith("in:") and not lab.endswith((":present", ":when")):
            p = pm.get(lab[3:])
            if p is not None and p["dir"] == "inout": continue   # printed with its out item
            put("arg", lab[3:], v, p)
        elif lab == "ret" and c == 1: put("ret", "ret", v, None)
        elif lab.startswith("out:") and c == 1 and r: put("out", lab[4:], v, pm.get(lab[4:]), ins.get(lab[4:]) if pm.get(lab[4:], {}).get("dir") == "inout" else None)
    return res


def dump(data, spec, out):
    hdr, recs = R.read_stream(data, spec); n = 0
    out.write("seq\tmethod\trole\titem\tfield\tvalue\tin_value\n")
    for rec in recs:
        for role, item, fld, val, old in rows(spec, rec):
            out.write(f"{rec.seq}\t{rec.iface or 'export'}::{rec.name}\t{role}\t{item}\t{fld}\t{val}\t{old or ''}\n"); n += 1
    return n


if __name__ == "__main__":
    import json
    if len(sys.argv) < 2: sys.exit(__doc__)
    spec = json.load(open(SPEC)); o = open(sys.argv[2], "w") if len(sys.argv) > 2 else sys.stdout
    n = dump(open(sys.argv[1], "rb").read(), spec, o); print(f"dump_env: {n} rows", file=sys.stderr)
