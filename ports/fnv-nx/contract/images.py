#!/usr/bin/env python3
"""images - the guest image set of the contract gates (L1 CONTRACT): one pure function, no hand list.

RULE: the set is the transitive closure of the PE import directories (normal and delay-load) starting from the exe, in modules.PE's
reading. An imported dll name (iat.norm_dll: ASCII lowercase, ".dll" when it has no '.') is resolved case-insensitively against the
files of the given directories (first directory in list order that has it; two files of one directory that differ only in case are an
error). A dll found there is a GUEST IMAGE and is walked further; a dll not found there is a platform import (gate `imports`, owner L5)
and is not an image. Assumption (stated, VERDICT.md): dynamic LoadLibrary names are facts of the `gpa` gate, not of this rule.
Order: the exe, then the images in discovery order (depth first, import directory then delay-load directory)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import iat  # noqa: E402
import modules  # noqa: E402


def image_set(exe_path, dirs):
    """-> [(name as the file is called in lowercase, path)], the exe first (its name lowercased); raises modules.StageError."""
    def read(p):
        with open(p, "rb") as f:
            return f.read()

    def find(name):
        for d in dirs:
            hits = [f for f in sorted(os.listdir(d)) if modules.lower(f) == name and os.path.isfile(os.path.join(d, f))]
            if len(hits) > 1:
                raise modules.StageError("%s: %s holds %s, which differ only in case" % (name, d, ", ".join(hits)))
            if hits:
                return os.path.join(d, hits[0])
        return None

    out, seen = [(modules.lower(os.path.basename(exe_path)), exe_path)], set()

    def walk(path):
        pe = modules.PE(read(path), os.path.basename(path))
        for raw in [d for d, _ in pe.import_directory()] + pe.delay_dll_names():
            name = iat.norm_dll(raw)
            if name in seen:
                continue
            seen.add(name)
            p = find(name)
            if p:
                out.append((name, p))
                walk(p)
    seen.add(out[0][0])
    walk(exe_path)
    return out
