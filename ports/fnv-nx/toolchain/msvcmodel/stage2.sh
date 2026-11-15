# stage2.sh - stage 2 of build.sh (sourced by it): the plugins' flags, newlib, libgcc, libstdc++/libsupc++, the MANIFEST. Everything here is keyed by
# K2 (stamp.py): the stage-1 key, this file, the runtime patches, cfg_link.py, the plugin sources and the record values the runtime reads. A changed K2
# restores the stage-1 snapshot (prefix, build tree, source tree) and reruns only this part. Variables come from build.sh.

NLSHA256=6ff27e3bf022666f43f7802255be680eeff722ac181b1725d21e2e8318604ee3   # sourceware.org/pub/newlib, recorded at first download
BUILDSCRIPTS_COMMIT=5740da9beb433ff9f5b724fafc1127f3a5ddc944                  # github.com/devkitPro/buildscripts
NLPATCH_SHA256=6d5aba4356553c727765c98e7553d3784f31d361c856e220ef27cc26353fb88f  # patches/newlib-$NLV-4.patch
X19_PATCH_SHA256=6b0cea9340bc0291ef74fc3a6eaa2e033b06896955fc7c5ebbd9eb2c21e8d935
NOSIZED_PATCH_SHA256=cb8a51365da7062a063e21a13aa461670f2054c7d9a3a36e6dcfa5cdfe7db204
NLGUEST_PATCH_SHA256=0b7dca2e46cb6c36e999e147f9ec78bac5e4c340172138f8292c8ad44a8abb10   # NL-LDBL (libm/ld, ld128 only when long double != double) + NL-SECT (no second -ffunction-sections source)
# the autotools newlib's own generated files state (configure: Autoconf 2.69, Makefile.in: automake 1.15.1), from their GNU tarballs
AUTOCONF_SHA256=64ebcec9f8ac5b2487125a86a7760d2591ac9e1d3dbd59489633f9de62a57684
AUTOMAKE_SHA256=af6ba39142220687c500f79b4aa2f181d9b24e4f8d8ec497cea4ba26c64bedaf

if [ -f "$W/stamp-k2" ] && [ "$(cat "$W/stamp-k2")" != "$STAMP" ]; then
  rm -rf "$P" "$W/b-gcc" "$SRC" "$NL" "$W/b-newlib" "$W"/stamp-unpack2 "$W"/stamp-specs "$W"/stamp-newlib "$W"/stamp-limits "$W"/stamp-libs
  cp -a "$W/s1/prefix" "$P"; cp -a "$W/s1/b-gcc" "$W/b-gcc"; cp -a "$W/s1/src" "$SRC"
fi
echo "$STAMP" > "$W/stamp-k2"
note "# build.sh stage-2 key $STAMP; newlib $NLV sha256 $NLSHA256; devkitPro/buildscripts $BUILDSCRIPTS_COMMIT"

fetch newlib-$NLV.tar.gz "${NEWLIB_TARBALL_URL:-https://sourceware.org/pub/newlib/newlib-$NLV.tar.gz}" $NLSHA256
fetch autoconf-2.69.tar.xz "${AUTOCONF_TARBALL_URL:-https://ftp.gnu.org/gnu/autoconf/autoconf-2.69.tar.xz}" $AUTOCONF_SHA256
fetch automake-1.15.1.tar.xz "${AUTOMAKE_TARBALL_URL:-https://ftp.gnu.org/gnu/automake/automake-1.15.1.tar.xz}" $AUTOMAKE_SHA256
BS=${BUILDSCRIPTS_DIR:-$W/buildscripts}
if [ ! -d "$BS/.git" ]; then
  git clone -q https://github.com/devkitPro/buildscripts "$BS"
fi
git -C "$BS" checkout -q $BUILDSCRIPTS_COMMIT
echo "$NLPATCH_SHA256  $BS/patches/newlib-$NLV-4.patch" | sha256sum -c --quiet
echo "$X19_PATCH_SHA256  $PATCH_DIR/x19-x27.patch" | sha256sum -c --quiet
echo "$NOSIZED_PATCH_SHA256  $PATCH_DIR/libstdcxx-nosized.patch" | sha256sum -c --quiet
echo "$NLGUEST_PATCH_SHA256  $PATCH_DIR/newlib-guest.patch" | sha256sum -c --quiet

