#!/usr/bin/env python3
"""Gate `forms` (fnvnx-gate/1, class 6 of VERDICT.md): an x86 instruction form without a certificate is a build error (rule R2).
    python3 -I ports/fnv-nx/lift/proof/formgate.py          cwd = checkout root, env GATE_OUT, optional GATE_CACHE; writes GATE_OUT/forms.json
ONE copy of this gate. No argument and no other environment variable is read: every private input is declared in formgate_inputs.json
(next to this file, "fnvnx-forms-inputs/1": the same `external` entries as verify/contract_inputs.json, fetched by the SAME loader,
recipe.fetch_externals, by exact commit with tree_sha256 checked, into GATE_CACHE/{store,external}; without GATE_CACHE the cache is
GATE_OUT/forms-cache). The loader is imported from ports/fnv-nx/verify (sibling of lift/) the way verify/gate_contract.py imports its own (plain imports: a checkout
without verify/ cannot run the gate, no result file, so the verdict is red).
Keys: external; shapes = `liftpack/<path>` (shapes.tsv of the lifted pack); certs = `certs/<path>` (certs.tsv); model_sha256 = the pinned model.

Universe: the ENCODING SHAPES of the lifted pack, whole: the rows `shape_key<TAB>family<TAB>count` of shapes.tsv (sorted by key, unique, family in
int x87 simd sys) inside the external `liftpack`; the generate driver of the pack writes it with proof/shape.py:shape_key. The gate has no decoder
of the exe and no census of its own. A shape is certified iff certs.tsv (rows shape_key family dsl_sha model_sha reference result) has a row with
the same shape key, the dsl_sha that the CURRENT code gives (symform.shape_sha(key): sha256 of the symbolic Form, immediate / displacement /
relative-target fields as variables; the same function the prover writes the field with; none when the shape has no symbolic Form: then the shape
is uncertified), the pinned model_sha256 and result == proved. The hash binds the certificate to the builder code (forms*.py, dsl.py, symform.py)
and to the object that was proved. The gate imports no SMT solver (the DSL imports z3 only inside to_smt / post).

Pinned model: formgate_inputs.json model_sha256 must equal the `x86_proof.ir` pin of proof/sail_pins.txt (red otherwise): one pin, two readers.

RED with the reason (item `inputs`, universe 1, certified 0), never skipped: no inputs file; not JSON; wrong format or keys; a path key naming an
external that is not declared (`inputs: external NAME not declared`); model_sha256 differing from sail_pins.txt; an external
that does not fetch or whose tree does not match its hash; shapes.tsv or certs.tsv absent from its external or malformed. Otherwise the items are the
shapes; uncertified = shapes without a certificate (first 50, sorted); detail keeps the certified/universe count per family.

TODAY (no pack with shapes.tsv is published): the committed file declares only `certs`, so the gate is RED with `inputs: external liftpack not declared`.
To make it live add ONE entry to "external" in formgate_inputs.json:
  {"name": "liftpack", "repo": "fnv-nx-build", "ref": <branch of the pack>, "commit": <40 hex>, "tree_sha256": <cas tree hash of that commit's checkout>, "at": "/ext/liftpack"}
(tree_sha256 = what recipe.fetch_externals reports for it)."""
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.dirname(HERE)
VERIFY = os.path.normpath(os.path.join(LIFT, "..", "verify"))
sys.path.insert(0, LIFT)
sys.path.insert(0, os.path.join(VERIFY, "..", "contract"))
sys.path.insert(0, VERIFY)
from proof import shape, symform  # noqa: E402
import gatefile  # noqa: E402     (ports/fnv-nx/contract)
import recipe  # noqa: E402

NAME = "forms"
INPUTS = os.path.join(HERE, "formgate_inputs.json")
PINS = os.path.join(HERE, "sail_pins.txt")
FORMAT = "fnvnx-forms-inputs/1"
EXT_KEYS = {"name", "repo", "ref", "commit", "tree_sha256", "at"}      # = recipe.EXT_KEYS (a test asserts it)
KEYS = {"format", "external", "shapes", "certs", "model_sha256"}
REFERENCES = ("sail", "fpsmt", "k", "sdm", "table")
FAMILIES = ("int", "x87", "simd", "sys")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class Red(Exception):
    """a declared input is missing or invalid: the gate is red with this reason"""


