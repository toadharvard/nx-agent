#!/bin/bash
# rt.sh - stage 2 of the game's compiler build, a `run` step of the toolchain product root (step rt): the plugins' flags, newlib, libgcc, libstdc++/libsupc++ and
# the MANIFEST, built by the stage-1 compiler. It reads ONLY what the sandbox mounts (the constants below). Its key is the content of those mounts: stage 1's output tree,
# the plugin objects, the patches, the records, the tarballs, the debs and this file; nothing is computed or listed here.
#   output $OUT_DIR   the compiler tree COMPILER: bin/, lib/, the plugin headers, the runtime libraries and crt, MANIFEST.json
set -euo pipefail
# a red stage names its failing command, the first error lines of every build log and its tail, the error lines last (the recipe keeps the last 20 stderr lines; the logs are redirected, a failure inside them is otherwise silent)
trap 'rc=$?; { for l in $(find /scratch/w -name config.log | sort) /scratch/w/*.log; do [ -f "$l" ] && { echo "== $l: tail"; tail -n 12 "$l"; }; done; echo "== the first real errors of each log (not the ignored ones, not file names):"; for l in /scratch/w/*.log; do [ -f "$l" ] && grep -n -m 3 -E "(^|[ :])error:|\*\*\* .*Error [0-9]|^make.*Error [0-9]+$" "$l" | grep -v "(ignored)" | sed "s|^|${l##*/}: |" || true; done; echo "stage failed: line $LINENO: $BASH_COMMAND (exit $rc)"; } >&2; exit $rc' ERR
# ---- the constants of this step: every path it touches; the mounts are the sandbox's (a change of the mounts is a change of these lines alone) ----
STAGE1=/in/10                                # the compiler external (stage 1's output STAGE1): prefix/, b-gcc/, src/ (the absolute paths below are the ones stage 1 built at)
RECORDS=/in/2                                     # input 2: records2.sh, the stage ref of the generator `records.py stage2` (T ABI MLFLAG, semantics, plugins)   (input 1 is this script)
MSVC_PATCH=/in/3                                  # inputs 3-9: the repo files, in this order (build.json)
P_X19=/in/4                                       #   toolchain/gcc/x19-x27.patch
P_NOSIZED=/in/5                                   #   toolchain/gcc/libstdcxx-nosized.patch
P_NLGUEST=/in/6                                   #   toolchain/gcc/newlib-guest.patch
CFG_LINK_PY=/in/7                                 #   toolchain/gcc/cfg_link.py
CFG_GUEST_LD=/in/8                                #   link/guest.ld
CFG_PROTOCOL_SRC=/in/9                            #   toolchain/dispatch/fnvnx_dispatch.cc
PLUGIN_IN=16                                      # inputs 10.. : {"plugin": NAME} in the records' PLUGIN_ORDER; plugin i is /in/$((PLUGIN_IN+i))/<name>.so
GCC_TARBALL=/in/11            # external `gcc` (only the MANIFEST's source_sha256: the source tree is STAGE1/src)
NEWLIB_TARBALL=/in/12
AUTOCONF_TARBALL=/in/13   # the autotools newlib's own generated files state (configure: Autoconf 2.69, Makefile.in: automake 1.15.1)
AUTOMAKE_TARBALL=/in/14
BS=/in/15                              # devkitPro/buildscripts at its pinned commit: patches/newlib-$NLV-4.patch
PY=/usr/bin/python3.12                          # the python3 deb unpacks the interpreter as python3.12; the python3 symlink is made by its postinst, which a sandbox does not run
SCRATCH=/scratch                                  # the same absolute path as in stage 1
mkdir -p "$SCRATCH/tmp"; export TMPDIR=$SCRATCH/tmp   # the writable temp directory: /tmp is read-only in the sandbox (config.guess, make and the compilers write temp files)
WORK=$SCRATCH/w; PREFIX=$SCRATCH/prefix
OUT_DIR=/out
V=13.3.0
NLV=4.6.0.20260123
# ---------------------------------------------------------------------------------------------------------------------------------------------------------------
. "$RECORDS"
JOBS=${JOBS:-$(nproc)}   # the job count is not part of the content
W=$WORK; P=$PREFIX; SRC=$W/gcc-$V; NL=$W/newlib-$NLV
export LC_ALL=C TZ=UTC SOURCE_DATE_EPOCH=0   # the host tools are the declared debs' (PATH=/usr/bin:/bin)
CONF=$P/CONFIGURE.txt
note() { printf '%s\n' "$*" >> "$CONF"; }
# stage 1's output goes back to the absolute paths it was built at (GCC's build tree and the compiler's configured paths are absolute)
mkdir -p "$W"; cp -a --no-preserve=ownership "$STAGE1/prefix" "$P"; cp -a --no-preserve=ownership "$STAGE1/b-gcc" "$W/b-gcc"; cp -a --no-preserve=ownership "$STAGE1/src" "$SRC"
chmod -R u+w "$P" "$W/b-gcc" "$SRC"   # the external is a read-only store tree: the copies are the step's own working trees
# the host (build) tools of the top-level configures (newlib's and the runtime libraries' own) are named by their real files, as in stage 1: the unversioned names are alternatives links a deb does not unpack
HB=$SCRATCH/hostbin; mkdir -p "$HB"
for pair in gcc:gcc-13 cc:gcc-13 g++:g++-13 c++:g++-13 awk:mawk nawk:mawk; do ln -sf "/usr/bin/${pair#*:}" "$HB/${pair%%:*}"; done
for t in /usr/bin/x86_64-linux-gnu-*; do n=${t##*/x86_64-linux-gnu-}; case "$n" in *-[0-9]*) continue ;; esac; [ -e "/usr/bin/$n" ] || [ -e "$HB/$n" ] || ln -s "$t" "$HB/$n"; done
export PATH=$P/bin:$HB:$PATH
export CC=gcc-13 CXX=g++-13 CC_FOR_BUILD=gcc-13 CXX_FOR_BUILD=g++-13   # the build machine's compilers; the target's are CC_FOR_TARGET (the stage-1 compiler in $P/bin)
cd "$W"
note "# stage 2; newlib $NLV; devkitPro/buildscripts at $BS"
mkdir -p "$W/tarballs"
BSP=$BS/patches/newlib-$NLV-4.patch
# the CFG-LINK patch of the runtime subdirs: guest_compiler.json runtime_configure_patch, from the records (CFG_SUBDIRS; the two files it names, link/guest.ld and the dispatch plugin source, are inputs 8 and 9)

# apply one -p0 patch of L5 (it names the trees of the old two-compiler build): the sections whose tree this build has
apply_p0() {
  "$PY" -I - "$1" <<'EOF' | patch -s -p0 -d "$W"
import os, re, sys
W = os.environ["W"]
secs = re.split(r"(?m)^(?=--- )", open(sys.argv[1]).read())
out = [secs[0]] + [s for s in secs[1:] if os.path.isdir(os.path.join(W, s.split("\n")[0].split()[1].split("/")[0]))]
sys.stdout.write("".join(out))
EOF
}
export W

{
  rm -rf "$NL"
  mkdir "$NL"
  tar --no-same-owner -xzf "$NEWLIB_TARBALL" -C "$NL" --strip-components=1
  patch -s -d "$NL" -p1 < "$BSP"
  apply_p0 "$P_X19"
  apply_p0 "$P_NOSIZED"
  # CFG-LINK: every runtime subdir's generated configure answers its link tests by a static link (see the libs step)
  for d in "${CFG_SUBDIRS[@]}"; do "$PY" -I "$CFG_LINK_PY" patch "$SRC/$d/configure"; done
  # NL-LDBL (newlib-guest.patch): configure and Makefile.in are regenerated with the autotools the tarball's files state. Proof that
  # the tooling is the right one: on the pristine tarball tree the regeneration reproduces its configure and Makefile.in byte for byte.
  AT=$W/autotools
  if [ ! -x "$AT/bin/automake" ]; then
    rm -rf "$W/at-src"; mkdir "$W/at-src"
    tar --no-same-owner -xJf "$AUTOCONF_TARBALL" -C "$W/at-src"; tar --no-same-owner -xJf "$AUTOMAKE_TARBALL" -C "$W/at-src"
    (cd "$W/at-src/autoconf-2.69" && ./configure --prefix="$AT" >/dev/null && make -j"$JOBS" >/dev/null && make install >/dev/null)
    (cd "$W/at-src/automake-1.15.1" && PATH="$AT/bin:$PATH" ./configure --prefix="$AT" >/dev/null && make -j"$JOBS" >/dev/null && make install >/dev/null)
    rm -rf "$W/at-src"
  fi
  regen() { (cd "$1/newlib" && PATH="$AT/bin:$PATH" autoconf && PATH="$AT/bin:$PATH" automake Makefile); }
  rm -rf "$W/nl-pristine"; mkdir "$W/nl-pristine"; tar --no-same-owner -xzf "$NEWLIB_TARBALL" -C "$W/nl-pristine" --strip-components=1
  cp "$W/nl-pristine/newlib/configure" "$W/nl-pristine/newlib/Makefile.in" "$W/"
  regen "$W/nl-pristine"
  cmp "$W/nl-pristine/newlib/configure" "$W/configure" && cmp "$W/nl-pristine/newlib/Makefile.in" "$W/Makefile.in" \
    || { echo "autoconf 2.69 / automake 1.15.1 do not reproduce the tarball's configure / Makefile.in" >&2; exit 2; }
  rm -rf "$W/nl-pristine" "$W/configure" "$W/Makefile.in"
  apply_p0 "$P_NLGUEST"
  regen "$NL"
}

# the target libraries' semantic flags: the records' SEMANTICS minus the multilib selector (the multilib adds it), minus every flag the stage-1 driver itself says
# does not apply to the language (its diagnostic "command-line option 'X' is valid for C++/ObjC++ but not for C", from one run on an empty input in the driver's -x
# language; any other failure is an error), then the records' RUNTIME_CFLAGS. The same rule as recipe.semantics_for.
tlflags() { # c|cxx
  local x=c drv=$P/bin/$T-gcc flags=() f err rc=0 drop out=()
  [ "$1" = cxx ] && { x=c++; drv=$P/bin/$T-g++; }
  for f in "${SEMANTICS[@]}"; do [ "$f" = -fPIC ] || flags+=("$f"); done
  err=$(LC_ALL=C LANGUAGE=C "$drv" -x $x -E -o /dev/null "${flags[@]}" /dev/null 2>&1) || rc=$?
  [ $rc = 0 ] || { echo "rt.sh: the driver refuses the semantic flags for $1: $err" >&2; exit 3; }
  drop=$(printf '%s\n' "$err" | sed -n "s/.*command-line option '\([^']*\)' is valid for [^ ]* but not for.*/\1/p")
  for f in "${flags[@]}"; do printf '%s\n' "$drop" | grep -qxF -- "$f" || out+=("$f"); done
  echo "${out[*]} ${RUNTIME_CFLAGS[*]}"
}

# the plugins' flags: -fplugin=<so> in the records' PLUGIN_ORDER, then each plugin's arguments from the records (recipe.plugin_args("native")); the same for C and C++
PLUG=""
PLUGIN_SO=()
i=0; for n in "${PLUGIN_ORDER[@]}"; do so=/in/$((PLUGIN_IN+i))/$n.so; i=$((i+1)); [ -f "$so" ] || { echo "rt.sh: $so is not mounted" >&2; exit 9; }; PLUGIN_SO+=("$so"); PLUG="$PLUG -fplugin=$so"; done
for n in "${PLUGIN_ORDER[@]}"; do eval "PLUG=\"\$PLUG \${PLUGIN_ARGV_$n[*]}\""; done
PLUG_C=$PLUG; PLUG_CXX=$PLUG
TLC="-O2 $(tlflags c) $PLUG_C"   # no -ffunction-sections/-fdata-sections: rtasm reads whole objects with their .fnvnx* sections
# -save-temps=obj for C++ (rule ODRDEF-PP of fnvnx_names.cc): the driver runs the compiler's own two steps, preprocess then compile the preprocessed text, as the
# recipe's PP and CC stages do; the ODR definition record re-lexes the unit and can only do so from its own preprocessed text (a raw unit's include chains and macros are
# not readable from the plugin). The flag changes no code: the same cc1 input, the preprocessed text is what the compiler would have lexed.
TLCXX="-O2 -save-temps=obj $(tlflags cxx) $PLUG_CXX"
# a plugin that writes a report (recipe.PLUGIN_OUT, the records' PLUGIN_REPORT) is given `beside` at every compile of the runtime build, configure's probes included: its report goes
# next to the object that compile makes (<output name without suffix>.f2i.json, from the driver's own -o). One report per object, no shared file; the FTOI step below pairs each
# member of each installed archive with its object and its report. A build argument, not part of the compiler's flags in MANIFEST (TLC/TLCXX).
REPORT=""; for n in "${PLUGIN_REPORT[@]}"; do REPORT="$REPORT -fplugin-arg-$n-beside"; done
TLC_RUN="$TLC$REPORT"; TLCXX_RUN="$TLCXX$REPORT"
note "CFLAGS_FOR_TARGET=$TLC"
note "CXXFLAGS_FOR_TARGET=$TLCXX"

# devkitA64's gcc links `--start-group -lsysbase -lc --end-group` (devkitPro gcc patch): newlib's libc takes its syscall layer from
# libsysbase, so configure's link tests of the target libraries need it. The driver of this build gets that library group in a specs
# file (build tree and installed driver while the libraries are built; the installed one is removed again at the end).
SPECS=$W/farm.specs
printf '*lib:\n%%{!shared:%%{g*:-lg} %%{!p:%%{!pg:--start-group -lc -lsysbase --end-group}}%%{p:-lc_p}%%{pg:-lc_p}}\n\n' > "$SPECS"
{
  cp "$SPECS" "$P/lib/gcc/$T/$V/specs"; cp "$SPECS" "$W/b-gcc/gcc/specs"
}

{
  mkdir -p b-newlib; cd b-newlib
  note "newlib configure: CFLAGS_FOR_TARGET='$TLC_RUN' $NL/configure --disable-newlib-supplied-syscalls --enable-newlib-mb --disable-newlib-wide-orient --enable-newlib-register-fini --target=$T --prefix=$P"
  CFLAGS_FOR_TARGET="$TLC_RUN" "$NL/configure" --disable-newlib-supplied-syscalls --enable-newlib-mb \
    --disable-newlib-wide-orient --enable-newlib-register-fini --target=$T --prefix="$P" > ../configure-newlib.log 2>&1
  # the .info manuals (libgloss/doc, newlib/doc) are documentation, not part of the runtime: the pinned environment has no texinfo, and the
  # tarball's own prebuilt .info files are what `make` would regenerate when the unpack order leaves a .texinfo newer than its .info.
  # MAKEINFO=true is the documented way (configure's own message) to say "do not run makeinfo"; no library or header depends on it.
  note "# newlib make/make install: MAKEINFO=true (there is no texinfo in the pinned env, and the .info docs are not a product)"
  make -j"$JOBS" MAKEINFO=true > ../make-newlib.log 2>&1
  make install -j1 MAKEINFO=true > ../install-newlib.log 2>&1
  cd ..
}

# gcc's limits.h was generated before newlib's headers existed: regenerate it now that they are installed
{
  rm -f b-gcc/gcc/stmp-int-hdrs b-gcc/gcc/include/limits.h
  make -C b-gcc -j"$JOBS" all-gcc > make-limits.log 2>&1
  make -C b-gcc install-gcc > install-limits.log 2>&1
  grep -q syslimits "$P/lib/gcc/$T/$V/include/limits.h"
  cp "$SPECS" "$P/lib/gcc/$T/$V/specs"; cp "$SPECS" "$W/b-gcc/gcc/specs"
}

{
  # Rule CFG-LINK (toolchain/gcc/cfg_link.py, recorded as guest_compiler.json runtime_configure_patch). The configure scripts of the runtime
  # subdirs (patched in the unpack step) answer every link test by a static link: the test compiles, and the linker's own archive closure
  # (ld -r over the driver's -### link line) leaves only linker-script and product-protocol symbols undefined.
  export PYTHON_CFG_LINK=""$PY" -I $CFG_LINK_PY"
  export FNVNX_CFG_NM=$P/bin/$T-nm FNVNX_CFG_LD=$P/bin/$T-ld FNVNX_CFG_EMUL=aarch64elf${ABI#ilp}
  export FNVNX_CFG_GUEST_LD=$CFG_GUEST_LD FNVNX_CFG_PROTOCOL_SRC=$CFG_PROTOCOL_SRC FNVNX_CFG_CACHE=$W/cfg-cache
  note "target libraries: make -C b-gcc CFLAGS_FOR_TARGET='$TLC_RUN' CXXFLAGS_FOR_TARGET='$TLCXX_RUN'"
  make -C b-gcc -j"$JOBS" CFLAGS_FOR_TARGET="$TLC_RUN" CXXFLAGS_FOR_TARGET="$TLCXX_RUN" > make-libs.log 2>&1
  make -C b-gcc install CFLAGS_FOR_TARGET="$TLC_RUN" CXXFLAGS_FOR_TARGET="$TLCXX_RUN" > install-libs.log 2>&1
  # The record of what the configure computed under CFG-LINK: the ac_cv_func_* / ac_cv_search_* values of each multilib's config.log (the
  # cache-variable section). The multilibs must agree; the MANIFEST carries it as cfg_func.
  "$PY" -I - "$W/b-gcc/$T" "$P/CFG-FUNC.json" <<'EOF5'
import json, re, sys
base, out = sys.argv[1:3]
def cache(path):
    d = {}
    for line in open(path, errors="replace"):
        m = re.match(r"^(ac_cv_(?:func|search)_\w+)=(.*)$", line.rstrip("\n"))
        if m:
            d[m.group(1)] = m.group(2).strip("'")
    return d
a, b = cache(base + "/libstdc++-v3/config.log"), cache(base + "/pic/libstdc++-v3/config.log")
assert a and a == b, "CFG-LINK answers of the multilibs differ or are empty: %s" % sorted(set(a.items()) ^ set(b.items()))[:6]
json.dump({"rule": "CFG-LINK", "source": "libstdc++-v3 config.log cache variables", "answers": dict(sorted(a.items()))}, open(out, "w"), indent=1)
print("CFG-LINK: %d answers, %d yes" % (len(a), sum(v == "yes" for v in a.values())))
EOF5
  rm -f "$P/lib/gcc/$T/$V/specs"
}
note "multilibs: $("$P/bin/$T-gcc" -print-multi-lib | tr '\n' ' ')"
# every library the g++ driver names on a C++ link line exists in each multilib (the names are the driver's own: -### of a C++ link;
# libsupc++ is part of libstdc++'s convenience set and is checked with it)
echo 'int main() {}' > "$W/probe.cc"
for ml in "" $MLFLAG; do
  for lib in $("$P/bin/$T-g++" -mabi=$ABI $ml -### "$W/probe.cc" -o "$W/probe" 2>&1 | grep collect2 | tr ' ' '\n' | tr -d '"' | sed -n 's/^-l//p' | sort -u) supc++; do
    f=$("$P/bin/$T-g++" -mabi=$ABI $ml -print-file-name=lib$lib.a)
    [ -f "$f" ] || { echo "rt.sh: missing lib$lib.a for the multilib '$ml' ($f)" >&2; exit 4; }
  done
done
# The prefix is self-contained real files: every symlink (libtool's .so version chain of libcc1 and the plugin libraries) becomes a copy of the regular file it resolves to
# inside the prefix; any other kind is an error.
find "$P" -type l > "$W/symlinks.txt"   # a list file, not a process substitution: the stage sandbox has no /dev/fd
while IFS= read -r l; do
  t=$(readlink -f "$l")
  case "$t" in "$P"/*) [ -f "$t" ] || { echo "rt.sh: symlink $l does not resolve to a file ($t)" >&2; exit 5; } ;;
    *) echo "rt.sh: symlink $l resolves outside the prefix ($t)" >&2; exit 5 ;; esac
  cp --remove-destination "$t" "$l"
done < "$W/symlinks.txt"
[ -z "$(find "$P" -type l)" ] || { echo "rt.sh: the prefix holds symlinks:" >&2; find "$P" -type l | head >&2; exit 5; }
# Host programs (the compiler's own x86-64 executables under bin/ and libexec/) lose their .debug_* sections: a host program is not a guest object, and with debug info (the runtime_cflags record)
# cc1/cc1plus/lto1/lto-dump exceed the 100 MB file limit of the mirror. The class is every x86-64 ELF file in those two directories; the guest libraries and their
# members keep all their sections.
find "$P/bin" "$P/libexec" -type f > "$W/hostfiles.txt"
while IFS= read -r f; do
  [ "$(head -c4 "$f" | od -An -c | tr -d ' ')" = '177ELF' ] || continue
  [ "$(readelf -h "$f" | sed -n 's/^ *Machine: *//p')" = "Advanced Micro Devices X86-64" ] || continue
  strip --strip-debug "$f"
done < "$W/hostfiles.txt"
# MANIFEST.json (fnvnx-runtime-manifest/1): the facts of this build for the recipe and the gates, all derived from the mounts: the plugin objects' sha256 in the records'
# PLUGIN_ORDER with their arguments, the flags of the target libraries, the multilib set, every library's sha256, CFG-LINK's answers. There is no key in it: the key of
# this tree is the stage key of the step that wrote it (the sandbox's content key).
PLUGLIST=$W/plugins.tsv; : > "$PLUGLIST"
i=0; for n in "${PLUGIN_ORDER[@]}"; do eval "printf '%s\\t%s\\t%s\\n' \"\$n\" \"\$(sha256sum \"\${PLUGIN_SO[$i]}\" | cut -d' ' -f1)\" \"\${PLUGIN_ARGV_$n[*]}\"" >> "$PLUGLIST"; i=$((i+1)); done
"$PY" -I - "$P" "$T" "$V" "$(sha256sum "$GCC_TARBALL" | cut -d' ' -f1)" "$(sha256sum "$MSVC_PATCH" | cut -d' ' -f1)" "$MLFLAG" "$TLC" "$TLCXX" "$CONF" "$PLUGLIST" "$P/bin/$T-gcc" <<'EOF'
import sys, os, json, hashlib, subprocess
P, T, V, SHA, PATCH_SHA, ML, TLC, TLCXX, CONF, PLUGLIST, GCC = sys.argv[1:12]
h = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
plugins = []
for ln in open(PLUGLIST).read().splitlines():
    n, sha, argv = (ln.split("\t") + [""])[:3]
    plugins.append({"name": n, "so_sha256": sha, "argv": sorted(set(argv.split()))})
libs = []
for root in (os.path.join(P, T, "lib"), os.path.join(P, "lib", "gcc", T, V)):
    for d, _, fs in sorted(os.walk(root)):
        for f in sorted(fs):
            if f.endswith(".a") or (f.startswith("crt") and f.endswith(".o")) or f == "libgcc.a":
                libs.append({"path": os.path.relpath(os.path.join(d, f), P), "sha256": h(os.path.join(d, f))})
ml = subprocess.run([GCC, "-print-multi-lib"], capture_output=True, text=True, check=True).stdout.split()
conf = [l for l in open(CONF).read().splitlines() if l.strip()]
m = {"format": "fnvnx-runtime-manifest/1",
     "compiler": {"gcc": V, "source_sha256": SHA, "msvcmodel_patch_sha256": PATCH_SHA, "configure": conf},
     "plugins": plugins,
     "flags": {"c": TLC.split(), "cxx": TLCXX.split()},
     "multilib": {"select": ML.split(), "dir": ml},
     "libraries": libs,
     "cfg_func": json.load(open(os.path.join(P, "CFG-FUNC.json"))),
     "notes": [{"id": "ld-2.42-tls-reloc-segv",
                "text": "binutils 2.42 ld (elfnn-aarch64.c:5308 assertion, then SIGSEGV) on an error path: a final link of any guest object that reaches "
                        "libc's __call_atexit.o without the product link fails first on the undefined thread-local fnvnx_call_fp and then crashes while "
                        "reporting it. Reproduce: aarch64-none-elf-g++ -mabi=ilp32 hello.cc (std::string, std::vector, exception, new/delete). "
                        "The undefined symbols fnvnx_dispatch and fnvnx_call_fp are the product link's protocol (dispatch plugin); not a defect of this tree."}]}
open(os.path.join(P, "MANIFEST.json"), "w").write(json.dumps(m, indent=1, sort_keys=True) + "\n")
print("MANIFEST.json: %d plugins, %d libraries" % (len(plugins), len(libs)))
EOF
# FTOI/: the fnvnx_ftoi report of every object of every installed runtime archive. Each member is paired with the build-tree object whose bytes it is (sha256); the report is the
# one next to that object. The files are FTOI/<archive path below the prefix>/<member>.f2i.json (a member that occurs more than once in its archive gets .<its position> after
# its name) and FTOI/index.json [{archive, member, report}] sorted by (archive, member); report is null when the object has no report beside it (cc1 did not run with the plugin
# loaded, as for an assembler unit). The facts belong to the object's bytes: the written report's `unit` is the sorted list of the units (the source path relative to the compile's
# directory) of every candidate object with those bytes. A member that matches no build object, or whose candidates carry reports that differ in any field but `unit`, or of which
# only some candidates have a report, is an error.
mkdir -p "$OUT_DIR/FTOI"
"$PY" -I - "$P" "$W" "$OUT_DIR/FTOI" <<'EOF3'
import hashlib, json, os, sys
P, W, OUT = sys.argv[1:4]
def members(path):
    d = open(path, "rb").read()
    if d[:8] != b"!<arch>\n":
        raise SystemExit("rt.sh: %s is not a plain ar archive" % path)
    pos, names, out = 8, b"", []
    while pos < len(d):
        h = d[pos:pos + 60]
        name, size = h[:16].decode().rstrip(), int(h[48:58])
        body = d[pos + 60:pos + 60 + size]
        pos += 60 + size + (size & 1)
        if name == "//":
            names = body
        elif name in ("/", "/SYM64/"):
            continue
        else:
            if name.startswith("/") and name[1:].isdigit():
                k = int(name[1:]); name = names[k:names.index(b"/\n", k)].decode()
            else:
                name = name.rstrip("/")
            out.append((name, body))
    return out
archives = sorted(os.path.join(r, f) for r, _, fs in os.walk(P) for f in fs if f.endswith(".a"))
want = {}
for a in archives:
    for n, b in members(a):
        want.setdefault(len(b), set()).add(hashlib.sha256(b).hexdigest())
objs = {}   # sha256 of a build-tree object that some member is -> its paths
for r, ds, fs in os.walk(W):
    ds.sort()
    for f in sorted(fs):
        if not f.endswith(".o"):
            continue
        p = os.path.join(r, f)
        if os.path.getsize(p) in want:
            h = hashlib.sha256(open(p, "rb").read()).hexdigest()
            if h in want[os.path.getsize(p)]:
                objs.setdefault(h, []).append(p)
def report_of(o):
    rp = os.path.splitext(o)[0] + ".f2i.json"
    return json.load(open(rp)) if os.path.isfile(rp) else None
def facts(r):
    return {k: v for k, v in r.items() if k != "unit"}
index, bad = [], []
for a in archives:
    arel = os.path.relpath(a, P)
    ms = members(a)
    count = {}
    for n, _ in ms:
        count[n] = count.get(n, 0) + 1
    seen = {}
    for n, b in ms:
        h = hashlib.sha256(b).hexdigest()
        cands = sorted(objs.get(h, []))
        if not cands:
            bad.append("%s(%s): no build object has these bytes" % (arel, n)); continue
        reps = [r for r in map(report_of, cands) if r is not None]
        if reps and len(reps) != len(cands):
            bad.append("%s(%s): some objects with these bytes have a report and some have none: %s" % (arel, n, ", ".join(cands))); continue
        if any(facts(r) != facts(reps[0]) for r in reps):
            bad.append("%s(%s): the objects with these bytes carry different reports: %s" % (arel, n, ", ".join(cands))); continue
        k = seen.get(n, 0); seen[n] = k + 1
        mname = n if count[n] == 1 else "%s.%d" % (n, k)
        rel = None
        if reps:
            rel = os.path.join(arel, mname + ".f2i.json")
            os.makedirs(os.path.join(OUT, arel), exist_ok=True)
            json.dump(dict(facts(reps[0]), unit=sorted({r["unit"] for r in reps})), open(os.path.join(OUT, rel), "w"), indent=1, sort_keys=True); open(os.path.join(OUT, rel), "a").write("\n")
        index.append({"archive": arel, "member": mname, "report": rel})
if bad:
    sys.stderr.write("rt.sh: FTOI pairing:\n  " + "\n  ".join(bad[:30]) + "\n"); sys.exit(6)
index.sort(key=lambda r: (r["archive"], r["member"]))
json.dump(index, open(os.path.join(OUT, "index.json"), "w"), indent=1); open(os.path.join(OUT, "index.json"), "a").write("\n")
print("FTOI: %d archives, %d members, %d with a report" % (len(archives), len(index), sum(r["report"] is not None for r in index)))
EOF3
# the output: the compiler tree
mkdir -p "$OUT_DIR/COMPILER"   # the directory output COMPILER of build.json
cp -a --no-preserve=ownership "$P/." "$OUT_DIR/COMPILER/"
echo "$P/bin/$T-g++"