# the CFG-LINK patch of the runtime subdirs' configure scripts: guest_compiler.json runtime_configure_patch, read through recipe
eval "$(python3 -I - "$VERIFY" <<'EOF4'
import json, os, shlex, sys
sys.path.insert(0, sys.argv[1])
import recipe
v = recipe.runtime_configure_patch(json.load(open(os.path.join(sys.argv[1], "guest_compiler.json"))))
print("CFG_SUBDIRS=(%s)" % " ".join(shlex.quote(d) for d in v["subdirs"]))
print("CFG_GUEST_LD=%s CFG_PROTOCOL_SRC=%s" % (shlex.quote(v["guest_script"]), shlex.quote(v["protocol_source"])))
EOF4
)"



# apply one -p0 patch of L5 (it names the trees of the old two-compiler build): the sections whose tree this build has
apply_p0() {
  python3 -I - "$1" <<'EOF' | patch -s -p0 -d "$W"
import os, re, sys
W = os.environ["W"]
secs = re.split(r"(?m)^(?=--- )", open(sys.argv[1]).read())
out = [secs[0]] + [s for s in secs[1:] if os.path.isdir(os.path.join(W, s.split("\n")[0].split()[1].split("/")[0]))]
sys.stdout.write("".join(out))
EOF
}
export W

if ! step unpack2; then
  rm -rf "$NL"
  mkdir "$NL"
  tar -xzf newlib-$NLV.tar.gz -C "$NL" --strip-components=1
  patch -s -d "$NL" -p1 < "$BS/patches/newlib-$NLV-4.patch"
  apply_p0 "$PATCH_DIR/x19-x27.patch"
  apply_p0 "$PATCH_DIR/libstdcxx-nosized.patch"
  # CFG-LINK: every runtime subdir's generated configure answers its link tests by a static link (see the libs step)
  for d in "${CFG_SUBDIRS[@]}"; do python3 -I "$PATCH_DIR/cfg_link.py" patch "$SRC/$d/configure"; done
  # NL-LDBL (newlib-guest.patch): configure and Makefile.in are regenerated with the autotools the tarball's files state. Proof that
  # the tooling is the right one: on the pristine tarball tree the regeneration reproduces its configure and Makefile.in byte for byte.
  AT=$W/autotools
  if [ ! -x "$AT/bin/automake" ]; then
    rm -rf "$W/at-src"; mkdir "$W/at-src"
    tar -xJf autoconf-2.69.tar.xz -C "$W/at-src"; tar -xJf automake-1.15.1.tar.xz -C "$W/at-src"
    (cd "$W/at-src/autoconf-2.69" && ./configure --prefix="$AT" >/dev/null && make -j"$JOBS" >/dev/null && make install >/dev/null)
    (cd "$W/at-src/automake-1.15.1" && PATH="$AT/bin:$PATH" ./configure --prefix="$AT" >/dev/null && make -j"$JOBS" >/dev/null && make install >/dev/null)
    rm -rf "$W/at-src"
  fi
  regen() { (cd "$1/newlib" && PATH="$AT/bin:$PATH" autoconf && PATH="$AT/bin:$PATH" automake Makefile); }
  rm -rf "$W/nl-pristine"; mkdir "$W/nl-pristine"; tar -xzf newlib-$NLV.tar.gz -C "$W/nl-pristine" --strip-components=1
  cp "$W/nl-pristine/newlib/configure" "$W/nl-pristine/newlib/Makefile.in" "$W/"
  regen "$W/nl-pristine"
  cmp "$W/nl-pristine/newlib/configure" "$W/configure" && cmp "$W/nl-pristine/newlib/Makefile.in" "$W/Makefile.in" \
    || { echo "autoconf 2.69 / automake 1.15.1 do not reproduce the tarball's configure / Makefile.in" >&2; exit 2; }
  rm -rf "$W/nl-pristine" "$W/configure" "$W/Makefile.in"
  apply_p0 "$PATCH_DIR/newlib-guest.patch"
  regen "$NL"
  done_ unpack2
