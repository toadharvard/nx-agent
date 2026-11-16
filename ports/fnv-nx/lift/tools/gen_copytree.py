#!/usr/bin/env python3
"""gen_copytree.py DST SRC [SRC ...]: a recipe `generated` stage that republishes whole tracked directories of a root under DST/<basename> as one directory output (verify/RECIPE.md "Directory outputs"),
so a `run` step takes the packages the lifter tools import (fnvlift, proof) at one stage path. Byte-identical copy; a __pycache__ is not copied."""
import os, shutil, sys
dst = sys.argv[1]
for src in sys.argv[2:]: shutil.copytree(src, os.path.join(dst, os.path.basename(src.rstrip('/'))), ignore=shutil.ignore_patterns('__pycache__'))
