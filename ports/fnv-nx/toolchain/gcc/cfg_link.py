#!/usr/bin/env python3
"""Rule CFG-LINK: the link tests of the runtime libraries' configure scripts, answered by a static link.

Why. libstdc++'s configure (and libgcc's) decide features by linking a test program. The guest runtime is compiled by the game's
compiler with the dispatch plugin, so its objects reference the product link's protocol symbols (fnvnx_dispatch, fnvnx_call_fp):
before the product link a real link of any libc.a member that has an indirect call cannot succeed, and configure's own answer would
be "no" for the whole class (and with gcc_no_link=yes configure refuses to link at all). The question configure asks is "does this
program link against the runtime", so it is answered here by what a static link decides.

Rule. A link test succeeds iff
  1. its program compiles (the compile half runs as configure runs it), and
  2. the link of its object resolves: take the link line the driver itself prints (`-###`: crt objects, the object, the -L
     directories and the -l libraries, which include the $LIBS of the test), and process it as ld does for static archives, in
     order, by the linker itself (a relocatable link, ld -r, pulls the same archive members a final link pulls and leaves the
     symbols a final link would report undefined). Every undefined symbol left must be defined by the linker: a symbol the ld default script or the guest link script (link/guest.ld) provides, or a symbol of the product link's
     protocol (the names the dispatch plugin declares: fnvnx_dispatch, fnvnx_call_fp), which only the product link defines.

`patch` rewrites the body of ac_fn_c_try_link and ac_fn_cxx_try_link of a generated configure and sets gcc_no_link=no, with the
citation acinclude.m4 / GCC_NO_EXECUTABLES. `check` is what the rewritten body runs.
"""
import hashlib, json, os, re, shlex, subprocess, sys

DEFINED = set("TDBRSAWVCGIi")


def die(msg):
    sys.stderr.write("cfg_link: " + msg + "\n")
    sys.exit(2)


# ---------------------------------------------------------------- patch

TRY_LINK_CALL = '  (eval "$ac_link") 2>conftest.err\n'
TRY_LINK_NEW = (
    "  # CFG-LINK (toolchain/gcc/cfg_link.py): compile as configure does, then decide the link statically.\n"
    "  ac_cfg_link=`$as_echo \"$ac_link\" | sed 's|>&5[[:space:]]*$||'`\n"
    "  ac_cfg_cc=`$as_echo \"$ac_cfg_link\" | sed 's|-o conftest\\$ac_exeext|-c -o conftest.cfg.o|'`\n"
    "  rm -f conftest.cfg.o conftest.cfg.link\n"
    "  ( eval \"$ac_cfg_cc\" >&5 && eval \"$ac_cfg_link -###\" 2>conftest.cfg.link >/dev/null &&\n"
    "    $PYTHON_CFG_LINK check conftest.cfg.o conftest.cfg.link conftest$ac_exeext >&5 ) 2>conftest.err\n"
)
GCC_NO_LINK_RE = re.compile(r"^(\s*)gcc_no_link=yes\s*$", re.M)


def patch(path):
    s = open(path).read()
    if "CFG-LINK" in s:
        die("already patched: " + path)
    for fn in ("ac_fn_c_try_link", "ac_fn_cxx_try_link"):
        m = re.search(r"^%s \(\)\n\{\n.*?^\} # %s\n" % (fn, fn), s, re.M | re.S)
        if not m:
            continue
        body = m.group(0)
        if body.count(TRY_LINK_CALL) != 1:
            die("%s: the link call of %s is not the expected line" % (path, fn))
        s = s.replace(body, body.replace(TRY_LINK_CALL, TRY_LINK_NEW))
    n = len(GCC_NO_LINK_RE.findall(s))
    if "CFG-LINK" not in s:
        # No ac_fn_*_try_link: the subdir runs no link test through configure's function (libgcc compiles only), and its
        # gcc_no_link=yes is what tells autoconf's own "compiler works" step not to link: nothing to patch.
        print("cfg_link: %s has no link test (nothing to patch)" % path)
        return
    if not n:
        die("%s: ac_fn_*_try_link without gcc_no_link=yes" % path)
    s = GCC_NO_LINK_RE.sub(r"\1gcc_no_link=no", s)
    open(path, "w").write(s)
    print("cfg_link: patched %s (gcc_no_link=yes x%d)" % (path, n))


# ---------------------------------------------------------------- check


