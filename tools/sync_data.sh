#!/bin/sh
# Copy docs and the skill into the Python package. With --check, fail if they differ.
set -eu
cd "$(dirname "$0")/.."
D=host/src/nxagent/data
if [ "${1:-}" = "--check" ]; then
  diff -q docs/SAFETY.md $D/SAFETY.md && diff -q docs/PROTOCOL.md $D/PROTOCOL.md && diff -rq skill/nx-agent $D/skill
  exit
fi
cp docs/SAFETY.md docs/PROTOCOL.md $D/
rm -rf $D/skill && mkdir -p $D/skill && cp -R skill/nx-agent/. $D/skill/