def rows(path):
    with open(path) as fh:
        for ln in fh:
            ln = ln.rstrip("\n")
            if ln and ln[0] != "#":
                yield ln.split("\t")


def model_pin(pins=PINS):
    """the `x86_proof.ir` sha256 of proof/sail_pins.txt"""
    with open(pins) as fh:
        got = [r for r in (ln.split() for ln in fh if ln[0] != "#" and ln.strip()) if r[0] == "x86_proof.ir"]
    if len(got) != 1 or len(got[0]) != 4 or not HEX64.match(got[0][3]):
        raise Red("inputs: %s has no single x86_proof.ir pin" % os.path.basename(pins))
    return got[0][3]


def load_inputs(path=INPUTS, pins=PINS):
    """-> the inputs document; raises Red naming the problem"""
    if not os.path.isfile(path):
        raise Red("inputs: no %s: the pack's shapes.tsv and certs.tsv are not declared as content-pinned externals of fnv-nx-build" % os.path.basename(path))
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except ValueError as e:
        raise Red("inputs: %s is not JSON: %s" % (os.path.basename(path), e))
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise Red("inputs: format is not %s" % FORMAT)
    if set(doc) != KEYS:
        raise Red("inputs: keys must be exactly %s" % ", ".join(sorted(KEYS)))
    exts = doc["external"]
    if not isinstance(exts, list):
        raise Red("inputs: external must be a list")
    for i, e in enumerate(exts):
        if not (isinstance(e, dict) and set(e) == EXT_KEYS and all(isinstance(v, str) for v in e.values())):
            raise Red("inputs: external[%d] needs exactly %s (strings)" % (i, ", ".join(sorted(EXT_KEYS))))
        if not (re.fullmatch("[A-Za-z0-9._-]+", e["name"]) and e["repo"] == "fnv-nx-build" and re.fullmatch("[0-9a-f]{40}", e["commit"])
                and HEX64.match(e["tree_sha256"]) and e["at"].startswith("/")):
            raise Red("inputs: external[%d] (%s): bad name, repo, commit, tree_sha256 or at" % (i, e["name"]))
    names = [e["name"] for e in exts]
    if len(set(names)) != len(names):
        raise Red("inputs: external names are not unique")
    for k in ("shapes", "certs"):
        v = doc[k]
        if not isinstance(v, str) or "/" not in v or ".." in v.split("/"):
            raise Red("inputs: %s must be NAME/path inside a declared external" % k)
        if v.split("/", 1)[0] not in names:
            raise Red("inputs: external %s not declared" % v.split("/", 1)[0])
    if not isinstance(doc["model_sha256"], str) or not HEX64.match(doc["model_sha256"]):
        raise Red("inputs: model_sha256 is not a SHA-256")
    if doc["model_sha256"] != model_pin(pins):
        raise Red("inputs: model_sha256 %s differs from the x86_proof.ir pin of sail_pins.txt" % doc["model_sha256"])
    return doc


def fetch(doc, cache):
    """-> {name: {host, tree, at}} through the loader of the trunk (recipe.fetch_externals into CACHE/store, CACHE/external)"""
    ws = os.path.join(cache, "ws")
    shutil.rmtree(ws, ignore_errors=True)
    os.makedirs(ws)
    try:
        return recipe.fetch_externals(doc["external"], os.path.join(cache, "store"), os.path.join(cache, "external"), ws)
    except recipe.BuildRed as e:
        f = e.args[0]
        raise Red("inputs: external does not fetch or match its hash: stage %s: %s" % (f["stage"], " | ".join(f["stderr"][-3:])[:300]))


def path_in(ext, v, what):
    n, rel = v.split("/", 1)
    p = os.path.join(ext[n]["host"], rel)
    if not os.path.isfile(p):
        raise Red("inputs: %s: %s is missing from external %s" % (what, rel, n))
    return p


