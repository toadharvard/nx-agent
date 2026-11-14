#!/bin/sh
# Checks import_escape.py: its documented-prototype selftest, and that the committed tables equal the regenerated ones.
# usage: test_import_escape.sh WINMD SHA256 COMMIT BRIDGE_TSV IMPORT_ESCAPE_TSV [IMPORT_WRITES_TSV]
# A missing WINMD is a declared input that is absent: it is named and the test fails (exit 1).
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
[ -f "$1" ] || { echo "test_import_escape: declared input missing: Win32 metadata '$1'" >&2; exit 1; }
python3 -I "$HERE/import_escape.py" --winmd "$1" --sha256 "$2" --selftest
python3 -I "$HERE/import_escape.py" --winmd "$1" --sha256 "$2" --commit "$3" --bridge "$4" --out "$5" --check
if [ -n "$6" ]; then
python3 -I "$HERE/import_escape.py" --winmd "$1" --sha256 "$2" --commit "$3" --bridge "$4" --out "$6" --check --writes
fi
