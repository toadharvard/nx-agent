#!/bin/bash
# Builds the synthetic LP64 NRO fixture ELF with the system AArch64 cross tools: tests/nx1/fixture/build.sh <out.elf>
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); OUT=$1; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
aarch64-linux-gnu-as "$HERE/crt0.S" -o "$T/crt0.o"
aarch64-linux-gnu-ld -pie -z text --build-id=sha1 -T "$HERE/nro.ld" "$T/crt0.o" -o "$OUT"
