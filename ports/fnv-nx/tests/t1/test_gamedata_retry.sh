#!/usr/bin/env bash
# test_gamedata_retry.sh - tools/gamedata_fetch.sh survives dropped transfers: a git wrapper fails the first fetches
# (as the proxy's "Connection reset by peer" did on sw36), the fetch retries with backoff and assembles the install;
# a second run is "up to date"; GAMEDATA_TRIES=1 fails on the first drop. Synthetic items.   bash tests/t1/test_gamedata_retry.sh
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
R=$T/remote.git; git init -q --bare "$R"
pub() {   # pub <branch> <dir>
	git -C "$2" init -q && git -C "$2" add -A && git -C "$2" -c user.name=t -c user.email=t@t commit -qm x &&
		git -C "$2" push -q "$R" "HEAD:refs/heads/$1" 2>/dev/null
}
mkdir -p "$T/e"; echo exe >"$T/e/FalloutNV.exe"; echo ini >"$T/e/Fallout_default.ini"; pub gamedata/exe "$T/e"
mkdir -p "$T/d"; head -c 300000 /dev/urandom >"$T/whole"; split -b 100000 -d -a 2 "$T/whole" "$T/d/Test.esm.part"
{ printf 'name\tTest.esm\nkind\tfile\nsha256\t%s\n' "$(sha256sum "$T/whole" | cut -c1-64)"
  for f in "$T"/d/Test.esm.part*; do printf 'part\t%s\t%s\t%s\n' "$(basename "$f")" "$(stat -c %s "$f")" "$(sha256sum "$f" | cut -c1-64)"; done
} >"$T/d/MANIFEST.tsv"; pub gamedata/data/Test.esm "$T/d"
# a git that drops the first $DROP fetches
mkdir -p "$T/bin"; REAL=$(command -v git)
cat >"$T/bin/git" <<SH
#!/bin/sh
case " \$* " in *" fetch "*) n=\$(cat "$T/drops" 2>/dev/null || echo 0)
  if [ "\$n" -lt "\${DROP:-0}" ]; then echo \$((n + 1)) >"$T/drops"; echo "fatal: unable to access: Connection reset by peer" >&2; exit 128; fi ;; esac
exec "$REAL" "\$@"
SH
chmod +x "$T/bin/git"
run() { PATH=$T/bin:$PATH ARTIFACTS_URL=$R bash "$P/tools/gamedata_fetch.sh" "$T/out$1" >"$T/log$1" 2>&1; }
# the retry sleeps 2 + 4 s for two drops
DROP=2 run 1; rc=$?
ok "two dropped fetches: install assembled" '[ $rc = 0 ] && cmp -s "$T/whole" "$T/out1/Data/Test.esm" && [ -f "$T/out1/FalloutNV.exe" ]'
ok "the retries are logged" '[ "$(grep -c "retry" "$T/log1")" = 2 ]'
ok "no repository left behind" '[ -z "$(ls -d "${TMPDIR:-/tmp}"/gamedata-fetch.* 2>/dev/null | xargs -r -n1 sh -c "[ \$0 -nt $T/whole ] && echo \$0")" ]'
run 1
ok "second run: up to date" 'grep -q "up to date" "$T/log1"'
rm -f "$T/drops"; DROP=1 GAMEDATA_TRIES=1 run 2; rc=$?
ok "GAMEDATA_TRIES=1: the first drop fails the fetch" '[ $rc != 0 ]'
[ $fails = 0 ] && echo "test_gamedata_retry: all ok" || { echo "test_gamedata_retry: $fails failed"; cat "$T"/log*; exit 1; }
