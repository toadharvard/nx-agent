#!/usr/bin/env bash
# fuzz_job.sh - farm job: differential fuzzing (tools/difffuzz, docs/DIFFFUZZ.md) of shard k of N of every decomp
# function: the native AArch64 ILP32 function of <commit> against the pristine decomp compiled with MSVC 2008.
#
#   tools/farm/fuzz_job.sh <commit> <k> <N> <report.md>
#
# Inputs, fetched or built when missing, cached under $FARM_HOME:
#   oracle     MSVC 2008 objects of the pristine decomp, one per unit (u<index in units.json>.obj). Fetched from
#              branch msvc/pristine/<decomp12> of the private artifacts repository; if that branch does not exist
#              yet, built here (Wine + Visual C++ 2008 Express SP1/RTM unpacked from Microsoft's ISOs,
#              tools/equiv/README.md; tools/equiv/setup_msvc.sh when present) and published there once.
#   candidate  AArch64 objects of <commit> from the compile farms (tools/farm/fetch_objs.sh, branches
#              c/<commit12>/<i>of<M>; M is the shard count all of whose shards exist, or $OBJ_SHARDS).
#   link       the link step's outputs for those objects (tools/link: resolve, probe, resolve, apply), from branch
#              link/<commit12>: out/{aliases.ld,refs.tsv,abi.tsv,addrmap.tsv,layouts.tsv,cache/msvc.tsv} and
#              fixed/*.o. No job in this directory publishes it yet: without it the script stops with exit 3 and
#              says so in the report. $FUZZ_LINK_DIR=<dir with out/ and fixed/> uses a local link instead.
#   tree       the prepared tree of <commit> (tools/prepare.py, as compile_job.sh makes it).
# Campaign: every catalogued oracle function whose unit has both objects and whose control flow the harness models
# (no indirect calls or jumps, no unresolved relocations: counted as "not modelled"), shard = crc32(address) mod N
# + 1, at most $ITERS executions and $TIME seconds per function (defaults 40 and 8), $JOBS processes, under nice.
# Resumable: a rerun with the same arguments continues after the last finished function.
# Output: <report.md> (counts, verdicts by stream and cause, the hard divergences as address, cause, stream, unit)
# and the reproducers (div/*.json, results.jsonl) on branch fuzz/<commit12>/<k>of<N> of the artifacts repository.
#
# Needs: git, python3 with venv or pip, g++-aarch64-linux-gnu, binutils-aarch64-linux-gnu; to build the oracle:
# root or sudo (apt: wine wine32:i386 p7zip-full cabextract msitools), curl, about 4 GB of free disk while unpacking.
# Push access to toadharvard/fnv-nx-build. The oracle objects, the candidate objects and the reproducers are derived
# from the decomp: they go only to that private repository, never to nx-agent. The report holds counts, addresses,
# unit names and causes only.
# Local inputs: FUZZ_X86OBJ=<dir of u<i>.obj> seeds the oracle cache (and publishes it when the branch is missing);
# FUZZ_OBJ_DIR, FUZZ_TREE, FUZZ_LINK_DIR, FUZZ_EXT use local candidate objects, tree, link, external checkouts.
# Testing: FUZZ_LIMIT=<n> fuzzes the first n targets of the shard; FUZZ_PUBLISH=0 skips every push.
set -euo pipefail
COMMIT=${1:?commit}; K=${2:?k}; N=${3:?N}; REPORT=${4:?report.md}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../../.." && pwd)
DF=$HERE/../difffuzz
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
ARTIFACTS=${ARTIFACTS:-$FARM_HOME/artifacts}
ARTIFACTS_URL=${ARTIFACTS_URL:-https://github.com/toadharvard/fnv-nx-build}
JOBS=${JOBS:-$(nproc)}
ITERS=${ITERS:-40}; TIME=${TIME:-8}
PUBLISH=${FUZZ_PUBLISH:-1}
C12=${COMMIT:0:12}
EXT=${FUZZ_EXT:-$FARM_HOME/ext}
RUN=$FARM_HOME/fuzz-$C12-${K}of${N}
mkdir -p "$FARM_HOME" "$RUN" "$(dirname "$REPORT")"
REPORT=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")
log() { echo "== $(date +%T) $*"; }
t0=$(date +%s)

fail_report() {   # fail_report <exit code> <message>
  printf '# fuzz %s shard %s/%s\n\n- status: not run\n- %s\n' "$C12" "$K" "$N" "$2" > "$REPORT"
  echo "fuzz_job: $2" >&2
  exit "$1"
}

publish() {   # publish <branch> <dir> <message>: one orphan commit of <dir>'s files, pushed to the artifacts repository
  [ "$PUBLISH" = 1 ] || { log "publish $1 skipped (FUZZ_PUBLISH=0)"; return 0; }
  local tmp=$FARM_HOME/pub-$$
  rm -rf "$tmp"; mkdir -p "$tmp"
  cp -a "$2/." "$tmp/"
  git -C "$tmp" init -q
  git -C "$tmp" add -A
  git -C "$tmp" -c user.name=fnv-nx-farm -c user.email=farm@invalid commit -qm "$3"
  local ok=0
  for i in 1 2 3; do
    if git -C "$tmp" push -q -f "$ARTIFACTS_URL" "HEAD:refs/heads/$1" 2>/dev/null; then ok=1; break; fi
    sleep $((i * 10))
  done
  rm -rf "$tmp"
  [ $ok = 1 ] || { echo "fuzz_job: push of $1 failed" >&2; return 1; }
  log "published $1"
}

fetch_branch() {  # fetch_branch <branch> <dest dir>: extract the branch's files; false when it does not exist
  [ -d "$ARTIFACTS/.git" ] || git clone -q --no-checkout "$ARTIFACTS_URL" "$ARTIFACTS"
  local ref="refs/remotes/origin/$1"
  git -C "$ARTIFACTS" fetch -q origin "+refs/heads/$1:$ref" 2>/dev/null || return 1
  rm -rf "$2.tmp"; mkdir -p "$2.tmp"
  git -C "$ARTIFACTS" archive "$ref" | tar -x -C "$2.tmp"
  rm -rf "$2"; mv "$2.tmp" "$2"
}

setup_msvc_inline() {   # <dest>: the decomp README's "Compilers without Windows", for commits without setup_msvc.sh
  local dest=$1 ms=https://download.microsoft.com/download v
  local -A url=([sp1]=$ms/E/8/E/E8EEB394-7F42-4963-A2D8-29559B738298/VS2008ExpressWithSP1ENUX1504728.iso
                [rtm]=$ms/8/B/5/8B5804AD-4990-40D0-A6AA-CE894CBBB3DC/VS2008ExpressENUX1397868.iso)
  local -A sha=([sp1]=6d3b5eedfa461969c5d645871b53002d162a2e238486900d2fdf71774d53fb91
                [rtm]=632318ef0df5bad58fcb99852bd251243610e7a4d84213c45b4f693605a13ead)
  local sudo=; [ "$(id -u)" = 0 ] || sudo=sudo
  if ! command -v wine >/dev/null || ! command -v 7z >/dev/null || ! command -v cabextract >/dev/null \
      || ! command -v msiextract >/dev/null; then
    $sudo dpkg --add-architecture i386
    $sudo apt-get update -q >/dev/null
    DEBIAN_FRONTEND=noninteractive $sudo apt-get install -y -q wine wine32:i386 wine64 p7zip-full cabextract msitools \
      >/dev/null
  fi
  for v in sp1 rtm; do
    local r=$dest/msvc/$v w=$dest/unpack-$v
    [ -f "$r/VC/bin/cl.exe" ] && [ -d "$r/SDK/Include" ] && continue
    rm -rf "$w" "$r"; mkdir -p "$w" "$r"
    curl -sSL --retry 5 --retry-all-errors -o "$w/vs.iso" "${url[$v]}"
    echo "${sha[$v]}  $w/vs.iso" | sha256sum -c --quiet - || { echo "fuzz_job: $v ISO checksum mismatch" >&2; return 1; }
    7z x -y -o"$w/tc" "$w/vs.iso" VCExpress >/dev/null; rm -f "$w/vs.iso"
    cabextract -q -d "$w/ixp" "$w/tc/VCExpress/Ixpvc.exe"
    (cd "$w/ixp" && msiextract -C ../vcx vs_setup.msi >/dev/null)
    local vs="$w/vcx/Program Files/Microsoft Visual Studio 9.0"
    cp -r "$vs/VC:Vc7" "$r/VC"
    for f in mspdb80.dll msobj80.dll mspdbcore.dll mspdbsrv.exe; do
      find "$vs/Common7" -iname "$f" -exec cp {} "$r/VC/bin/" \;
    done
    cabextract -q -d "$w/sdkx" "$w/tc/VCExpress/WCU/WinSDK/WinSDK_Build.exe"
    (cd "$w/sdkx" && msiextract -C ../sdko VistaClientHeadersLibs-x86.msi >/dev/null)
    local sd="$w/sdko/SOURCEDIR/Program Files/Microsoft SDKs:./Windows:./v6.0A:."
    mkdir -p "$r/SDK"; cp -r "$sd/Include" "$r/SDK/Include"; cp -r "$sd/Lib" "$r/SDK/Lib"
    rm -rf "$w"
  done
  WINEPREFIX=$dest/wineprefix WINEARCH=win32 WINEDEBUG=-all timeout 600 wineboot -i >/dev/null 2>&1 || true
  echo "export FNV_MSVC9_ROOT=$dest/msvc/sp1 FNV_MSVC9_RTM_ROOT=$dest/msvc/rtm FNV_MSVC9_PREFIX=$dest/wineprefix"
}

# --- the commit's sources, the decomp, Python modules ------------------------------------------------------------
SRC=$FARM_HOME/src-$C12
[ -d "$SRC" ] || git -C "$REPO" worktree add --detach "$SRC" "$COMMIT" >/dev/null
P=$SRC/ports/fnv-nx
bash "$P/tools/fetch.sh" "$EXT" >/dev/null
DECOMP=$EXT/fnv_decomp
D12=$(git -C "$DECOMP" rev-parse HEAD | cut -c1-12)
PY=python3
if ! python3 -c 'import unicorn, capstone' 2>/dev/null; then
  if [ ! -x "$FARM_HOME/venv/bin/python3" ]; then
    python3 -m venv "$FARM_HOME/venv" 2>/dev/null || { rm -rf "$FARM_HOME/venv"; }
  fi
  if [ -x "$FARM_HOME/venv/bin/python3" ]; then
    PY=$FARM_HOME/venv/bin/python3
    $PY -c 'import unicorn, capstone' 2>/dev/null || $PY -m pip install -q unicorn==2.1.4 capstone==5.0.9
  else
    python3 -m pip install -q --user --break-system-packages unicorn==2.1.4 capstone==5.0.9
  fi
fi

# --- oracle: MSVC 2008 objects of the pristine decomp --------------------------------------------------------------
ORACLE=$FARM_HOME/oracle-$D12
if [ ! -f "$ORACLE/.done" ]; then
  if fetch_branch "msvc/pristine/$D12" "$ORACLE"; then
    log "oracle fetched from msvc/pristine/$D12"
  elif [ -n "${FUZZ_X86OBJ:-}" ]; then
    log "oracle: local objects $FUZZ_X86OBJ"
    rm -rf "$ORACLE"; mkdir -p "$ORACLE/obj"
    cp "$FUZZ_X86OBJ"/u*.obj "$ORACLE/obj/"
    printf '{"decomp_commit": "%s", "objects": %s, "built_by": "equiv.py build pristine (local copy)"}\n' \
      "$(git -C "$DECOMP" rev-parse HEAD)" "$(ls "$ORACLE/obj" | grep -c '\.obj$')" > "$ORACLE/meta.json"
    publish "msvc/pristine/$D12" "$ORACLE" "MSVC 2008 objects of the pristine decomp $D12" || true
  else
    log "oracle: building the pristine decomp with MSVC 2008 (once per decomp commit)"
    MS=$FARM_HOME/msvc
    if [ -f "$HERE/../equiv/setup_msvc.sh" ]; then
      eval "$(bash "$HERE/../equiv/setup_msvc.sh" "$MS" | grep '^export ')"
    else
      eval "$(setup_msvc_inline "$MS" | grep '^export ')"
    fi
    [ -n "${FNV_MSVC9_ROOT:-}" ] || fail_report 4 "MSVC 2008 setup failed (see tools/equiv/README.md)"
    mkdir -p "$ORACLE.build/obj"
    WINEDEBUG=-all nice $PY "$P/tools/equiv/equiv.py" build "$DECOMP" "$DECOMP" --out "$ORACLE.build/fp.jsonl" \
      --units all --objdir "$ORACLE.build/obj" --jobs "$JOBS" > "$ORACLE.build/build.log" 2>&1
    rm -rf "$ORACLE"; mkdir -p "$ORACLE"
    mv "$ORACLE.build/obj" "$ORACLE/obj"
    printf '{"decomp_commit": "%s", "objects": %s, "built_by": "tools/farm/fuzz_job.sh (equiv.py build pristine)"}\n' \
      "$(git -C "$DECOMP" rev-parse HEAD)" "$(ls "$ORACLE/obj" | grep -c '\.obj$')" > "$ORACLE/meta.json"
    publish "msvc/pristine/$D12" "$ORACLE" "MSVC 2008 objects of the pristine decomp $D12" || true
    rm -rf "$ORACLE.build"
  fi
  touch "$ORACLE/.done"
fi
NX86=$(ls "$ORACLE/obj" | grep -c '\.obj$' || true)

# --- prepared tree, candidate objects, link outputs -----------------------------------------------------------------
if [ -n "${FUZZ_TREE:-}" ]; then TREE=$FUZZ_TREE
elif [ -f "$FARM_HOME/compile-$C12/tree.done" ]; then TREE=$FARM_HOME/compile-$C12/tree   # a compile job's
else
  TREE=$RUN/tree
  if [ ! -f "$RUN/tree.done" ]; then
    log "prepare"
    rm -rf "$TREE"; python3 "$P/tools/prepare.py" "$DECOMP" "$TREE" > "$RUN/prepare.log" 2>&1
    touch "$RUN/tree.done"
  fi
fi
if [ -n "${FUZZ_OBJ_DIR:-}" ]; then OBJ=$FUZZ_OBJ_DIR
else
  OBJ=$FARM_HOME/objs-$C12
  if [ ! -f "$OBJ/.done" ]; then
    M=${OBJ_SHARDS:-}
    if [ -z "$M" ]; then
      M=$(git ls-remote "$ARTIFACTS_URL" "refs/heads/c/$C12/*" | sed -n 's|.*/\([0-9]*\)of\([0-9]*\)$|\2|p' \
          | sort | uniq -c | awk '$1 == $2 {print $2}' | sort -n | head -1)
    fi
    [ -n "$M" ] || fail_report 2 "no complete set of compile shards c/$C12/*of<M> in the artifacts repository yet"
    log "candidate objects: $M compile shards"
    ARTIFACTS=$ARTIFACTS bash "$HERE/fetch_objs.sh" "$COMMIT" "$M" "$OBJ" "$TREE" > "$RUN/fetch_objs.log"
    touch "$OBJ/.done"
  fi
fi
if [ -n "${FUZZ_LINK_DIR:-}" ]; then LINK=$FUZZ_LINK_DIR
else
  LINK=$FARM_HOME/link-$C12
  if [ ! -f "$LINK/.done" ]; then
    fetch_branch "link/$C12" "$LINK" || fail_report 3 "no link outputs for $C12 (branch link/$C12 of the artifacts \
repository: out/ and fixed/ of tools/link resolve, probe, resolve, apply); a link job has to publish them first"
    touch "$LINK/.done"
  fi
fi
for f in out/aliases.ld out/refs.tsv out/abi.tsv out/addrmap.tsv fixed/zz_bridges.o; do
  [ -e "$LINK/$f" ] || fail_report 3 "link outputs for $C12 lack $f"
done

export FNVNX_DECOMP=$DECOMP FNVNX_X86OBJ=$ORACLE/obj FNVNX_TREE=$TREE FNVNX_A64OBJ=$OBJ FNVNX_DFLINK=$LINK
export FNVNX_DFWORK=$RUN/work FNVNX_DFA64=$RUN/a64 SSE2NEON=$EXT/sse2neon
unset FNVNX_DFPROFILE
mkdir -p "$RUN/work"
t1=$(date +%s)

# --- targets: catalog (per decomp commit), modelled functions with both objects, this shard ------------------------
CAT=$FARM_HOME/catalog-$D12.jsonl
if [ ! -s "$CAT" ]; then
  log "catalog"
  (cd "$DF" && nice $PY catalog.py --out "$CAT.tmp" > "$RUN/catalog.log" 2>&1) && mv "$CAT.tmp" "$CAT"
fi
(cd "$DF" && $PY - "$CAT" "$K" "$N" "${FUZZ_LIMIT:-0}" "$RUN/targets.jsonl" "$RUN/targets.json" <<'EOF'
import json, sys, zlib
import config
cat, k, n, limit, out, summary = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5], sys.argv[6]
counts = dict(catalogued=0, shard=0, no_native_object=0, not_modelled=0, targets=0)
have = {}
rows = []
for line in open(cat):
    r = json.loads(line)
    if 'error' in r:
        continue
    counts['catalogued'] += 1
    if zlib.crc32(r['address'].encode()) % n != k - 1:
        continue
    counts['shard'] += 1
    s = r['source']
    if s not in have:
        have[s] = config.a64_object(s) is not None
    if not have[s]:
        counts['no_native_object'] += 1
        continue
    if r['unresolved'] or r['indirect'] or r['tails']:
        counts['not_modelled'] += 1
        continue
    ncalls = len(r['calls'])
    rows.append(dict(unit=r['unit'], source=s, address=r['address'], symbol=r['symbol'], size=r['size'],
                     calls=r['calls'], x87=r['x87'], sse=r['sse'], fs=r['fs'],
                     kind='leaf' if ncalls == 0 else 'calls%d' % ncalls))
rows.sort(key=lambda r: r['address'])
if limit:
    rows = rows[:limit]
counts['targets'] = len(rows)
with open(out, 'w') as f:
    for r in rows:
        f.write(json.dumps(r) + '\n')
json.dump(counts, open(summary, 'w'))
print(counts)
EOF
) > "$RUN/targets.log"
log "targets: $(cat "$RUN/targets.json")"

