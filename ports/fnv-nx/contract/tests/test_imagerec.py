#!/usr/bin/env python3
"""imagerec: the universe flag vocabulary is the facts' own (manifest.json `universe_flags`); one status per entry, the rest attributes."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
from fnvnx.contract import imagerec  # noqa: E402

FAILED = []


def check(name, got, want):
    ok = got == want
    print("%s %s" % ("PASS" if ok else "FAIL", name) + ("" if ok else ": got %r, want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


MAN = {"universe_flags": {"status": ["proven", "candidate"], "attr": ["ptr", "seh-handler", "reloc-misfit=", "ud="]}}
V = imagerec.vocabulary(MAN)


def uni(flags):
    try:
        return imagerec._universe_of("# base=0x1000:\n00001000\tfn\t10\t%s\n" % flags, V)[0]
    except imagerec.ImageError as e:
        return str(e)


def voc(man):
    try:
        return imagerec.vocabulary(man)
    except imagerec.ImageError as e:
        return str(e)


check("a status alone", uni("proven"), {0x1000: "proven"})
check("a status with attributes and valued attributes", uni("candidate,ptr,reloc-misfit=101"), {0x1000: "candidate"})
check("proven,ptr,seh-handler (the token set that failed the fixed pair)", uni("proven,ptr,seh-handler"), {0x1000: "proven"})
check("a token outside the vocabulary is red", "not in the vocabulary" in uni("proven,bogus"), True)
check("a valued token of an attribute that takes no value is red", "not in the vocabulary" in uni("proven,ptr=1"), True)
check("a non-decimal value is red", "not in the vocabulary" in uni("proven,ud=x"), True)
check("zero statuses is red", "0 statuses" in uni("ptr"), True)
check("two statuses is red", "2 statuses" in uni("proven,candidate"), True)
check("a duplicate token is red", "duplicate token" in uni("proven,ptr,ptr"), True)
check("a missing vocabulary field is red", "no `universe_flags`" in voc({}), True)
check("a malformed vocabulary (extra key) is red", "no `universe_flags`" in voc({"universe_flags": {"status": ["a"], "attr": [], "x": 1}}), True)
check("a vocabulary with no status is red", "no status" in voc({"universe_flags": {"status": [], "attr": ["a"]}}), True)
check("a name shared by status and attr is red", "shared" in voc({"universe_flags": {"status": ["a"], "attr": ["a"]}}), True)
print("FAILED: %d" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