fi

# says (recipe.semantics_for), plus the plugin flags of the caller. Needs the stage-1 driver.
tlflags() { # c|cxx
  python3 -I - "$VERIFY" "$P/bin/$T-$([ "$1" = c ] && echo gcc || echo g++)" "$1" <<'EOF'
import json, os, sys
sys.path.insert(0, sys.argv[1])
import recipe
pin = json.load(open(os.path.join(sys.argv[1], "guest_compiler.json")))
sel = ["-fPIC"]   # the multilib adds the code model's selector
c = [f for f in recipe.semantics_set(None) if f not in sel]
print(" ".join(recipe.semantics_for(sys.argv[2], sys.argv[3], flags=c) + recipe.runtime_cflags(pin)))   # runtime_cflags: the guest_compiler.json field (newlib's -DPREFER_SIZE_OVER_SPEED), never spelled here
EOF
}


PLUG_C=""; PLUG_CXX=""
[ -n "${FNVNX_TL_PLUGIN_FLAGS_C:-}" ] && PLUG_C=$(cat "$FNVNX_TL_PLUGIN_FLAGS_C")
[ -n "${FNVNX_TL_PLUGIN_FLAGS_CXX:-}" ] && PLUG_CXX=$(cat "$FNVNX_TL_PLUGIN_FLAGS_CXX")
TLC="-O2 $(tlflags c) $PLUG_C"   # no -ffunction-sections/-fdata-sections: rtasm reads whole objects with their .fnvnx* sections
# -save-temps=obj for C++ (rule ODRDEF-PP of fnvnx_names.cc): the driver runs the compiler's own two steps, preprocess then compile the preprocessed text, as the
# recipe's PP and CC stages do; the ODR definition record re-lexes the unit and can only do so from its own preprocessed text (a raw unit's include chains and macros are
# not readable from the plugin). The flag changes no code: the same cc1 input, the preprocessed text is what the compiler would have lexed.
TLCXX="-O2 -save-temps=obj $(tlflags cxx) $PLUG_CXX"
note "CFLAGS_FOR_TARGET=$TLC"
note "CXXFLAGS_FOR_TARGET=$TLCXX"

# devkitA64's gcc links `--start-group -lsysbase -lc --end-group` (devkitPro gcc patch): newlib's libc takes its syscall layer from
# libsysbase, so configure's link tests of the target libraries need it. The driver of this build gets that library group in a specs
# file (build tree and installed driver while the libraries are built; the installed one is removed again at the end).
SPECS=$W/farm.specs
printf '*lib:\n%%{!shared:%%{g*:-lg} %%{!p:%%{!pg:--start-group -lc -lsysbase --end-group}}%%{p:-lc_p}%%{pg:-lc_p}}\n\n' > "$SPECS"
if ! step specs; then
  cp "$SPECS" "$P/lib/gcc/$T/$V/specs"; cp "$SPECS" "$W/b-gcc/gcc/specs"; done_ specs
fi

if ! step newlib; then
  mkdir -p b-newlib; cd b-newlib
  note "newlib configure: CFLAGS_FOR_TARGET='$TLC' $NL/configure --disable-newlib-supplied-syscalls --enable-newlib-mb --disable-newlib-wide-orient --enable-newlib-register-fini --target=$T --prefix=$P"
  CFLAGS_FOR_TARGET="$TLC" "$NL/configure" --disable-newlib-supplied-syscalls --enable-newlib-mb \
    --disable-newlib-wide-orient --enable-newlib-register-fini --target=$T --prefix="$P" > ../configure-newlib.log 2>&1
  # the .info manuals (libgloss/doc, newlib/doc) are documentation, not part of the runtime: the pinned environment has no texinfo, and the
  # tarball's own prebuilt .info files are what `make` would regenerate when the unpack order leaves a .texinfo newer than its .info.
  # MAKEINFO=true is the documented way (configure's own message) to say "do not run makeinfo"; no library or header depends on it.
  note "# newlib make/make install: MAKEINFO=true (there is no texinfo in the pinned env, and the .info docs are not a product)"
  make -j"$JOBS" MAKEINFO=true > ../make-newlib.log 2>&1
  make install -j1 MAKEINFO=true > ../install-newlib.log 2>&1
  cd ..; done_ newlib
