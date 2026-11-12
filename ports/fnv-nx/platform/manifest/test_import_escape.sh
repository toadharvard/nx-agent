#!/bin/sh
# Checks import_escape.py: its documented-prototype selftest, and that the committed table equals the regenerated one.
# usage: test_import_escape.sh WINMD SHA256 COMMIT BRIDGE_TSV IMPORT_ESCAPE_TSV
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
python3 -I "$HERE/import_escape.py" --winmd "$1" --sha256 "$2" --selftest
python3 -I "$HERE/import_escape.py" --winmd "$1" --sha256 "$2" --commit "$3" --bridge "$4" --out "$5" --check
