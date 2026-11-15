#!/usr/bin/env python3
"""Gate `forms` (fnvnx-gate/1, class 6 of VERDICT.md): an x86 instruction form without a certificate is a build error (rule R2).
    python3 -I ports/fnv-nx/lift/proof/formgate.py          cwd = checkout root, env GATE_OUT, optional GATE_CACHE; writes GATE_OUT/forms.json
ONE copy of this gate. No argument and no other environment variable is read: every private input is declared in formgate_inputs.json
(next to this file, "fnvnx-forms-inputs/1": the same `external` entries as verify/contract_inputs.json, fetched by the SAME loader,
recipe.fetch_externals, by exact commit with tree_sha256 checked, into GATE_CACHE/{store,external}; without GATE_CACHE the cache is
GATE_OUT/forms-cache). The loader is imported from ports/fnv-nx/verify (sibling of lift/) the way verify/gate_contract.py imports its own (plain imports: a checkout
without verify/ cannot run the gate, no result file, so the verdict is red).
Keys: external; shapes = `liftpack/<path>` (shapes.tsv of the lifted pack); certs = `certs/<path>` (certs.tsv); instrs = `liftpack/<path>` (instrs.tsv of the pack: the instance set of the link check, proof/symcheck.py); the external `x86ir` pins the model (x86_proof.ir); its sha256 is derived from that file.

Universe: the ENCODING SHAPES of the lifted pack, whole: the rows `shape_key<TAB>family<TAB>count` of shapes.tsv (sorted by key, unique, family in
int x87 simd sys) inside the external `liftpack`; the generate driver of the pack writes it with proof/shape.py:shape_key. The gate has no decoder
of the exe and no census of its own. A shape is certified iff certs.tsv (rows shape_key family dsl_sha model_sha reference result obligations undef_flags def0_witness def0_free nonflag_free) has a row with
the same shape key, the dsl_sha that the CURRENT code gives (symform.shape_sha(key): sha256 of the symbolic Form, immediate / displacement /
relative-target fields as variables; the same function the prover writes the field with; none when the shape has no symbolic Form: then the shape
is uncertified), the sha256 of the pinned x86_proof.ir (derived from the external) and result == proved. The hash binds the certificate to the builder code (forms*.py, dsl.py, symform.py)
and to the object that was proved. The gate imports no SMT solver (the DSL imports z3 only inside to_smt / post).

Pinned model: the external `x86ir` is the one place x86_proof.ir is pinned (commit + tree_sha256); every reader derives the sha256 from its file.

RED with the reason (item `inputs`, universe 1, certified 0), never skipped: no inputs file; not JSON; wrong format or keys; a path key naming an
external that is not declared (`inputs: external NAME not declared`); a missing `x86ir` external; an external
that does not fetch or whose tree does not match its hash; shapes.tsv or certs.tsv absent from its external or malformed. Otherwise the items are the
shapes; uncertified = shapes without a certificate (first 50, sorted); detail keeps the certified/universe count per family.

TODAY (no pack with shapes.tsv is published): the committed file declares only `certs`, so the gate is RED with `inputs: external liftpack not declared`.
To make it live add ONE entry to "external" in formgate_inputs.json:
  {"name": "liftpack", "repo": "fnv-nx-build", "ref": <branch of the pack>, "commit": <40 hex>, "tree_sha256": <cas tree hash of that commit's checkout>, "at": "/ext/liftpack"}
(tree_sha256 = what recipe.fetch_externals reports for it)."""
import hashlib
import hashlib
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
from proof import codeuniverse, eaaudit, shape, symcheck, symform  # noqa: E402
import gatefile  # noqa: E402     (ports/fnv-nx/contract)
import recipe  # noqa: E402

NAME = "forms"
INPUTS = os.path.join(HERE, "formgate_inputs.json")
FORMAT = "fnvnx-forms-inputs/1"
EXT_KEYS = {"name", "repo", "ref", "commit", "tree_sha256", "at"}      # = recipe.EXT_KEYS (a test asserts it)
KEYS = {"format", "external", "shapes", "certs", "instrs"}
IR_EXT = "x86ir"      # the external that pins x86_proof.ir: the one place the model is pinned; its sha256 is derived from the file
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


def load_inputs(path=INPUTS):
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
    for k in ("shapes", "certs", "instrs"):
        v = doc[k]
        if not isinstance(v, str) or "/" not in v or ".." in v.split("/"):
            raise Red("inputs: %s must be NAME/path inside a declared external" % k)
        if v.split("/", 1)[0] not in names:
            raise Red("inputs: external %s not declared" % v.split("/", 1)[0])
    if IR_EXT not in names:
        raise Red("inputs: external %s (the pinned x86_proof.ir) not declared" % IR_EXT)
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


def file_sha(p):
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


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


