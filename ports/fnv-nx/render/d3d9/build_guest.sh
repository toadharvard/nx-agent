#!/bin/bash
# Build the D3D9 render layer (render/d3d9 product sources) for the guest (AArch64 ILP32, newlib) as ONE archive.
#
#   FNVNX_TC=<toolchain prefix of fnvnx-a64> [JOBS=n<=4] render/d3d9/build_guest.sh OUT_DIR
#
# Output:  OUT_DIR/gen/                 generated sources and tables (gen_impl, state_model, res_smgen, gen_bridge, gen_env)
#          OUT_DIR/libfnvnx-d3d9.a      every product source: dev/*.cpp except app_unk_native.cpp (the guest uses app_unk_guest.cpp),
#                                       the generated .cpp files, front/*.c. Deterministic (ar D, fixed member order, no paths).
#          OUT_DIR/defined_nxb.txt      the nxb_* symbols the archive defines (expected: column 7 of gen/d9bridge_rows.tsv; set-compared)
#          OUT_DIR/undefined.tsv        every symbol undefined in the archive as a whole: symbol, demangled, owner, members
#          OUT_DIR/build.json           compiler version, flags, sources with sha256, archive sha256
# Nothing is stubbed: methods and backend hooks without a body stay undefined and are listed. No weak symbols, no
# --unresolved-symbols / --allow-undefined. Flags: the ABI flags of platform/build_guest.sh (ARCH), plus -Wall (no -w).
set -euo pipefail
export LC_ALL=C PYTHONDONTWRITEBYTECODE=1 TZ=UTC
OUT_ARG=${1:?usage: build_guest.sh OUT_DIR}
HERE=$(cd "$(dirname "$0")" && pwd)             # .../render/d3d9
ROOT=$(cd "$HERE/../.." && pwd)                  # .../fnv-nx
mkdir -p "$OUT_ARG"; OUT=$(realpath "$OUT_ARG")
TC=${FNVNX_TC:-}
[ -z "$TC" ] && [ -n "${DEVKITPRO:-}" ] && TC=$DEVKITPRO/devkitA64
[ -n "$TC" ] || { echo "build_guest: set FNVNX_TC=<toolchain prefix>"; exit 2; }
TC=$(realpath "$TC")
GCC=$TC/bin/aarch64-none-elf-gcc; GXX=$TC/bin/aarch64-none-elf-g++; AR=$TC/bin/aarch64-none-elf-ar; NM=$TC/bin/aarch64-none-elf-nm
for t in "$GCC" "$GXX" "$AR" "$NM"; do [ -x "$t" ] || { echo "build_guest: missing $t"; exit 2; }; done
JOBS=${JOBS:-4}; [ "$JOBS" -le 4 ] || JOBS=4
case "$ROOT$OUT$TC" in *" "*) echo "build_guest: paths with spaces are not supported"; exit 2;; esac

SPEC=$HERE/spec/d3d9_spec.json
GEN=$OUT/gen; OBJ=$OUT/obj; LOGS=$OUT/logs
rm -rf "$GEN" "$OBJ" "$LOGS" "$OUT/libfnvnx-d3d9.a" "$OUT"/defined_nxb.txt "$OUT"/undefined.tsv "$OUT"/build.json
mkdir -p "$GEN" "$OBJ" "$LOGS"

# ---- generators
PY=(python3 -I)
"${PY[@]}" "$HERE/gen/gen_impl.py" "$GEN" "$SPEC"
"${PY[@]}" "$HERE/dev/state_model.py" "$GEN" "$SPEC"
"${PY[@]}" "$HERE/dev/res_smgen.py" "$GEN" "$ROOT/render/shaders/total/sm_table.json" "$HERE/spec/d3d9_tables.py"
"${PY[@]}" "$HERE/gen/gen_bridge.py" "$GEN" "$SPEC"
"${PY[@]}" "$HERE/env/gen_env.py" "$GEN"

# ---- flags (platform/build_guest.sh ARCH + COMMON without -w)
ARCH=(-mabi=ilp32 -march=armv8-a+crc+crypto -mtune=cortex-a57 -fno-pie -ftls-model=local-exec -fsigned-char)
COMMON=("${ARCH[@]}" -O2 -g -ffunction-sections -fdata-sections -frounding-math -fshort-wchar -Wall
        "-ffile-prefix-map=$ROOT=." "-ffile-prefix-map=$OUT=out")
INC=("-I$ROOT/shim/include" "-I$GEN" "-I$HERE/dev" "-I$HERE/front" "-I$HERE/backend")
CXXF=("${COMMON[@]}" -std=c++17 -fno-exceptions -fno-rtti -DSTDMETHODCALLTYPE= "${INC[@]}")
CF=("${COMMON[@]}" -std=gnu11 -DSTDMETHODCALLTYPE= "${INC[@]}")

