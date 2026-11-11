#!/usr/bin/env bash
# test_compile_flags.sh - tools/link/compile.sh holds one flag set and one compiler (no prepared tree needed).
#   bash tests/s12/test_compile_flags.sh [work dir]      -> "compile_flags: ok"
# Needs aarch64-linux-gnu-g++/gcc 13 and gcc-13-plugin-dev-aarch64-linux-gnu; the plugins build in the work dir.
# COMPILE_TEST_EXTRA: flags added to every compile.sh run (only while a plugin still wants options removed from it).
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); P=$HERE/../..
W=${1:-/home/user/l2/work-compile}; mkdir -p "$W/bin"; W=$(cd "$W" && pwd)
# the distribution names the cross compilers with a -13 suffix; the unversioned names are the build's own
for t in g++ gcc; do
  [ -e "$W/bin/aarch64-linux-gnu-$t" ] || ln -sf "$(command -v aarch64-linux-gnu-$t 2>/dev/null || echo /usr/bin/aarch64-linux-gnu-$t-13)" "$W/bin/aarch64-linux-gnu-$t"
done
export PATH=$W/bin:$PATH
export SSE2NEON=${SSE2NEON:-/home/user/l2/ext/sse2neon}
CS=$P/tools/link/compile.sh
fail=0
check() { if [ "$2" = "$3" ]; then :; else echo "FAIL: $1: want '$3', got '$2'"; fail=1; fi; }
# empty tree: no units.json, hand-written
mkdir -p "$W/empty"
unset CC FNVNX_SDD

# (d)
for f in "$CS" "$P/tools/farm/compile_job.sh"; do bash -n "$f"; check "bash -n $(basename "$f")" $? 0; done

# compile_job.sh: no key machinery, one compiler, every plugin source in the flags hash
grep -q -e KEYS -e keys_ -e order_keys "$P/tools/farm/compile_job.sh" && { echo "FAIL: compile_job.sh still has keys"; fail=1; }
grep -q 'aarch64-linux-gnu-g++ --version' "$P/tools/farm/compile_job.sh" && { echo "FAIL: compile_job.sh names the compiler"; fail=1; }
grep -q 'ftoi order sdd pi dispatch addr names' "$P/tools/farm/compile_job.sh" || { echo "FAIL: FLAGS_HASH misses plugins"; fail=1; }
grep -q 'CC=' "$P/tools/farm/compile_job.sh" && { echo "FAIL: compile_job.sh sets CC"; fail=1; }

# (a)
flags=$(bash "$CS" --print-flags "$W/empty" "$W/out-flags" 2> "$W/flags.err"); check "--print-flags exit" $? 0
for bad in fnvnx_order-mode fnvnx_order-keys fnvnx_order-refcount fnvnx_order-uninit fnvnx_order-discover; do
  grep -q -- "$bad" <<<"$flags" && { echo "FAIL: --print-flags has $bad"; fail=1; }
done
for want in fnvnx_ftoi fnvnx_order fnvnx_sdd fnvnx_pi fnvnx_dispatch fnvnx_addr fnvnx_names; do
  grep -q -- "-fplugin=.*$want.so" <<<"$flags" || { echo "FAIL: --print-flags lacks plugin $want"; fail=1; }
done
grep -qx -- '-DFNVNX_SDD=1' <<<"$flags" || { echo "FAIL: no -DFNVNX_SDD=1"; fail=1; }
grep -qx -- '-fno-devirtualize' <<<"$flags" || { echo "FAIL: no -fno-devirtualize"; fail=1; }
grep -q 'fnvnx_ftoi-' <<<"$flags" && { echo "FAIL: ftoi plugin argument in the flags"; fail=1; }
grep -q COMPILER_BARRIERS <<<"$flags" && { echo "FAIL: COMPILER_BARRIERS"; fail=1; }

# (b) a CC of another build is refused, both compilers named
msg=$(CC=/usr/bin/gcc bash "$CS" --print-flags "$W/empty" "$W/out-flags" 2>&1 >/dev/null); rc=$?
check "foreign CC exit" "$rc" 2
grep -q '/usr/bin/gcc' <<<"$msg" && grep -q 'aarch64-linux-gnu-g++' <<<"$msg" || { echo "FAIL: foreign CC message names both: $msg"; fail=1; }
# the same build under an explicit CC is accepted
CC=$(command -v aarch64-linux-gnu-gcc) bash "$CS" --print-flags "$W/empty" "$W/out-flags" >/dev/null 2>&1; check "same-build CC exit" $? 0
# a derived CC that does not exist
mkdir -p "$W/lone"; ln -sf "$(command -v aarch64-linux-gnu-g++)" "$W/lone/aarch64-linux-gnu-g++"
CXX="$W/lone/aarch64-linux-gnu-g++" bash "$CS" --print-flags "$W/empty" "$W/out-flags" >/dev/null 2>&1; check "missing derived CC exit" $? 2

# (c)
FNVNX_SDD=0 bash "$CS" --print-flags "$W/empty" "$W/out-flags" >/dev/null 2>&1; check "FNVNX_SDD=0 exit" $? 2
FNVNX_SDD=1 bash "$CS" --print-flags "$W/empty" "$W/out-flags" >/dev/null 2>&1; check "FNVNX_SDD=1 exit" $? 0

# (e) a tiny tree: one C++ unit, one C unit
T=$W/tree; rm -rf "$T" "$W/obj"; mkdir -p "$T/src" "$T/include" "$T/build"
cat > "$T/src/a.cpp" <<'EOF'
struct S { virtual ~S() {} virtual int f(int x) { return x + 1; } };
int use(S *s, int v) { return s->f(v); }
EOF
cat > "$T/src/b.c" <<'EOF'
int add2(int x) { return x + 2; }
EOF
echo '{"units": [{"source": "src/a.cpp"}, {"source": "src/b.c"}]}' > "$T/build/units.json"
# shellcheck disable=SC2086
AB_FLAGS=${COMPILE_TEST_EXTRA:-} bash "$CS" "$T" "$W/obj" "" 2 > "$W/compile.log" 2>&1; check "tiny tree exit" $? 0
check "tiny tree ok rows" "$(grep -c $'\tok$' "$W/obj/objects.tsv" 2>/dev/null)" 2
[ "$(grep -c $'\tok$' "$W/obj/objects.tsv" 2>/dev/null)" = 2 ] || tail -n 20 "$W/obj"/*.log 2>/dev/null | head -n 40

[ $fail = 0 ] && echo "compile_flags: ok"
exit $fail
