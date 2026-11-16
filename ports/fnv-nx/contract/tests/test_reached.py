#!/usr/bin/env python3
"""reached.py on the synthetic PE32 image of test_gpa_names: one IAT import, one resolved GetProcAddress name and one export nothing reaches. Pure (no game data)."""
import contextlib
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): contract.X by its qualified name
with contextlib.redirect_stdout(io.StringIO()):
    from fnvnx.contract.tests import test_gpa_names as T   # builds the toy image and runs its own checks
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
from fnvnx.contract import gpa_names, reached

fails = []


def check(name, got, want):
    if got != want:
        fails.append("%s: got %r, want %r" % (name, got, want))


SPECS = {"kernel32": "@ stdcall LoadLibraryA(str)\n@ stdcall GetProcAddress(long str)\n@ stdcall Unreached(long)\n"}


def spec_text(dll):
    if dll not in SPECS:
        raise FileNotFoundError(dll)
    return SPECS[dll]


imports = {"syn.exe": reached.read_imports("slot\tdll\tname\tkind\tvalue\n0x402000\tkernel32.dll\tLoadLibraryA\tkey\t0\n0x402004\tKERNEL32\tGetProcAddress\tkey\t0\n")}
sig, errs = reached.signatures([i for v in imports.values() for i in v], spec_text)
check("signatures: no error", errs, [])
check("signatures: stack bytes from the spec alone", sorted(gpa_names.import_stacks(sig).items()),
      [(("kernel32.dll", "GetProcAddress"), 8), (("kernel32.dll", "LoadLibraryA"), 4)])
check("signatures: an unreached export is no row", "Unreached" in sig, False)
check("signatures: no row binds a symbol", {l.split("\t")[7] for l in sig.splitlines()[1:]}, {"none"})
_, errs = reached.signatures([("kernel32.dll", "Missing", "0x1")], spec_text)
check("signatures: a name the spec lacks is an error, listed", len(errs), 1)

T.img.import_stack = gpa_names.import_stacks(sig)
site = T.site_of("f_const", 0)
res = gpa_names.run([("syn.exe", site, "GetProcAddress", "dynamic")], {"syn.exe": T.img})
rows = reached.rows_of(imports, res)
check("rows", rows, [("kernel32.dll", "GetProcAddress", "iat", "syn.exe@0x402004"),
                     ("kernel32.dll", "LoadLibraryA", "iat", "syn.exe@0x402000"),
                     ("mod.dll", "Foo", "gpa", "syn.exe@0x%x" % site)])
check("tsv header", reached.tsv(rows).splitlines()[0], "dll\tname\thow\tsite")
check("the unreached export is not a row", any(r[1] == "Unreached" for r in rows), False)
if fails:
    print("FAIL\n" + "\n".join(fails))
    sys.exit(1)
print("ok test_reached")
