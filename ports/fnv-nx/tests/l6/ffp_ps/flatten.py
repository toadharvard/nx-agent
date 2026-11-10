#!/usr/bin/env python3
"""flatten.py <ffp.frag.glsl> <out.glsl> [DEFINE ...]: expand #include with build.py's expand() and add #define lines
after #version (what render/native/shaders/build.py does for a variant)."""
import importlib.util, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("nxbuild", os.path.join(HERE, "../../../render/native/shaders/build.py"))
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
text = "".join(b.expand(sys.argv[1]))
defs = "".join("#define %s\n" % d.replace("=", " ", 1) if "=" in d else "#define %s 1\n" % d for d in sys.argv[3:])
i = text.index("\n", text.index("#version")) + 1
open(sys.argv[2], "w").write(text[:i] + defs + text[i:])
