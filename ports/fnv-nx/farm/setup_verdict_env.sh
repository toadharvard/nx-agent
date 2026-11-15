#!/usr/bin/env bash
# setup_verdict_env.sh - bring a fresh container of the base image (Ubuntu 24.04, root) to the environment DECLARED in
# verify/env.json (fnvnx-env/1), the only environment in which an L1 verdict is computed.
#
#   bash ports/fnv-nx/farm/setup_verdict_env.sh [--print]      (--print: only print the command lines, run nothing)
#
# Installs exactly the Debian packages of env.json at exactly those versions and exactly the python modules of python.modules from
# the pinned MIRROR (install_mirror.py: the private fnv-nx-build branch tools/verdict-debs fetched by commit; the tree hashes of debs/
# and wheels/ and the sha256 of every deb and wheel are checked before dpkg / pip --no-index --require-hashes --no-deps;
# no package index, neither apt nor PyPI, is used), then runs `envcheck.py check` and exits with its status.
# The lists come from env.json (no second list). Another version is never installed: the fix is a deliberate new
# snapshot commit (`envcheck.py snapshot -o verify/env.json --from-code` on a machine set up by this script, new mirror commit).
# Env: ENV_JSON (default verify/env.json next to this script's tree).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); VERIFY=$(dirname "$HERE")/verify
ENV_JSON=$(readlink -f "${ENV_JSON:-$VERIFY/env.json}")
# --profile NAME CHROOT: a build profile (env.json profiles.NAME, e.g. toolchain-build) is built into its own empty CHROOT from its mirror
# (make_chroot.py) and checked there (`envcheck.py check --profile NAME` inside the chroot). Never into this root.
if [ "${1:-}" = --profile ]; then
  [ $# -eq 3 ] || { echo "usage: $0 --profile NAME CHROOT" >&2; exit 1; }
  exec python3 -I "$HERE/make_chroot.py" --profile "$2" --chroot "$3" --env "$ENV_JSON"
fi
MODE=${1:-run}; case $MODE in run|--print) ;; *) echo "usage: $0 [--print | --profile NAME CHROOT]" >&2; exit 1 ;; esac
CHECK="python3 -I $VERIFY/envcheck.py check --env $ENV_JSON"
DEBS="python3 -I $HERE/install_mirror.py --env $ENV_JSON"
if [ "$MODE" = --print ]; then
  printf '%s\n' "$DEBS" "$CHECK"
  exit 0
fi
$DEBS   # debs by dpkg -i and wheels by pip --no-index --require-hashes, all from the mirror, every sha256 and both tree hashes checked first
$CHECK
