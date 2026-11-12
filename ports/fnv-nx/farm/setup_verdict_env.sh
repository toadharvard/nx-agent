#!/usr/bin/env bash
# setup_verdict_env.sh - bring a fresh container of the base image (Ubuntu 24.04, root) to the environment DECLARED in
# verify/env.json (fnvnx-env/1), the only environment in which an L1 verdict is computed.
#
#   bash ports/fnv-nx/farm/setup_verdict_env.sh [--print]      (--print: only print the command lines, run nothing)
#
# Installs exactly the Debian packages of env.json at exactly those versions (pkg=version) and exactly the python module
# versions (pip --break-system-packages, as the other farm scripts), then runs `envcheck.py check` and exits with its status.
# The lists are generated from env.json (no second list). A package version that the apt sources no longer carry makes the
# script FAIL naming the package; another version is never installed: the fix is a deliberate new snapshot commit
# (`envcheck.py snapshot -o verify/env.json --from-code` on a machine set up by this script) .
# Env: ENV_JSON (default verify/env.json next to this script's tree).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); VERIFY=$(dirname "$HERE")/verify
ENV_JSON=$(readlink -f "${ENV_JSON:-$VERIFY/env.json}")
MODE=${1:-run}; case $MODE in run|--print) ;; *) echo "usage: $0 [--print]" >&2; exit 1 ;; esac
J() { python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); exec(sys.argv[2])' "$ENV_JSON" "$1"; }
PKGS=$(J 'print(*[p + "=" + v for p, v in sorted(d["packages"].items())])')
PIPS=$(J 'print(*[m + "==" + v for m, v in sorted(d["python"]["modules"].items()) if m not in d["python"].get("wheels", {})])')
PIPW=$(J 'print(*[m + "==" + d["python"]["modules"][m] + " --hash=sha256:" + w["sha256"] for m, w in sorted(d["python"].get("wheels", {}).items())], sep="\n")')   # wheels pinned by sha256 (pip --require-hashes)
CHECK="python3 -I $VERIFY/envcheck.py check --env $ENV_JSON"
APT="apt-get install -y --no-install-recommends $PKGS"
PIP="python3 -m pip install --break-system-packages $PIPS"
PIPH="python3 -m pip install --break-system-packages --require-hashes -r"
if [ "$MODE" = --print ]; then
  printf '%s\n' "apt-get update" "$APT" "$PIP" "printf '%s\\n' '$PIPW' > REQ && $PIPH REQ" "$CHECK"
  exit 0
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
missing=0
for spec in $PKGS; do   # exact version or nothing: installed already, or offered by the apt sources
  p=${spec%%=*}; v=${spec#*=}
  [ "$(dpkg-query -W -f '${Version}' "$p" 2>/dev/null || true)" = "$v" ] && continue
  apt-cache madison "$p" | awk -F' [|] ' '{print $2}' | grep -qxF "$v" || { echo "setup: $p=$v is not available in the apt sources" >&2; missing=1; }
done
[ "$missing" = 0 ] || { echo "setup: FAILED, take a new env.json snapshot deliberately" >&2; exit 1; }
$APT
$PIP
if [ -n "$PIPW" ]; then REQ=$(mktemp); printf '%s\n' "$PIPW" > "$REQ"; $PIPH "$REQ"; rm -f "$REQ"; fi
exec $CHECK
