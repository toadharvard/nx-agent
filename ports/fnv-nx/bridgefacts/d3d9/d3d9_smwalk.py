#!/usr/bin/env python3
"""d3d9_smwalk.py OUT_JSON: the stage of the tool root bridgefacts that writes the output D9_SMWALK: SM1_PARAMS and SM_WALK of d3d9_tables.py (the shader token-walk operand counts) as one JSON
document, the declared input of the generator of render/smscan. Nothing is chosen here: the tables are written as they are."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import json, sys

import fnvnx.bridgefacts.d3d9.d3d9_tables as T   # noqa: E402

with open(sys.argv[1], "w") as f:
    json.dump({"SM1_PARAMS": T.SM1_PARAMS, "SM_WALK": T.SM_WALK}, f, sort_keys=True, indent=1)
    f.write("\n")
