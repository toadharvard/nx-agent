#!/usr/bin/env bash
# globext_job.sh - the measurement of the global-extents fact (link/globext_cv.py) on the pinned inputs of the contract gate.
#
#   farm/globext_job.sh <out dir> [--range LO HI] [--probe VA...]
#
# Fetches from the private artifacts repository, by the commits of verify/contract_inputs.json (exe, msvc) and the head of
# l1-contract/inputs/e1ca6b02d60d (units.json, the root_results of that input set) and the /Z7 tree msvc/z7/8f324ecde2a7/{1..4}of4 (heads at
# the time of the run; every commit is written to OUT/inputs.txt),
# runs contract/n1.py (the same stage the contract gate runs), then link/globext_cv.py. Everything exe-derived stays in <out>
# and the scratch directory: outside any git checkout, never committed.  Output: OUT/n1.json, OUT/globext.{txt,audit,json},
# OUT/inputs.txt (the commits and tree hashes used).  Run it on a farm: n1 takes more than 10 minutes.
# Env: ARTIFACTS_URL (default https://github.com/toadharvard/fnv-nx-build).  Exit: 0 ok, 1 a fetch or a stage failed, 2 usage.
set -euo pipefail
OUT=${1:?usage: farm/globext_job.sh <out dir> [--range LO HI] [--probe VA...]}; shift
HERE=$(cd "$(dirname "$0")/.." && pwd)
URL=${ARTIFACTS_URL:-https://github.com/toadharvard/fnv-nx-build}
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
if git -C "$OUT" rev-parse --show-toplevel >/dev/null 2>&1; then echo "globext_job: $OUT is inside a git checkout" >&2; exit 2; fi
W=$(mktemp -d "${TMPDIR:-/tmp}/globext.XXXXXX"); trap 'rm -rf "$W"' EXIT
pin() { python3 -I -c "import json,sys; d=json.load(open('$HERE/verify/contract_inputs.json')); e=[x for x in d['external'] if x['name']==sys.argv[1]][0]; print(e['commit'], e['tree_sha256'])" "$1"; }
get() {  # name commit-or-ref dest
  local g="$W/g-$1"; git init -q "$g"
  local i=1; until git -C "$g" fetch -q --depth 1 "$URL" "$2"; do [ $i -ge 5 ] && return 1; sleep $((2 ** i)); i=$((i + 1)); done
  mkdir -p "$3"; git --git-dir="$g/.git" --work-tree="$3" checkout -q -f FETCH_HEAD -- . ; git -C "$g" rev-parse FETCH_HEAD
}
: > "$OUT/inputs.txt"
read -r EC ET <<<"$(pin exe)"; read -r MC MT <<<"$(pin msvc)"
echo "exe $EC tree_sha256 $ET" >> "$OUT/inputs.txt"; echo "msvc $MC tree_sha256 $MT" >> "$OUT/inputs.txt"
get exe "$EC" "$W/exe" >/dev/null; get msvc "$MC" "$W/msvc" >/dev/null
IC=$(get inputs refs/heads/l1-contract/inputs/e1ca6b02d60d "$W/inputs"); echo "inputs $IC" >> "$OUT/inputs.txt"
for k in 1of4 2of4 3of4 4of4; do ZC=$(get "z7-$k" "refs/heads/msvc/z7/8f324ecde2a7/$k" "$W/z7/$k"); echo "z7 $k $ZC" >> "$OUT/inputs.txt"; done
EXE=$W/exe/FalloutNV.exe; [ -f "$EXE" ] || { echo "globext_job: no FalloutNV.exe in the exe external" >&2; exit 1; }
python3 -I "$HERE/contract/n1.py" --exe "$EXE" --units "$W/inputs/units.json" --msvc "$W/msvc" --out "$OUT/n1.json"
python3 -I "$HERE/link/globext_cv.py" --pristine "$W/msvc/obj" --z7 "$W/z7/1of4" --z7 "$W/z7/2of4" --z7 "$W/z7/3of4" --z7 "$W/z7/4of4" --units "$W/inputs/units.json" --n1 "$OUT/n1.json" --exe "$EXE" --out "$OUT" "$@"