fi
[ "$STAGE" = newlib ] && { echo "$P/bin/$T-g++"; exit 0; }

# gcc's limits.h was generated before newlib's headers existed: regenerate it now that they are installed
if ! step limits; then
  rm -f b-gcc/gcc/stmp-int-hdrs b-gcc/gcc/include/limits.h
  make -C b-gcc -j"$JOBS" all-gcc > make-limits.log 2>&1
  make -C b-gcc install-gcc > install-limits.log 2>&1
  grep -q syslimits "$P/lib/gcc/$T/$V/include/limits.h"
  cp "$SPECS" "$P/lib/gcc/$T/$V/specs"; cp "$SPECS" "$W/b-gcc/gcc/specs"
  done_ limits
fi

if ! step libs; then
  # Rule CFG-LINK (toolchain/gcc/cfg_link.py, recorded as guest_compiler.json runtime_configure_patch). The configure scripts of the runtime
  # subdirs (patched in the unpack step) answer every link test by a static link: the test compiles, and the linker's own archive closure
  # (ld -r over the driver's -### link line) leaves only linker-script and product-protocol symbols undefined.
  export PYTHON_CFG_LINK="python3 -I $PATCH_DIR/cfg_link.py"
  export FNVNX_CFG_NM=$P/bin/$T-nm FNVNX_CFG_LD=$P/bin/$T-ld FNVNX_CFG_EMUL=aarch64elf${ABI#ilp}
  export FNVNX_CFG_GUEST_LD=$VERIFY/../$CFG_GUEST_LD FNVNX_CFG_PROTOCOL_SRC=$VERIFY/../$CFG_PROTOCOL_SRC FNVNX_CFG_CACHE=$W/cfg-cache
  note "target libraries: make -C b-gcc CFLAGS_FOR_TARGET='$TLC' CXXFLAGS_FOR_TARGET='$TLCXX'"
  make -C b-gcc -j"$JOBS" CFLAGS_FOR_TARGET="$TLC" CXXFLAGS_FOR_TARGET="$TLCXX" > make-libs.log 2>&1
  make -C b-gcc install CFLAGS_FOR_TARGET="$TLC" CXXFLAGS_FOR_TARGET="$TLCXX" > install-libs.log 2>&1
  # The record of what the configure computed under CFG-LINK: the ac_cv_func_* / ac_cv_search_* values of each multilib's config.log (the
  # cache-variable section). The multilibs must agree; the MANIFEST carries it as cfg_func.
  python3 -I - "$W/b-gcc/$T" "$P/CFG-FUNC.json" <<'EOF5'
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
  done_ libs
fi
note "multilibs: $("$P/bin/$T-gcc" -print-multi-lib | tr '\n' ' ')"
# every library the g++ driver names on a C++ link line exists in each multilib (the names are the driver's own: -### of a C++ link;
# libsupc++ is part of libstdc++'s convenience set and is checked with it)
echo 'int main() {}' > "$W/probe.cc"
for ml in "" $MLFLAGS; do
  for lib in $("$P/bin/$T-g++" -mabi=$ABI $ml -### "$W/probe.cc" -o "$W/probe" 2>&1 | grep collect2 | tr ' ' '\n' | tr -d '"' | sed -n 's/^-l//p' | sort -u) supc++; do
    f=$("$P/bin/$T-g++" -mabi=$ABI $ml -print-file-name=lib$lib.a)
    [ -f "$f" ] || { echo "build.sh: missing lib$lib.a for the multilib '$ml' ($f)" >&2; exit 4; }
  done
done
# MANIFEST.json (fnvnx-runtime-manifest/1): what the recipe compares against the compiler before any unit runs. The compiler's own
# repo/ref/commit/tree_sha256 are not in it (the manifest is inside the tree that hash covers): they are the pin in guest_compiler.json.
# Plugins come from the recipe's objects.json (FNVNX_PLUGINS_JSON: name, sha256, stage_key) in recipe.PLUGIN_SOURCES order, argv from the flag files.
if [ -n "${FNVNX_PLUGINS_JSON:-}" ]; then
  # The prefix is self-contained real files (a tree_sha256 of it must not depend on link targets): every symlink (libtool's .so version
  # chain of libcc1 and the plugin libraries) becomes a copy of the regular file it resolves to inside the prefix; any other kind is an error.
  while IFS= read -r l; do
    t=$(readlink -f "$l")
    case "$t" in "$P"/*) [ -f "$t" ] || { echo "build.sh: symlink $l does not resolve to a file ($t)" >&2; exit 5; } ;;
      *) echo "build.sh: symlink $l resolves outside the prefix ($t)" >&2; exit 5 ;; esac
    cp --remove-destination "$t" "$l"
  done < <(find "$P" -type l)
  [ -z "$(find "$P" -type l)" ] || { echo "build.sh: the prefix holds symlinks:" >&2; find "$P" -type l | head >&2; exit 5; }
  # Host programs (the compiler's own x86-64 executables under bin/ and libexec/) lose their .debug_* sections: a host program is not a guest object,
  # and with -g cc1/cc1plus/lto1/lto-dump exceed the 100 MB file limit of the mirror. The class is every x86-64 ELF file in those two directories;
  # the guest libraries and their members keep all their sections.
  while IFS= read -r f; do
    [ "$(head -c4 "$f" | od -An -c | tr -d ' ')" = '177ELF' ] || continue
    [ "$(readelf -h "$f" | sed -n 's/^ *Machine: *//p')" = "Advanced Micro Devices X86-64" ] || continue
    strip --strip-debug "$f"
  done < <(find "$P/bin" "$P/libexec" -type f)
  python3 -I - "$P" "$T" "$V" "$SHA256" "$STAMP" "$MSVC_PATCH" "$MLFLAGS" "$PLUG_C" "$PLUG_CXX" "$TLC" "$TLCXX" "$CONF" "$FNVNX_PLUGINS_JSON" "$VERIFY" "$P/bin/$T-gcc" "$K1" <<'EOF'
import sys, os, json, hashlib, subprocess
P, T, V, SHA, STAMP, PATCH, ML, PC, PCX, TLC, TLCXX, CONF, PJ, VERIFY, GCC, K1 = sys.argv[1:17]
sys.path.insert(0, VERIFY)
import recipe
h = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
objs = {p["name"]: p for p in json.load(open(PJ))["plugins"]}
argv = lambda n: [t for t in (PC + " " + PCX).split() if t.startswith("-fplugin-arg-%s-" % n)]
plugins = [{"name": n, "so_sha256": objs[n]["sha256"], "stage_key": objs[n].get("stage_key"), "argv": sorted(set(argv(n)))} for n in recipe.PLUGIN_SOURCES if n in objs]
libs = []
for root in (os.path.join(P, T, "lib"), os.path.join(P, "lib", "gcc", T, V)):
    for d, _, fs in sorted(os.walk(root)):
        for f in sorted(fs):
            if f.endswith(".a") or (f.startswith("crt") and f.endswith(".o")) or f == "libgcc.a":
                libs.append({"path": os.path.relpath(os.path.join(d, f), P), "sha256": h(os.path.join(d, f))})
ml = subprocess.run([GCC, "-print-multi-lib"], capture_output=True, text=True, check=True).stdout.split()
conf = [l for l in open(CONF).read().splitlines() if l.strip()]
m = {"format": "fnvnx-runtime-manifest/1",
     "compiler": {"gcc": V, "source_sha256": SHA, "msvcmodel_patch_sha256": h(PATCH), "build_key": STAMP, "stage_keys": {"compiler": K1, "runtime": STAMP}, "configure": conf},
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
fi
echo "$P/bin/$T-g++"