# ---- job list (fixed order: sorted by path within each group)
JOBLIST=$OUT/jobs.txt; : >"$JOBLIST"
while IFS= read -r f; do
    [ "$(basename "$f")" = app_unk_native.cpp ] && continue
    echo "cxx ${f#"$ROOT/"}" >>"$JOBLIST"
done < <(find "$HERE/dev" -maxdepth 1 -name '*.cpp' | sort)
while IFS= read -r f; do echo "cxx gen:$(basename "$f")" >>"$JOBLIST"; done < <(find "$GEN" -maxdepth 1 -name '*.cpp' | sort)
while IFS= read -r f; do echo "c ${f#"$ROOT/"}" >>"$JOBLIST"; done < <(find "$HERE/front" -maxdepth 1 -name '*.c' | sort)
export GCC GXX ROOT GEN OBJ LOGS CXXF_S="${CXXF[*]}" CF_S="${CF[*]}"
compile_one() {                                   # kind source
    local kind=$1 src=$2 abs obj log
    case $src in gen:*) abs=$GEN/${src#gen:}; obj=$OBJ/gen_${src#gen:}.o;; *) abs=$ROOT/$src; obj=$OBJ/$(echo "$src" | tr / _).o;; esac
    log=$LOGS/$(basename "$obj").log
    cd "$ROOT"
    # shellcheck disable=SC2086
    case $kind in
        cxx) "$GXX" $CXXF_S -c "$abs" -o "$obj" ;;
        c)   "$GCC" $CF_S -c "$abs" -o "$obj" ;;
    esac >"$log" 2>&1 || { echo "$kind $src" >"$log.fail"; return 1; }
}
export -f compile_one
NALL=$(wc -l <"$JOBLIST")
echo "build_guest: compiling $NALL sources with $("$GCC" -dumpfullversion) (fnvnx-a64: $("$GCC" --version | head -1)), -j$JOBS"
set +e
xargs -P "$JOBS" -L 1 bash -c 'compile_one "$@"' _ <"$JOBLIST" 2>/dev/null
set -e
NFAIL=$(find "$LOGS" -name '*.fail' | wc -l)
if [ "$NFAIL" -gt 0 ]; then
    echo "build_guest: $NFAIL of $NALL sources FAILED:"
    for f in $(find "$LOGS" -name '*.fail' | sort); do echo "  $(cat "$f"): $(grep -m1 -E 'error' "${f%.fail}" || tail -1 "${f%.fail}")"; done
    exit 1
fi
# warnings are kept in logs (not silenced); count them
NWARN=$(cat "$LOGS"/*.log | grep -c 'warning:' || true)

# ---- archive: member order = job order
mapfile -t MEMBERS < <(while read -r kind src; do
    case $src in gen:*) echo "$OBJ/gen_${src#gen:}.o";; *) echo "$OBJ/$(echo "$src" | tr / _).o";; esac
done <"$JOBLIST")
(cd "$OBJ" && "$AR" Dcr "$OUT/libfnvnx-d3d9.a" "${MEMBERS[@]##*/}")

# ---- reports (nm parsed by columns)
export NM OUT HERE ROOT GEN TC JOBS NWARN
export GCCV="$("$GCC" --version | head -1)"
python3 -I - "$OUT" <<'PYEOF'
import hashlib, json, os, re, subprocess, sys
out = sys.argv[1]; NM = os.environ["NM"]; HERE = os.environ["HERE"]; ROOT = os.environ["ROOT"]; GEN = os.environ["GEN"]; TC = os.environ["TC"]
jobs = [l.split(" ", 1) for l in open(os.path.join(out, "jobs.txt")).read().split("\n") if l]
def objof(src):
    if src.startswith("gen:"): return os.path.join(out, "obj", "gen_" + src[4:] + ".o")
    return os.path.join(out, "obj", src.replace("/", "_") + ".o")
def sha(p): return hashlib.sha256(open(p, "rb").read()).hexdigest()
def nm(args, o): return subprocess.run([NM] + args + [o], capture_output=True, text=True, check=True).stdout
def cxxfilt(names):
    if not names: return {}
    r = subprocess.run([os.path.join(os.path.dirname(NM), "aarch64-none-elf-c++filt")], input="\n".join(names) + "\n", capture_output=True, text=True, check=True).stdout.split("\n")
    return dict(zip(names, r))
