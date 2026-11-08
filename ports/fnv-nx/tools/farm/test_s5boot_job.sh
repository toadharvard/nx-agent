#!/usr/bin/env bash
# test_s5boot_job.sh - s5boot_job.sh on a host without the emulator's Python packages: a fresh venv (no unicorn,
# capstone, pyelftools) first on PATH. The job must install them itself and boot.
#
#   tools/farm/test_s5boot_job.sh <link12> <game dir>      e.g. 3f7a6c35e923 ~/fnvnx-farm/game
#
# Short boot (EMU_TIMEOUT 40). Needs network for pip, ARTIFACTS (fnv-nx-build) for the link; publishes to
# s5boot/<link12>-deptest. Exit 0 when the packages were installed and the report has a boot section.
set -uo pipefail
LC=${1:?link12}; GAME=${2:?game dir}
P=$(cd "$(dirname "$0")/../.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
python3 -m venv "$T/venv" || { echo "FAIL: python3 -m venv"; exit 1; }
"$T/venv/bin/python3" -I -c 'import unicorn' 2> /dev/null && { echo "FAIL: the fresh venv already has unicorn"; exit 1; }
export PATH="$T/venv/bin:$PATH"
FARM_HOME=$T/fh FARM_GAME=$GAME S5BOOT_LINK=$LC S5BOOT_TAG=deptest EMU_TIMEOUT=40 \
	bash "$P/tools/farm/s5boot_job.sh" "$LC" "$T/report.md" > "$T/job.log" 2>&1
rc=$?
fail() { echo "FAIL: $*"; cat "$T/job.log"; cat "$T/report.md" 2> /dev/null; exit 1; }
grep -q "installing unicorn" "$T/job.log" || fail "the job did not install the packages"
"$T/venv/bin/python3" -I -c 'import unicorn, capstone, elftools' 2> /dev/null || fail "packages missing after the job"
[ "$rc" = 0 ] || fail "job exit $rc"
grep -q "initializers skipped\|STOPPED\|^milestone" "$T/report.md" || fail "no boot section in the report"
echo "ok: s5boot_job.sh installed its packages and booted $LC"