def nm(env_nm, path, archive):
    """-> list of (member, name, type); member None for an object file. Cached per file content."""
    cache = os.environ.get("FNVNX_CFG_CACHE")
    key = None
    if cache:
        st = os.stat(path)
        key = os.path.join(cache, hashlib.sha256(("%s:%d:%d" % (os.path.realpath(path), st.st_size, st.st_mtime_ns)).encode()).hexdigest()[:24] + ".json")
        if os.path.exists(key):
            return json.load(open(key))
    out = subprocess.run([env_nm, "--format=posix", "-A", path], capture_output=True, text=True, check=True).stdout
    rows = []
    for line in out.splitlines():
        if archive:
            m = re.match(r"^.*?\[(.*?)\]: (\S+) (\S)", line)
            if m:
                rows.append((m.group(1), m.group(2), m.group(3)))
        else:
            m = re.match(r"^.*?: (\S+) (\S)", line)
            if m:
                rows.append((None, m.group(1), m.group(2)))
    if key:
        os.makedirs(cache, exist_ok=True)
        json.dump(rows, open(key + ".tmp", "w"))
        os.replace(key + ".tmp", key)
    return rows


def script_symbols(ld, abi_emul, guest_script):
    syms = set()
    texts = [subprocess.run([ld, "-m" + abi_emul, "--verbose"], capture_output=True, text=True, check=True).stdout]
    if guest_script and os.path.exists(guest_script):
        texts.append(open(guest_script).read())
    for t in texts:
        syms.update(re.findall(r"PROVIDE(?:_HIDDEN)?\s*\(\s*([A-Za-z_.$][\w.$]*)", t))
        syms.update(re.findall(r"(?:^|;)\s*([A-Za-z_]\w*)\s*(?:=|\+=)", t, re.M))
    return syms


def protocol_symbols(plugin_src):
    s = open(plugin_src).read()
    names = set(re.findall(r'build_fn_decl\("(fnvnx_\w+)"', s)) | set(re.findall(r'get_identifier\("(fnvnx_\w+)"\)', s))
    if not names:
        die("no protocol symbols found in " + plugin_src)
    return names


def link_line(text):
    lines = [l for l in text.splitlines() if "collect2" in l or re.search(r'(^|/)ld"?(\s|$)', l.split(" ")[0] if l.strip() else "")]
    if not lines:
        die("no link line in the driver's -### output")
    return shlex.split(lines[-1])


def check(obj, linkfile, marker):
    nm_, ld = os.environ["FNVNX_CFG_NM"], os.environ["FNVNX_CFG_LD"]
    emul, guest, plug = os.environ["FNVNX_CFG_EMUL"], os.environ.get("FNVNX_CFG_GUEST_LD"), os.environ["FNVNX_CFG_PROTOCOL_SRC"]
    toks = link_line(open(linkfile).read())
    skip_arg = {"-o", "-plugin", "-T", "-z", "-rpath", "-dynamic-linker", "-m"}
    args, i = [], 1
    while i < len(toks):
        t = toks[i]
        if t in skip_arg:
            i += 2
            continue
        if t.startswith(("-L", "-l", "-m")) or t in ("--start-group", "--end-group", "-(", "-)"):
            args.append(t)
        elif t.endswith(".o") and not t.startswith("-"):
            args.append(obj if not os.path.exists(t) and os.path.basename(t).startswith("cc") else t)
        i += 1
    # ld itself decides which archive members the link pulls: a relocatable link (-r) takes the same members for the same
    # undefined symbols, and leaves exactly the symbols that a final link would report as undefined.
    out = marker + ".r.o"
    r = subprocess.run([ld, "-r", "-o", out] + args, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        sys.exit(1)
    left = {n for _, n, ty in nm(nm_, out, False) if ty == "U"}
    os.unlink(out)
    left -= script_symbols(ld, emul, guest) | protocol_symbols(plug)
    if left:
        sys.stderr.write("cfg_link: unresolved: %s\n" % " ".join(sorted(left)[:8]))
        sys.exit(1)
    open(marker, "w").write("cfg-link: resolved by the linker's own archive closure\n")
    os.chmod(marker, 0o755)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "patch":
        patch(sys.argv[2])
    elif len(sys.argv) == 5 and sys.argv[1] == "check":
        check(*sys.argv[2:5])
    else:
        die("usage: cfg_link.py patch <configure> | check <obj> <link-line-file> <marker>")
