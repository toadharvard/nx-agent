#!/bin/bash
# Test of the "imports" gate (platform/manifest/gate_imports.py): tiny synthetic objects compiled on the spot (host cc/nm/ar),
# a fake manifest, fake spec files and a fake probe result; both output files are compared byte for byte.
#   defined function -> certified; undefined -> unresolved-symbol; weak -> weak; an object where a function is required
#   (and a function where an object is required) -> not-a-function; no such symbol -> none; msvcrt name the probe denies ->
#   lookup-miss; l3 row and another owner's row without/with --provided -> external/certified; a local COM row -> archive.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
GATE=$HERE/../../platform/manifest/gate_imports.py
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
fail=0
cd "$W" || exit 1
mkdir specs out1 out2
cat >x.c <<'EOF'
int FuncOk(void) { return 1; }
extern int FuncUndef(void);
int call_undef(void) { return FuncUndef(); }
__attribute__((weak)) int FuncWeak(void) { return 1; }
int FuncObj = 3;
int DataOk = 5;
int DataFn(void) { return 2; }
int nxb_IFoo_M(void) { return 1; }
EOF
cc -c x.c -o x.o && ar rcs libx.a x.o || { echo "FAIL: cannot build the fixture"; exit 1; }
cat >specs/kernel32.spec <<'EOF'
@ stdcall FuncOk()
@ stdcall FuncUndef()
@ stdcall FuncWeak()
@ stdcall FuncObj()
@ stdcall FuncNone()
@ extern DataOk
@ extern DataFn
@ stdcall DebugBreak()
EOF
cat >specs/msvcrt.spec <<'EOF'
@ cdecl LkYes()
@ cdecl LkNo()
EOF
printf 'kernel32.dll\tFuncOk\nkernel32.dll\tFuncUndef\nkernel32.dll\tFuncWeak\nkernel32.dll\tFuncObj\nkernel32.dll\tFuncNone\nkernel32.dll\tDataOk\nkernel32.dll\tDataFn\nkernel32.dll\tDebugBreak\nmsvcrt.dll\tLkYes\nmsvcrt.dll\tLkNo\n' >names.tsv
printf 'msvcrt.dll\tLkYes\tyes\nmsvcrt.dll\tLkNo\tno\n' >probe.tsv
printf '!IFoo\tM\t0\tstdcall\ti\ti\tnxb_IFoo_M\tsym\n' >com.tsv
printf 'd3d9.dll\tDirect3DCreate9\t\tstdcall\ti\ti\tnxb_Direct3DCreate9\tsym\n' >d9.tsv
printf 'fnvl_os_DebugBreak\nnxb_Direct3DCreate9\n' >provided.txt
run() {   # out-dir, extra args...
    local o=$1; shift
    GATE_OUT=$W/$o python3 -s -B "$GATE" --manifest names.tsv --specs specs --archive libx.a --nm nm --probe probe.tsv \
        --extra com.tsv --external d9.tsv "$@" >"$W/$o/stdout" 2>&1
    echo $? >"$W/$o/rc"
}
run out1
run out2 --provided provided.txt
printf 'd3d9.dll!Direct3DCreate9\texternal\nkernel32.dll!DataFn\tnot-a-function\nkernel32.dll!DebugBreak\texternal\nkernel32.dll!FuncNone\tnone\nkernel32.dll!FuncObj\tnot-a-function\nkernel32.dll!FuncUndef\tunresolved-symbol\nkernel32.dll!FuncWeak\tweak\nmsvcrt.dll!LkNo\tlookup-miss\n' >want1.ids
printf 'kernel32.dll!DataFn\tnot-a-function\nkernel32.dll!FuncNone\tnone\nkernel32.dll!FuncObj\tnot-a-function\nkernel32.dll!FuncUndef\tunresolved-symbol\nkernel32.dll!FuncWeak\tweak\nmsvcrt.dll!LkNo\tlookup-miss\n' >want2.ids
printf '%s' '{"certified": 4, "detail": "8 of 12 imports without a proven body: external 2, lookup-miss 1, none 1, not-a-function 2, unresolved-symbol 1, weak 1", "format": "fnvnx-gate/1", "name": "imports", "status": "red", "uncertified": ["d3d9.dll!Direct3DCreate9", "kernel32.dll!DataFn", "kernel32.dll!DebugBreak", "kernel32.dll!FuncNone", "kernel32.dll!FuncObj", "kernel32.dll!FuncUndef", "kernel32.dll!FuncWeak", "msvcrt.dll!LkNo"], "universe": 12}' >want1.json
printf '%s' '{"certified": 6, "detail": "6 of 12 imports without a proven body: lookup-miss 1, none 1, not-a-function 2, unresolved-symbol 1, weak 1", "format": "fnvnx-gate/1", "name": "imports", "status": "red", "uncertified": ["kernel32.dll!DataFn", "kernel32.dll!FuncNone", "kernel32.dll!FuncObj", "kernel32.dll!FuncUndef", "kernel32.dll!FuncWeak", "msvcrt.dll!LkNo"], "universe": 12}' >want2.json
chk() {   # name, got, want
    if cmp -s "$2" "$3"; then echo "ok   $1"; else echo "FAIL $1"; diff "$2" "$3" | head -6; fail=1; fi
}
chk run1-ids out1/imports.ids want1.ids
chk run1-json out1/imports.json want1.json
chk run2-ids out2/imports.ids want2.ids
chk run2-json out2/imports.json want2.json
[ "$(cat out1/rc)" = 2 ] && [ "$(cat out2/rc)" = 2 ] || { echo "FAIL exit codes: $(cat out1/rc) $(cat out2/rc), want 2 2"; fail=1; }
# green: every uncertified row removed from the fixture
printf 'kernel32.dll\tFuncOk\nkernel32.dll\tDataOk\nkernel32.dll\tDebugBreak\nmsvcrt.dll\tLkYes\n' >names.tsv
mkdir out3
GATE_OUT=$W/out3 python3 -s -B "$GATE" --manifest names.tsv --specs specs --archive libx.a --nm nm --probe probe.tsv \
    --extra com.tsv --external d9.tsv --provided provided.txt >/dev/null 2>&1
rc=$?
printf '%s' '{"certified": 6, "detail": "6 imports, every body proven by the archive'"'"'s symbol table, the probe or a provided list", "format": "fnvnx-gate/1", "name": "imports", "status": "green", "uncertified": [], "universe": 6}' >want3.json
: >want3.ids
[ $rc = 0 ] || { echo "FAIL green run exit code $rc"; fail=1; }
chk run3-ids out3/imports.ids want3.ids
chk run3-json out3/imports.json want3.json
[ $fail -eq 0 ] && echo "PASS gate_imports"
exit $fail
