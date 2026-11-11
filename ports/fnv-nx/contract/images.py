#!/usr/bin/env python3
"""images - the guest image set of the contract gates (L1 CONTRACT): one pure function, no hand list.

RULE (dllkind.py, shared with modules.py): the set is the exe and the dlls reached from its import directories (normal and delay-load, in
modules.PE's reading) through guest images only.  A dll name (iat.norm_dll) is resolved case-insensitively against the files of the
given directories; it is a GUEST IMAGE iff it is found, has no bridge row, and its whole normal import closure resolves to guest images
or bridge dlls.  A bridge dll (rows in bridge.tsv) and a found dll the declared machine cannot load are not images and are not walked
further; a name that is neither found nor bridged is a platform import (gate `imports`, owner L5).  Dynamic LoadLibrary names are facts of
the `gpa` gate, not of this rule (VERDICT.md).
Order: the exe, then the images in discovery order (depth first, import directory then delay-load directory)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dllkind  # noqa: E402
import iat  # noqa: E402
import modules  # noqa: E402


def classify(exe_path, dirs, bridge=frozenset()):
    """-> (order, kind): order = [(name, path)] of the exe's reached dlls that are found (guest images and declared ones, discovery order),
    kind = {name: image | declared | bridge | platform}; raises modules.StageError.  BRIDGE: iterable of normalised dll names."""
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

    def imports_of(p):
        pe = modules.PE(read(p), os.path.basename(p))
        return [iat.norm_dll(d) for d, _ in pe.import_directory()], [iat.norm_dll(d) for d in pe.delay_dll_names()]

    n, d = imports_of(exe_path)
    me = modules.lower(os.path.basename(exe_path))
    order, kind = dllkind.classify((n, d), imports_of, find, frozenset(bridge), me)
    return order, kind


def image_set(exe_path, dirs, bridge=frozenset()):
    """-> [(name as the file is called in lowercase, path)], the exe first (its name lowercased): the guest images."""
    order, kind = classify(exe_path, dirs, bridge)
    return [(modules.lower(os.path.basename(exe_path)), exe_path)] + [(n, p) for n, p in order if kind[n] == "image"]
