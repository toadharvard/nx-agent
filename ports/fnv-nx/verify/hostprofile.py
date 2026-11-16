# reads: ports/fnv-nx/verify/env.json
"""The declared host profile (L1): the paths of the pinned machine that the environment check, the runtime closure and the plugin stage read.
They are data of verify/env.json (record `host`, in the verdict key), never literals of code (abspath gate).
    HOST = {bindir, dpkg_info, dpkg_status, egl_vendor_files, ld_so_conf, ldconfig, libdirs, os_release, sysinclude}
A missing record is an error: the profile is declared, not defaulted."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
KEYS = {"bindir", "dpkg_info", "dpkg_status", "egl_vendor_files", "ld_so_conf", "ldconfig", "libdirs", "os_release", "sysinclude"}


def load(path=os.path.join(HERE, "env.json")):
    with open(path) as f:
        h = json.load(f).get("host")
    if not isinstance(h, dict) or set(h) != KEYS:
        raise ValueError("%s: record `host` must have exactly the keys %s" % (path, sorted(KEYS)))
    return h


HOST = load()
