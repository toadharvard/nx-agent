#!/usr/bin/env python3
"""Stage `closure` of the lift/proof/shapeprove root: the prover's identity as a directory.

    python3 -I mkclosure.py PROOF_DIR FNVLIFT_DIR VERIFY_DIR CONTRACT_DIR LINK_DIR OUT_DIR

PROOF_DIR / FNVLIFT_DIR / VERIFY_DIR are the mounted lift/proof, lift/fnvlift, verify, contract and link (the recipe loader and the directories it imports from, which certbasis.component_inputs reads the component roots through).  OUT_DIR/{proof,fnvlift} receive exactly certbasis.prover_files() and certbasis.prover_data_files() (the import closure of the prover
and the data files of its packages, derived by certbasis, never listed here).  OUT_DIR/proof/PROVER_SHA (the closure marker) is certbasis.prover_sha()
of the source tree; the step is red unless the copy has the same sha, so every later step that mounts OUT_DIR computes the identical identity.  Later steps read this directory by content: an edit of a file
outside the closure (a test, a tool, a document) changes this step's key but not its output, so no proof is re-run."""
import os
import shutil
import sys


def main(argv):
    proof, fnvlift, verify, contract, link, out = [os.path.abspath(a) for a in argv]
    tree = "/scratch/repo/ports/fnv-nx"      # the checkout layout certbasis reads: lift/{proof,fnvlift} and verify
    lift = tree + "/lift"
    os.makedirs(lift)
    os.symlink(proof, os.path.join(lift, "proof")); os.symlink(fnvlift, os.path.join(lift, "fnvlift")); os.symlink(verify, os.path.join(tree, "verify")); os.symlink(contract, os.path.join(tree, "contract")); os.symlink(link, os.path.join(tree, "link"))
    sys.path.insert(0, lift)
    from proof import certbasis
    sha = certbasis.prover_sha()
    files = certbasis.prover_files() + certbasis.prover_data_files()
    for f in files:
        dst = os.path.join(out, f)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(os.path.join(lift, f), dst)
    sys.path.remove(lift)
    for m in [m for m in sys.modules if m.split(".")[0] in ("proof", "fnvlift")]: del sys.modules[m]
    sys.path.insert(0, out)
    open(os.path.join(out, "proof", "PROVER_SHA"), "w").close()      # certbasis.CLOSURE_MARK: this directory is a closure, there is no checkout around it
    from proof import certbasis as copy
    if copy.prover_sha() != sha:
        sys.exit("closure: the copy's prover_sha %s differs from the tree's %s" % (copy.prover_sha(), sha))
    with open(os.path.join(out, "proof", "PROVER_SHA"), "w") as fh: fh.write(sha + "\n")
    print("closure: %d files, prover_sha %s" % (len(files), sha))
    return 0


if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
