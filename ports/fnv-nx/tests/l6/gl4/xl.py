"""xl.py: the translator build, the declared input of every test that builds libnxgl4 / the backend sources (FNVNX_SHADERS_BUILD = the render/shaders/build.sh build directory).
Without it the test stops with the message of render/gl4/gl4_xlator.sh (exit 2): there is no mode without the translator."""
import os, re, subprocess, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
BACK = os.path.join(ROOT, "render", "gl4"); NXB = os.path.normpath(os.path.join(BACK, "..", "backend"))
_flags = None


def build_dir(): return os.environ.get("FNVNX_SHADERS_BUILD", "")


def flags(build=None):
    """[-I<translator include>, libfnvnx_shaders.a, libmojoshader.a, libuam.a, -lstdc++, -lm, -lpthread] from the one definition of the link line (gl4_xlator.sh)"""
    global _flags
    if build is None and _flags is not None: return _flags
    r = subprocess.run(["sh", os.path.join(BACK, "gl4_xlator.sh"), build_dir() if build is None else build], capture_output=True, text=True)
    if r.returncode: sys.stderr.write(r.stderr); sys.exit(2)
    out = r.stdout.split()
    if build is None: _flags = out
    return out


def deps():
    for ln in open(os.path.join(build_dir(), "CMakeCache.txt")):
        m = re.match(r"FNVNX_DEPS:[A-Z]*=(.*)", ln)
        if m: return m.group(1).strip()
    sys.exit("xl: no FNVNX_DEPS in %s/CMakeCache.txt" % build_dir())


def hand():
    """(sm_hand module, opcode table): hand-assembled D3D9 bytecode (render/shaders/total/sm_hand.py, read-only import)"""
    flags()
    sys.path.insert(0, os.path.join(ROOT, "render", "shaders", "total"))
    import sm_hand
    return sm_hand, sm_hand.load_opcodes(os.path.join(deps(), "mojoshader", "mojoshader_internal.h"))
