#!/usr/bin/env bash
# setup_verdict_env.sh - bring a fresh container of the base image (Ubuntu 24.04, root) to the environment DECLARED in
# verify/env.json (fnvnx-env/1), the only environment in which an L1 verdict is computed.
#
#   bash ports/fnv-nx/farm/setup_verdict_env.sh [--print]      (--print: only print the command lines, run nothing)
#
# Installs exactly the Debian packages of env.json at exactly those versions from the pinned deb MIRROR (install_debs.py: the
# private fnv-nx-build branch tools/verdict-debs fetched by commit, its tree hash and the sha256 of every deb checked before dpkg;
# no package index is used) and exactly the python modules of python.modules by the sha256 of their wheels (pip --require-hashes
# --no-deps --break-system-packages), then runs `envcheck.py check` and exits with its status.
# The lists are generated from env.json (no second list). Another version is never installed: the fix is a deliberate new
# snapshot commit (`envcheck.py snapshot -o verify/env.json --from-code` on a machine set up by this script, new mirror commit).
# Env: ENV_JSON (default verify/env.json next to this script's tree).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); VERIFY=$(dirname "$HERE")/verify
ENV_JSON=$(readlink -f "${ENV_JSON:-$VERIFY/env.json}")
MODE=${1:-run}; case $MODE in run|--print) ;; *) echo "usage: $0 [--print]" >&2; exit 1 ;; esac
J() { python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); exec(sys.argv[2])' "$ENV_JSON" "$1"; }
REQS=$(J 'print("\n".join("%s==%s --hash=sha256:%s" % (m, v, d["python"]["wheel_sha256"][m]) for m, v in sorted(d["python"]["modules"].items())))')
CHECK="python3 -I $VERIFY/envcheck.py check --env $ENV_JSON"
DEBS="python3 -I $HERE/install_debs.py --env $ENV_JSON"
PIP="python3 -m pip install --break-system-packages --require-hashes --no-deps -r"   # + a requirements file: name==version --hash=sha256:<wheel>
if [ "$MODE" = --print ]; then
  printf '%s\n' "$DEBS" "$PIP REQUIREMENTS" "REQUIREMENTS:" "$REQS" "$CHECK"
  exit 0
fi
$DEBS   # every deb must match its sha256 of env.json (debs.packages) and the mirror its tree_sha256; dpkg -i, no apt
RF=$(mktemp); trap 'rm -f "$RF"' EXIT
printf '%s\n' "$REQS" > "$RF"
$PIP "$RF"   # every wheel must match its sha256 of env.json (python.wheel_sha256) and nothing else is installed (--no-deps)
$CHECK
