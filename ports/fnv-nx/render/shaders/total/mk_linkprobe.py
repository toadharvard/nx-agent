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
OUT/not_expressible.tsv: row id <TAB> reason     rows with no program text (illegal-list rows without a census case) or that the encoder rejects.
EVERY item exists twice: with the minimal partner of the other stage (same major) and with the FIXED-FUNCTION partner ("-": no shader of that stage), so that the
 fixed-function vertex / pixel programs of wined3d are members of the domain; item "ffp|ffp|ffp" is the fixed-function pair. rows.tsv: row, set, model, n (minimal partner), n_ffp, sha256.
--sdp DIR --private-out DIR2 (optional): also every shader of the shipped shader packages (.sdp, render/shaders/SDP.md; the owner's game data: its bytecode never enters this
 repository) -> DIR2/probe_in_shipped.tsv + DIR2/rows_shipped.tsv (row id "shipped|package|entry"); item numbers continue after probe_in.tsv's."""
import argparse, glob, hashlib, os, struct, sys
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
    ap = argparse.ArgumentParser(); ap.add_argument("--mojoshader-header", required=True); ap.add_argument("--out", required=True); ap.add_argument("--sdp"); ap.add_argument("--private-out"); a = ap.parse_args()
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
    def item(stage, shader_hex, partner_hex):
        key = (stage, shader_hex, partner_hex)
        if key not in index: index[key] = len(lines); lines.append("%d\t%s\t%s\t%s" % (len(lines), stage, shader_hex, partner_hex))
        return index[key]
    item("f", "-", "-")                                    # the fixed-function pair
    for st, m, k, f, text in rows:
        rid = "%s|%s|%s" % (m, k, f)
        if text is None: ne.append((rid, "no program text: sm_illegal.tsv lists the form but the census has no case of that (model, kind, feature)")); continue
        try: bc = sm_hand.assemble(text, ops)
        except Exception as e: ne.append((rid, "encoder: " + str(e).replace("\t", " ")[:160])); continue
        stage, major = m[0], int(m[3])
        rl.append("%s\t%s\t%s\t%d\t%d\t%s" % (rid, st, m, item(stage, bc.hex(), partner[("p" if stage == "v" else "v", major)]), item(stage, bc.hex(), "-"), hashlib.sha256(bc).hexdigest()))
    n_public = len(lines); pub = list(lines); priv_lines, priv_rows = [], []
    if a.sdp:
        for f in sorted(glob.glob(os.path.join(a.sdp, "*.sdp"))):
            d = open(f, "rb").read(); magic, count, size = struct.unpack("<III", d[:12]); p = 12
            for _ in range(count):
                name = d[p:p + 256].split(b"\0")[0].decode(); sz = struct.unpack("<I", d[p + 256:p + 260])[0]; code = d[p + 260:p + 260 + sz]; p += 260 + sz
                w0 = struct.unpack("<I", code[:4])[0]; stage = "v" if (w0 >> 16) == 0xFFFE else "p"; major = (w0 >> 8) & 0xFF
                n1 = item(stage, code.hex(), partner[("p" if stage == "v" else "v", major)]); n2 = item(stage, code.hex(), "-")
                priv_rows.append("shipped|%s|%s\t%s\t%s_%d_%d\t%d\t%d\t%s" % (os.path.basename(f), name, "shipped", "vs" if stage == "v" else "ps", major, w0 & 0xFF, n1, n2, hashlib.sha256(code).hexdigest()))
            if p != len(d): sys.exit("%s: trailing bytes" % f)
        priv_lines = lines[n_public:]; lines = lines[:n_public]
        # items shared with a public row keep the public number (the index above); only new ones are private
    w = lambda n, L: open(os.path.join(a.out, n), "w", newline="\n").write("\n".join(L) + "\n")
    w("probe_in.tsv", lines); w("rows.tsv", ["row\tset\tmodel\tn\tn_ffp\tsha256"] + rl); w("not_expressible.tsv", ["row\treason"] + ["%s\t%s" % x for x in ne])
    if a.sdp:
        os.makedirs(a.private_out, exist_ok=True); wp = lambda n, L: open(os.path.join(a.private_out, n), "w", newline="\n").write("\n".join(L) + "\n")
        wp("probe_in_shipped.tsv", priv_lines); wp("rows_shipped.tsv", ["row\tset\tmodel\tn\tn_ffp\tsha256"] + priv_rows)
    print("mk_linkprobe: %d rows (%d universe + %d illegal), %d items (%d public, %d shipped-only), %d shipped shaders, %d not expressible" % (len(rows), sum(r[0] == "universe" for r in rows), sum(r[0] == "illegal" for r in rows), len(lines) + len(priv_lines), len(lines), len(priv_lines), len(priv_rows), len(ne)))


if __name__ == "__main__":
    main()