OBLIGATIONS = ("def0", "xstate")      # = prove.OBLIGATIONS (test_formgate checks it; this module imports no SMT solver): a row certifies only what the current prover checks


def declared_table():
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "declared.json")) as fh:
        return json.load(fh)


def platform_sha(fns, root=None):
    """= prove.platform_sha (test_formgate checks they agree; this module imports no SMT solver): sha256 over (relative path, content) of the declared.json sources of the functions, hashed from the lift tree this gate runs from"""
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rows = {u: r for r in declared_table()["rows"] for u in r["ufs"]}
    h = hashlib.sha256()
    for src in sorted({rows[f]["source"] for f in fns}):
        h.update(src.encode()); h.update(open(os.path.join(root, src), "rb").read())
    return h.hexdigest()


def declared_ok(decl, root=None):
    """`reg:fn,...@sha256`: every pair is allowed by declared.json (function, output), and the sha is that of the platform sources in this tree"""
    try:
        pairs, sha = decl.rsplit("@", 1)
        rows = {u: r for r in declared_table()["rows"] for u in r["ufs"]}
        fns = []
        for p in pairs.split(","):
            reg, fn = p.split(":")
            if fn not in rows or reg not in rows[fn]["outputs"]: return False
            fns.append(fn)
        return sha == platform_sha(fns, root)
    except (ValueError, KeyError, OSError):
        return False



def load_certs(path, model_sha):
    """{shape: set of dsl_sha} of the rows that are proofs against the pinned model"""
    ok = {}
    for r in rows(path):
        if len(r) != 12:
            raise Red("certs.tsv: bad row %r" % "\t".join(r)[:100])
        key, _, dsl, model, ref, res, obl, decl = r[:7] + r[11:]
        # `proved`: every output equals the model's.  `agree-except-declared`: every output equals the model's EXCEPT the registers listed in the declared column
        # (reg:function pairs, @platform sha256), which the Form takes from a platform function (cpuid: rt/cpumodel.c); a proved row declares nothing
        if res not in ("proved", "agree-except-declared") or (res == "proved") != (decl == "-"):
            continue
        if decl != "-" and not declared_ok(decl):
            continue                                                # a declared output outside declared.json, or a platform source that is not the one of this tree: stale
        if model == model_sha and ref in REFERENCES and obl == ",".join(OBLIGATIONS):
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


def judge(cache, inputs=INPUTS):
    """-> (universe, certified, uncertified ids, detail); never raises for an input problem"""
    try:
        doc = load_inputs(inputs)
        ext = fetch(doc, cache)
        fam, cert, nobuilder = analyse(path_in(ext, doc["shapes"], "shapes"), path_in(ext, doc["certs"], "certs"), file_sha(path_in(ext, IR_EXT + "/ir/x86_proof.ir", "x86_proof.ir")))
    except Red as e:
        return 1, 0, ["inputs"], str(e)[:600]
    except (OSError, ValueError) as e:
        return 1, 0, ["inputs"], "inputs: %s" % (str(e)[:300] or type(e).__name__)
    finally:
        shutil.rmtree(os.path.join(cache, "ws"), ignore_errors=True)     # the checkouts of the externals (the CAS store keeps the content)
    unc = [k for k in sorted(cert) if not cert[k]]
    try:      # the link check (symcheck.py, ASM-006): every instance of the pack's instrs.tsv builds the Form that its shape's proved symbolic Form instantiates to; MISMATCH, unlinked and seglim are red items
        lc, lred = symcheck.link(codeuniverse.load_instrs(path_in(ext, doc["instrs"], "instrs")))
    except (Red, codeuniverse.Bad) as e:
        return 1, 0, ["inputs"], str(e)[:600]
    ninst = lc["instances"] - lc["no-form"]      # the instances that are Form instances (a no-form instance has neither builder: nothing to link)
    link_ids = ["link:%s:%s:%x" % (c, im, va) for c, im, va, _ in lred]
    ea = eaaudit.violations(HERE)      # one uncertified item per violation of the ea-rule: every Form memory access goes through an address function (proof/eaaudit.py)
    ea_ids = ["ea-rule:%s:%d" % v[:2] for v in ea]
    byfam = ", ".join("%s %d/%d" % (f, sum(cert[k] for k in fam if fam[k] == f), sum(1 for k in fam if fam[k] == f)) for f in FAMILIES)
    return (len(cert) + len(ea_ids) + ninst, len(cert) - len(unc) + ninst - len(link_ids), unc + ea_ids + link_ids,
            "certified/universe per family: %s; shapes without a DSL builder: %d; ea-rule violations: %d; link check over %d instances: %s%s" % (byfam, nobuilder, len(ea), lc["instances"], dict(lc), "".join("; %s:%d %s" % v for v in ea[:5])))


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