# --- campaign, attribution, report ---------------------------------------------------------------------------------
log "fuzz ($JOBS jobs, $ITERS executions / ${TIME}s per function)"
(cd "$DF" && nice $PY difffuzz.py fuzz "$RUN/targets.jsonl" --out "$RUN/camp" --iters "$ITERS" --time "$TIME" \
  --jobs "$JOBS" --resume > "$RUN/fuzz.log" 2>&1)
t2=$(date +%s)
ATT=$FARM_HOME/attrib-$C12          # stream attribution, once per commit (every shard on this machine shares it)
mkdir -p "$ATT"
if [ ! -s "$ATT/rules.json" ] || [ ! -s "$ATT/bridges.json" ]; then
  log "attribution (prepare rules, bridges)"
  (cd "$DF" && nice $PY attrib.py rules "$ATT/rules.json.tmp" > "$ATT/attrib.log" 2>&1 \
    && nice $PY attrib.py bridges "$ATT/bridges.json.tmp" >> "$ATT/attrib.log" 2>&1 \
    && mv "$ATT/rules.json.tmp" "$ATT/rules.json" && mv "$ATT/bridges.json.tmp" "$ATT/bridges.json") || true
fi
(cd "$DF" && $PY report.py "$RUN/camp" --rules "$ATT/rules.json" --bridges "$ATT/bridges.json" \
  --out "$RUN/verdicts" --hard-tsv "$RUN/hard.tsv" > "$RUN/report.log" 2>&1)
