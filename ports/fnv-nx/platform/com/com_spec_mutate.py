#!/usr/bin/env python3
"""com_spec_mutate.py IN.json OUT.json FIELD VALUE: a copy of the spec with FIELD of the first parameter of
IDirectSoundBuffer::SetVolume set to VALUE. Only for `make -C tests/s7 com-nocompile` (the spec must be checked against the
declarations: a wrong type must not build, an unknown kind must stop the generator)."""
import json, sys

spec = json.load(open(sys.argv[1]))
for m in spec["methods"]:
    if m["iface"] == "IDirectSoundBuffer" and m["name"] == "SetVolume":
        m["params"][0][sys.argv[3]] = sys.argv[4]
json.dump(spec, open(sys.argv[2], "w"))