def dsl_sha(key, md):
    """the certificate field dsl_sha of the shape under the CURRENT code: symform.shape_sha, the one function the prover (proof/shapeprove.py) writes
    the field with: sha256 of the SYMBOLIC Form (fields are variables), i.e. of the very object the proof is about. None = the shape has no symbolic
    Form (no builder, builder precondition, builder branches on a field, field decode): it stays uncertified."""
    try:
        return symform.shape_sha(key)
    except (KeyError, AssertionError, symform.SymbolicBranch, symform.FieldDecodeError, shape.ShapeError):
        return None


def load_shapes(path):
    """{shape key: family} of shapes.tsv; Red unless rows are `key family count`, sorted by key, unique, family known, and there is a row"""
    out, last = {}, None
    for r in rows(path):
        if len(r) != 3 or r[1] not in FAMILIES or not r[2].isdigit():
            raise Red("shapes.tsv: bad row %r" % "\t".join(r)[:100])
        if last is not None and r[0] <= last:
            raise Red("shapes.tsv: not sorted by key, or a key twice, at %r" % r[0][:100])
        last = r[0]
        out[r[0]] = r[1]
    if not out:
        raise Red("shapes.tsv: no shapes (a pack has entries)")
    return out


def load_certs(path, model_sha):
    """{shape: set of dsl_sha} of the rows that are proofs against the pinned model"""
    ok = {}
    for r in rows(path):
        if len(r) != 6:
            raise Red("certs.tsv: bad row %r" % "\t".join(r)[:100])
        key, _, dsl, model, ref, res = r
        if res == "proved" and model == model_sha and ref in REFERENCES:
            ok.setdefault(key, set()).add(dsl)
    return ok


def analyse(shapes_path, certs_path, model_sha):
    """-> ({shape: family}, {shape: certified}, shapes without builder)"""
    fam = load_shapes(shapes_path)
    certs = load_certs(certs_path, model_sha)
    md = shape.decoder()
    cert, nobuilder = {}, 0
    for k in sorted(fam):
        d = dsl_sha(k, md)
        nobuilder += d is None
        cert[k] = d is not None and d in certs.get(k, ())
    return fam, cert, nobuilder


def judge(cache, inputs=INPUTS, pins=PINS):
    """-> (universe, certified, uncertified ids, detail); never raises for an input problem"""
    try:
        doc = load_inputs(inputs, pins)
        ext = fetch(doc, cache)
        fam, cert, nobuilder = analyse(path_in(ext, doc["shapes"], "shapes"), path_in(ext, doc["certs"], "certs"), doc["model_sha256"])
    except Red as e:
        return 1, 0, ["inputs"], str(e)[:600]
    except (OSError, ValueError) as e:
        return 1, 0, ["inputs"], "inputs: %s" % (str(e)[:300] or type(e).__name__)
    finally:
        shutil.rmtree(os.path.join(cache, "ws"), ignore_errors=True)     # the checkouts of the externals (the CAS store keeps the content)
    unc = [k for k in sorted(cert) if not cert[k]]
    byfam = ", ".join("%s %d/%d" % (f, sum(cert[k] for k in fam if fam[k] == f), sum(1 for k in fam if fam[k] == f)) for f in FAMILIES)
    return len(cert), len(cert) - len(unc), unc, "certified/universe per family: %s; shapes without a DSL builder: %d" % (byfam, nobuilder)


def cache_dir(env):
    return env.get("GATE_CACHE") or os.path.join(env["GATE_OUT"], "forms-cache")


def main(argv):
    if argv or "GATE_OUT" not in os.environ:
        sys.stderr.write("usage: formgate.py   (no arguments; env GATE_OUT, optional GATE_CACHE; cwd = checkout root)\n")
        return 1
    cache = cache_dir(os.environ)
    os.makedirs(cache, exist_ok=True)
    u, c, ids, detail = judge(cache)
    gatefile.write(NAME, u, c, ids, detail)
    st = "green" if u == c else "red"
    sys.stdout.write("%s: %s %d/%d %s\n" % (NAME, st, c, u, detail[:200]))
    return 0 if st == "green" else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