t3=$(date +%s)

$PY - "$RUN" "$REPORT" "$COMMIT" "$D12" "$K" "$N" "$NX86" "$ITERS" "$TIME" "$JOBS" "$((t1-t0))" "$((t2-t1))" \
  "$((t3-t2))" <<'EOF'
import collections, json, os, sys
run, report, commit, d12, k, n, nx86, iters, tm, jobs, ts, tf, tr = sys.argv[1:]
counts = json.load(open(os.path.join(run, 'targets.json')))
status = collections.Counter()
execs = 0
for line in open(os.path.join(run, 'camp', 'results.jsonl')):
    r = json.loads(line)
    status[r.get('status', '?').split(':')[0]] += 1
    execs += r.get('execs', 0)
md = open(os.path.join(run, 'verdicts.md')).read().split('\n## ')[0].strip().split('\n')
hard = [l.rstrip('\n').split('\t') for l in open(os.path.join(run, 'hard.tsv'))] if os.path.exists(
    os.path.join(run, 'hard.tsv')) else []
out = ['# fuzz %s shard %s/%s' % (commit[:12], k, n), '',
       '- commit %s, decomp %s, oracle: %s MSVC 2008 objects of the pristine decomp' % (commit, d12, nx86),
       '- functions catalogued %d; in this shard %d: no native object %d, control flow not modelled (indirect '
       'calls/jumps, unresolved relocations) %d, fuzzed %d' % (counts['catalogued'], counts['shard'],
                                                              counts['no_native_object'], counts['not_modelled'],
                                                              counts['targets']),
       '- results: %s; executions %d (at most %s and %ss per function, %s jobs)' % (
           ', '.join('%s %d' % kv for kv in status.most_common()), execs, iters, tm, jobs),
       '- time: inputs %ss, fuzz %ss, report %ss' % (ts, tf, tr),
       '- reproducers: branch fuzz/%s/%sof%s of the artifacts repository' % (commit[:12], k, n), '']
