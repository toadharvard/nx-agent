#!/bin/sh
# Runs import_escape.py on escape_fixture/ (host backend, explicit sources) and compares every letter.
# Exit non-zero on any wrong letter or any missing / unexpected row.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
FX=$HERE/escape_fixture
W=${TMPDIR:-/tmp}/import_escape_test.$$
mkdir -p "$W"
trap 'rm -rf "$W"' EXIT
ulimit -v 6000000 2>/dev/null
python3 "$HERE/import_escape.py" --backend posix --bridge "$FX/fx_bridge.tsv" --root "$FX" --src "$FX/fx.c" --contract "$FX/fx_contract.tsv" \
    --out "$W/out.tsv" --report "$W/rep.tsv" --work "$W/work" || { echo "FAIL: tool error"; exit 1; }
fail=0
# expected rows
while IFS='	' read -r name want wwant; do
    got=$(awk -F'\t' -v n="$name" '$1=="fx.dll" && $2==n {print $3}' "$W/out.tsv")
    wgot=$(awk -F'\t' -v n="$name" '$1=="fx.dll" && $2==n {print $4}' "$W/out.tsv")
    if [ "$got" != "$want" ] || [ "$wgot" != "$wwant" ]; then echo "FAIL $name: want '$want' '$wwant' got '$got' '$wgot'"; fail=1; else echo "ok   $name $got $wgot"; fi
done < "$FX/expected.tsv"
# omitted rows must be reported with the right reason
chk() { grep -q "^fx.dll	$1	$2	" "$W/rep.tsv" || { echo "FAIL report: $1 not omitted with reason '$2'"; fail=1; }; }
chk fx_mismatch "parameter mismatch"
chk fx_missing "no definition"
chk fnvl_thing "no definition"
chk fx_data "no definition"
# a contract naming a function defined in the module must fail the tool
if python3 "$HERE/import_escape.py" --backend posix --bridge "$FX/fx_bridge.tsv" --root "$FX" --src "$FX/fx.c" --contract "$FX/fx_contract_bad.tsv" \
    --out "$W/bad.tsv" --report "$W/badrep.tsv" --work "$W/workbad" >/dev/null 2>&1; then echo "FAIL contract naming a defined function was accepted"; fail=1; else echo "ok   contract on a defined function rejected"; fi
# no row beyond the expected ones
n_exp=$(wc -l < "$FX/expected.tsv"); n_got=$(grep -v '^#' "$W/out.tsv" | tail -n +2 | wc -l)
[ "$n_exp" -eq "$n_got" ] || { echo "FAIL row count: expected $n_exp got $n_got"; fail=1; }
[ $fail -eq 0 ] && echo "PASS import_escape fixture"
exit $fail
