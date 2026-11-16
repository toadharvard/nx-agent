#!/usr/bin/env python3
"""d3d9_smwalk.py OUT_JSON: the stage of the tool root bridgefacts that writes the output D9_SMWALK: SM1_PARAMS and SM_WALK of d3d9_tables.py (the shader token-walk operand counts) as one JSON
document, the declared input of the generator of render/smscan. Nothing is chosen here: the tables are written as they are."""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import d3d9_tables as T   # noqa: E402

with open(sys.argv[1], "w") as f:
    json.dump({"SM1_PARAMS": T.SM1_PARAMS, "SM_WALK": T.SM_WALK}, f, sort_keys=True, indent=1)
    f.write("\n")