defined, undef = set(), {}
for kind, src in jobs:
    o = objof(src); m = os.path.basename(o)
    for l in nm(["-g", "--defined-only"], o).split("\n"):
        f = l.split()
        if len(f) == 3: defined.add(f[2])
    for l in nm(["-u"], o).split("\n"):
        f = l.split()
        if len(f) == 2 and f[0] == "U": undef.setdefault(f[1], []).append(m)
und = {s: ms for s, ms in undef.items() if s not in defined}
# defined_nxb.txt
rows = [l.rstrip("\n").split("\t") for l in open(os.path.join(GEN, "d9bridge_rows.tsv")) if l.strip()]
want = {r[6] for r in rows}; have = sorted(s for s in defined if s.startswith("nxb_"))
open(os.path.join(out, "defined_nxb.txt"), "w").write("".join(s + "\n" for s in have))
print(f"defined nxb: {len(set(have) & want)}/{len(want)} expected, {len(set(have) - want)} unexpected, {len(want - set(have))} missing")
# owner classes: exact-name matching against lists
dem = cxxfilt(sorted(und))
methods = {l.strip() for l in open(os.path.join(GEN, "d9impl_methods.txt")) if l.strip()}
ident = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
hooks = set()
for h in sorted(f for f in os.listdir(os.path.join(HERE, "dev")) if f.endswith((".h", ".hpp"))):
    txt = open(os.path.join(HERE, "dev", h), errors="replace").read()
    txt = re.sub(r"/\*.*?\*/", " ", txt, flags=re.S); txt = re.sub(r"//[^\n]*", " ", txt)
    hooks |= {t for t in ident.findall(txt) if t.startswith(("nxd3d_", "nx_"))}
L1L3 = {"fnvnx_import_key": "L1/L3a", "fnvnx_guest_invoke": "L1/L3a", "nx_env_profile": "L5 platform"}
libdirs = [os.path.join(TC, "aarch64-none-elf", "lib", "ilp32"), os.path.join(TC, "aarch64-none-elf", "lib")]
libdefs = set()
for d in libdirs:
    for n in ("libc.a", "libm.a", "libstdc++.a", "libgcc.a", "libsupc++.a"):
        p = os.path.join(d, n)
        if not os.path.exists(p):
            for r, _, fs in os.walk(os.path.join(TC, "lib", "gcc")):
                if n in fs and "ilp32" in r and "pic" not in r: p = os.path.join(r, n); break
        if os.path.exists(p):
            for l in subprocess.run([NM, "-g", "--defined-only", p], capture_output=True, text=True).stdout.split("\n"):
                f = l.split()
                if len(f) == 3: libdefs.add(f[2])
def owner(s):
    d = dem[s]; base = d.split("(")[0]
    if base in methods: return "L6 method without body"
    if base in L1L3: return L1L3[base]
    if base in hooks: return "L6 backend glue"
    if s in libdefs: return "toolchain library"
    return "unknown"
lines = ["symbol\tdemangled\towner\tmembers"]; counts = {}
for s in sorted(und):
    o = owner(s); counts[o] = counts.get(o, 0) + 1
    lines.append(f"{s}\t{dem[s]}\t{o}\t{','.join(sorted(set(und[s])))}")
open(os.path.join(out, "undefined.tsv"), "w").write("\n".join(lines) + "\n")
print(f"undefined total {len(und)}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
# build.json
srcs = []
for kind, src in jobs:
    p = os.path.join(GEN, src[4:]) if src.startswith("gen:") else os.path.join(ROOT, src)
    srcs.append({"source": ("gen/" + src[4:]) if src.startswith("gen:") else src, "kind": kind, "sha256": sha(p)})
flags_c = os.environ.get("CXXF_S", "")
bj = {"compiler": os.environ["GCCV"], "gcc_dumpfullversion": subprocess.run([os.path.join(TC, "bin", "aarch64-none-elf-gcc"), "-dumpfullversion"], capture_output=True, text=True).stdout.strip(),
      "flags_cxx": os.environ["CXXF_S"].replace(ROOT, "<fnv-nx>").replace(out, "<out>"), "flags_c": os.environ["CF_S"].replace(ROOT, "<fnv-nx>").replace(out, "<out>"),
      "warnings": int(os.environ["NWARN"]), "sources": srcs, "archive": "libfnvnx-d3d9.a", "archive_sha256": sha(os.path.join(out, "libfnvnx-d3d9.a")),
      "members": len(jobs), "defined_nxb": len(have), "undefined": len(und), "undefined_by_owner": counts}
json.dump(bj, open(os.path.join(out, "build.json"), "w"), indent=1, sort_keys=True); open(os.path.join(out, "build.json"), "a").write("\n")
PYEOF
echo "build_guest: warnings $NWARN; archive sha256 $(sha256sum "$OUT/libfnvnx-d3d9.a" | cut -d' ' -f1)"
