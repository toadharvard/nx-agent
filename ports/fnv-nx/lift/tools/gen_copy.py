#!/usr/bin/env python3
"""gen_copy.py SRC DST: a recipe `generated` stage that republishes one tracked file of a root as an output other roots take through `uses` (verify/RECIPE.md "Outputs and uses"). Byte-identical copy."""
import os, shutil, sys
os.makedirs(os.path.dirname(sys.argv[2]), exist_ok=True); shutil.copyfile(sys.argv[1], sys.argv[2])
