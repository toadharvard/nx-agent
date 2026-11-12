#!/usr/bin/env python3
"""Build the chroot of a build profile of verify/env.json from the pinned mirror, then check it. As root:
    python3 -I make_chroot.py --profile toolchain-build --chroot DIR [--env F]
DIR must be absent or empty (never "/": a profile is installed only into its own chroot, the verdict root keeps passing `envcheck.py
check`). The mirror (profiles.<P>.debs.mirror: the fnv-nx-build branch tools/toolchain-debs, fetched by its exact commit) must have the
tree sha256 of debs/ and every deb the sha256 of debs.packages (exit 2 before anything is extracted). The profile's package set is
the WHOLE set (base and closure): no resolution and no package index at this point. Then: every deb is extracted (dpkg-deb -x, the
base-files deb first so that the merged-/usr links exist), and `dpkg --install` of all of them runs inside the chroot (dpkg orders the
unpack step, `--force-depends` for the Pre-Depends cycles of the base, and the configure step without force; `dpkg --audit` must be clean; no retry loop). Last, envcheck.py runs inside the chroot (`check --profile P`): the installed set must
equal the profile exactly. Output: `make_chroot: P: N packages, chroot DIR` and envcheck's result."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.join(os.path.dirname(HERE), "verify")
sys.path.insert(0, VERIFY)
sys.path.insert(0, HERE)
import cas  # noqa: E402
import install_mirror  # noqa: E402

def main(a):
    o = dict(zip(a[::2], a[1::2]))
    prof_name, ch = o["--profile"], os.path.abspath(o["--chroot"])
    envf = o.get("--env", os.path.join(VERIFY, "env.json"))
    if ch == "/" or (os.path.exists(ch) and os.listdir(ch)):
        print("make_chroot: %s is not an empty directory (a profile goes into its own chroot only)" % ch, file=sys.stderr)
        return 2
    doc = json.load(open(envf))
    prof = doc["profiles"][prof_name]
    m, pk = prof["debs"]["mirror"], prof["debs"]["packages"]
    work = tempfile.mkdtemp(prefix="chroot-")
    try:
        dest = install_mirror.fetch(m, work)
        tree = cas.tree_hash(os.path.join(dest, "debs"))
        if tree != m["tree_sha256"]:
            print("make_chroot: mirror tree_sha256 is %s, env.json says %s" % (tree, m["tree_sha256"]), file=sys.stderr)
            return 2
        for n, e in sorted(pk.items()):
            f = os.path.join(dest, "debs", e["file"])
            h = hashlib.sha256(open(f, "rb").read()).hexdigest() if os.path.isfile(f) else "missing"
            if h != e["sha256"]:
                print("make_chroot: %s: sha256 is %s, env.json says %s" % (n, h, e["sha256"]), file=sys.stderr)
                return 2
        os.makedirs(os.path.join(ch, "var/lib/dpkg"), exist_ok=True)
        open(os.path.join(ch, "var/lib/dpkg/status"), "w").close()
        for d in ("etc", "tmp", "proc", "debs"):
            os.makedirs(os.path.join(ch, d), exist_ok=True)
        files = sorted(e["file"] for e in pk.values())
        first = [f for f in files if f.startswith("base-files_")]
        for f in first + [f for f in files if f not in first]:
            subprocess.run(["dpkg-deb", "-x", os.path.join(dest, "debs", f), ch], check=True)
            shutil.copy(os.path.join(dest, "debs", f), os.path.join(ch, "debs", f))
        env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
        # the base has Pre-Depends cycles (base-files -> awk -> libc6 -> ...): the unpack of the closed set ignores the dependency
        # ORDER only (--force-depends, as debootstrap does); the configure step takes no force and orders by Depends, and `dpkg --audit`
        # must then report nothing: the dependencies of the whole set hold or this exits non-zero
        subprocess.run(["chroot", ch, "/bin/sh", "-c", "dpkg --force-overwrite --force-confold --force-depends --unpack /debs/*.deb"], check=True, env=env)
        subprocess.run(["chroot", ch, "dpkg", "--configure", "-a"], check=True, env=env)
        au = subprocess.run(["chroot", ch, "dpkg", "--audit"], capture_output=True, text=True)
        if au.returncode != 0 or au.stdout.strip():
            print("make_chroot: dpkg --audit is not clean:\n" + au.stdout + au.stderr, file=sys.stderr)
            return 1
        shutil.rmtree(os.path.join(ch, "debs"))
        sub = os.path.join(ch, "tmp", "envcheck")
        os.makedirs(sub)
        shutil.copy(os.path.join(VERIFY, "envcheck.py"), sub)
        shutil.copy(envf, os.path.join(sub, "env.json"))
        print("make_chroot: %s: %d packages, chroot %s" % (prof_name, len(pk), ch))
        r = subprocess.run(["chroot", ch, "python3", "-I", "/tmp/envcheck/envcheck.py", "check", "--env", "/tmp/envcheck/env.json", "--profile", prof_name])
        shutil.rmtree(sub, ignore_errors=True)
        return r.returncode
    finally:
        shutil.rmtree(work, ignore_errors=True)

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