out += ['## Verdicts', ''] + md[2:] + ['']
out += ['## Hard divergences (%d in %d functions)' % (len(hard), len({row[0] for row in hard})), '',
        '| address | cause | stream | kind | unit |', '|---|---|---|---|---|']
out += ['| %s |' % ' | '.join(row) for row in hard]
open(report, 'w').write('\n'.join(out) + '\n')
print('\n'.join(out[:8]))
EOF

# --- reproducers ---------------------------------------------------------------------------------------------------
PUB=$RUN/pub; rm -rf "$PUB"; mkdir -p "$PUB"
cp "$RUN/camp/results.jsonl" "$RUN/targets.json" "$PUB/"
[ -d "$RUN/camp/div" ] && cp -a "$RUN/camp/div" "$PUB/div"
cp "$RUN/hard.tsv" "$PUB/" 2>/dev/null || true
printf '{"commit": "%s", "decomp": "%s", "shard": %s, "shards": %s, "iters": %s, "time": %s}\n' \
  "$COMMIT" "$D12" "$K" "$N" "$ITERS" "$TIME" > "$PUB/meta.json"
publish "fuzz/$C12/${K}of${N}" "$PUB" "$C12 fuzz shard $K/$N" || true
rm -rf "$PUB"
log "done: $REPORT"
