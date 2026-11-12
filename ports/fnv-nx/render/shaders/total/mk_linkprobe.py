#!/usr/bin/env python3
"""mk_linkprobe.py: generates the input of the Wine 9.0 GLSL link probe (tests/l6/linkprobe) from the SM1-3 language definition, by rule, with no per-row edits.
usage: mk_linkprobe.py --mojoshader-header mojoshader_internal.h --out DIR      (deterministic: the same inputs give the same bytes)
ROWS: (a) every universe row: sm_universe.rows() (the 7553 rows of sm_total.tsv, each = one instruction form of sm_table.json with the operands the row names and
 the shortest well-formed completion, built by sm_emit.finish: version token, declarations, a vs writes oPos, a ps writes oC0);
 (b) every row of sm_illegal.tsv: its program text is the census case (render/total/census/sm_cases.py cases(), the same (model, kind, feature) key; the form the
 documented validator rule names). A row id is "model|kind|feature".
ENCODING: sm_hand.assemble (the token encoder the translator gate uses) -> D3D9 shader bytecode. PARTNER: the minimal shader of the other stage, same major version
 (a stage is not paired across major versions): PARTNER below; a vs row is drawn with a ps partner and a ps row with a vs partner.
OUT/probe_in.tsv:   n <TAB> v|p (stage of the row's shader) <TAB> hex(shader) <TAB> hex(partner)       one line per DISTINCT (stage, shader, partner)
OUT/rows.tsv:       row id <TAB> set (universe|illegal) <TAB> model <TAB> n (line of probe_in.tsv) <TAB> sha256 of the shader bytes
OUT/not_expressible.tsv: row id <TAB> reason     rows with no program text (illegal-list rows without a census case) or that the encoder rejects."""
import argparse, hashlib, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.dont_write_bytecode = True
sys.path[:0] = [HERE, os.path.join(HERE, "..", "..", "total", "census")]
import sm_hand, sm_universe

# minimal complete shader of each stage by major version (what a pairing needs: a vs writes oPos, a ps writes a colour)
PARTNER = {
    ("v", 1): "vs_1_1\ndcl_position v0\nmov oPos, v0\n",
    ("v", 2): "vs_2_0\ndcl_position v0\nmov oPos, v0\n",
    ("v", 3): "vs_3_0\ndcl_position v0\ndcl_position o0\nmov o0, v0\n",
    ("p", 1): "ps_1_1\nmov r0, c0\n",
    ("p", 2): "ps_2_0\nmov oC0, c0\n",
    ("p", 3): "ps_3_0\nmov oC0, c0\n",
}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--mojoshader-header", required=True); ap.add_argument("--out", required=True); a = ap.parse_args()
    ops = sm_hand.load_opcodes(a.mojoshader_header); os.makedirs(a.out, exist_ok=True)
    import sm_cases
    cases = {(m, k, f): t for m, k, f, t, lg in sm_cases.cases()}
    rows = [("universe", r["model"], r["kind"], r["feature"], r["asm"]) for r in sm_universe.rows()]
    with open(os.path.join(HERE, "sm_illegal.tsv"), encoding="utf-8") as fh:
        for ln in fh.read().splitlines()[1:]:
            m, k, f = ln.split("\t")[:3]
            rows.append(("illegal", m, k, f, cases.get((m, k, f))))
    partner = {}
    for key, txt in PARTNER.items(): partner[key] = sm_hand.assemble(txt, ops).hex()
    lines, index, rl, ne = [], {}, [], []
    for st, m, k, f, text in rows:
        rid = "%s|%s|%s" % (m, k, f)
        if text is None: ne.append((rid, "no program text: sm_illegal.tsv lists the form but the census has no case of that (model, kind, feature)")); continue
        try: bc = sm_hand.assemble(text, ops)
        except Exception as e: ne.append((rid, "encoder: " + str(e).replace("\t", " ")[:160])); continue
        stage, major = m[0], int(m[3])
        key = (stage, bc.hex(), partner[("p" if stage == "v" else "v", major)])
        if key not in index: index[key] = len(lines); lines.append("%d\t%s\t%s\t%s" % (len(lines), stage, key[1], key[2]))
        rl.append("%s\t%s\t%s\t%d\t%s" % (rid, st, m, index[key], hashlib.sha256(bc).hexdigest()))
    w = lambda n, L: open(os.path.join(a.out, n), "w", newline="\n").write("\n".join(L) + "\n")
    w("probe_in.tsv", lines); w("rows.tsv", ["row\tset\tmodel\tn\tsha256"] + rl); w("not_expressible.tsv", ["row\treason"] + ["%s\t%s" % x for x in ne])
    print("mk_linkprobe: %d rows (%d universe + %d illegal), %d distinct programs, %d not expressible" % (len(rows), sum(r[0] == "universe" for r in rows), sum(r[0] == "illegal" for r in rows), len(lines), len(ne)))


if __name__ == "__main__":
    main()
