#!/usr/bin/env bash
# gog_fetch.sh - download the player's own Fallout: New Vegas from GOG with a Galaxy login token, unpack it with
# innoextract and check FalloutNV.exe. The game data stays on this machine: <out> must be outside any git checkout,
# and nothing here is ever committed (docs/ARCHITECTURE.md, Legal).
#
#   tools/gog_fetch.sh <out dir> [work dir]
#
# Token: ~/.config/lgogdownloader/galaxy_tokens.json (override with GOG_TOKENS), as lgogdownloader keeps it, or as
# branch gamedata/gog of the private artifacts repository holds it (docs/HANDOFF.md section 5). The file needs
# access_token and refresh_token. An access token older than an hour is refreshed and written back. This script holds
# no credential. CLIENT_ID and CLIENT_SECRET below identify the GOG Galaxy client itself, the same public values every
# open-source GOG client uses (lgogdownloader among them). They are not the player's.
#
# Without a token file: open
#   https://auth.gog.com/auth?client_id=46899977096215655&redirect_uri=https%3A%2F%2Fembed.gog.com%2Fon_login_success%3Forigin%3Dclient&response_type=code&layout=client2
# sign in, and run `tools/gog_fetch.sh --code '<the whole embed.gog.com/on_login_success?...code=... URL>'` within a
# few minutes. It writes the token file and exits.
#
# What it does: products/<id>?expand=downloads (GOG_PRODUCT, default 1454587428 = Fallout: New Vegas Ultimate
# Edition), installer GOG_INSTALLER (default installer_windows_en). Each file is resolved to a CDN link, downloaded
# with resume (the proxy cuts transfers of several GB; up to 8 tries per file), and checked against GOG's md5. Then
# innoextract runs into <out>, and FalloutNV.exe is checked against FNVNX_EXE_SHA256 (default GOG 1.4.0.525(a)).
# The installer files stay in [work dir] (default <out>.dl) unless GOG_KEEP_INSTALLER=0. GOG_DRY_RUN=1 lists the
# installer's files (checks the token and that the account owns the game) and downloads nothing.
# Needs: curl, python3, innoextract, md5sum, sha256sum; about 18 GB free (8.4 installer + 9.5 unpacked).
# Exit: 0 ok, 1 failure, 2 usage, 3 no token.
set -euo pipefail
CLIENT_ID=46899977096215655
CLIENT_SECRET=9d85c43b1482497dbbce61f6e4aa173a433796eeae2ca8c5f6129f2dc4de46d9
REDIRECT='https://embed.gog.com/on_login_success?origin=client'
TOK=${GOG_TOKENS:-$HOME/.config/lgogdownloader/galaxy_tokens.json}
PRODUCT=${GOG_PRODUCT:-1454587428}
INSTALLER=${GOG_INSTALLER:-installer_windows_en}
EXE_SHA=${FNVNX_EXE_SHA256:-89e7020487e80f20ddc92ea641c4433b2c6839a82f45a076e414d167d11dd0fe}

if [ "${1:-}" = "--code" ]; then
  url=${2:?--code needs the on_login_success URL or the code}
  code=$(python3 -I -c "import sys,urllib.parse as u;a=sys.argv[1];q=u.parse_qs(u.urlparse(a).query);print(q['code'][0] if 'code' in q else a)" "$url")
  mkdir -p "$(dirname "$TOK")"; umask 077
  curl -sSf -G https://auth.gog.com/token --data-urlencode "client_id=$CLIENT_ID" \
    --data-urlencode "client_secret=$CLIENT_SECRET" --data-urlencode grant_type=authorization_code \
    --data-urlencode "code=$code" --data-urlencode "redirect_uri=$REDIRECT" |
    python3 -I -c "import json,sys,time;d=json.load(sys.stdin);assert 'refresh_token' in d, d.get('error');d['updated_at']=int(time.time());json.dump(d,open(sys.argv[1],'w'))" "$TOK"
  echo "gog_fetch: token written to $TOK"
  exit 0
fi
OUT=${1:?usage: tools/gog_fetch.sh <out dir> [work dir] | --code <url>}
OUT=$(mkdir -p "$OUT" && cd "$OUT" && pwd)
DL=${2:-$OUT.dl}; mkdir -p "$DL"; DL=$(cd "$DL" && pwd)
for d in "$OUT" "$DL"; do
  if top=$(git -C "$d" rev-parse --show-toplevel 2>/dev/null); then
    echo "gog_fetch: $d is inside the git checkout $top; game data must stay out of git" >&2; exit 2
  fi
done
[ -f "$TOK" ] || { echo "gog_fetch: no token at $TOK (see the header for --code)" >&2; exit 3; }

token() {
  python3 -I - "$TOK" "$CLIENT_ID" "$CLIENT_SECRET" <<'PY'
import json, sys, time, urllib.parse, urllib.request
p, cid, sec = sys.argv[1:4]
d = json.load(open(p))
if time.time() - d.get("updated_at", 0) > d.get("expires_in", 3600) - 300:
    q = urllib.parse.urlencode(dict(client_id=cid, client_secret=sec, grant_type="refresh_token",
                                    refresh_token=d["refresh_token"]))
    n = json.load(urllib.request.urlopen("https://auth.gog.com/token?" + q))
    n["updated_at"] = int(time.time())
    with open(p, "w") as f:
        json.dump(n, f)
    d = n
print(d["access_token"])
PY
}
api() { curl -sSf -H "Authorization: Bearer $(token)" "$1"; }

api "https://api.gog.com/products/$PRODUCT?expand=downloads" | python3 -I -c "
import json, sys
d = json.load(sys.stdin)
ins = [i for i in d['downloads']['installers'] if i['id'] == sys.argv[1]]
if not ins:
    sys.exit('gog_fetch: no installer ' + sys.argv[1] + ' (the account may not own the game)')
print('# ' + d['title'] + ' ' + ins[0]['version'], file=sys.stderr)
for f in ins[0]['files']:
    print(f['id'], f['size'], f['downlink'])" "$INSTALLER" > "$DL/files.txt"

if [ "${GOG_DRY_RUN:-0}" = 1 ]; then cut -d' ' -f1,2 "$DL/files.txt"; exit 0; fi
first=""
while read -r id size link; do
  name=""
  for try in 1 2 3 4 5 6 7 8; do
    api "$link" > "$DL/$id.json" || { sleep $((try * 5)); continue; }
    cdn=$(python3 -I -c "import json,sys;print(json.load(open(sys.argv[1]))['downlink'])" "$DL/$id.json")
    sum=$(python3 -I -c "import json,sys;print(json.load(open(sys.argv[1])).get('checksum',''))" "$DL/$id.json")
    name=$(python3 -I -c "import os,sys,urllib.parse as u;print(u.unquote(os.path.basename(sys.argv[1].split('?')[0])))" "$cdn")
    echo "$(date +%T) $id -> $name (about $size bytes), try $try"
    curl -sS -L -C - --retry 5 -o "$DL/$name" "$cdn" || { sleep $((try * 5)); continue; }
    want=""
    [ -n "$sum" ] && want=$(curl -sS -L "$sum" | python3 -I -c "import re,sys;m=re.search(r'md5=.([0-9a-f]{32}).',sys.stdin.read());print(m.group(1) if m else '')") || true
    got=$(md5sum "$DL/$name" | cut -c1-32)
    if [ -z "$want" ] || [ "$want" = "$got" ]; then
      echo "$id ok ($(stat -c %s "$DL/$name") bytes, md5 $got${want:+, matches GOG})"; break
    fi
    echo "$id: md5 $got, GOG says $want; downloading again"; rm -f "$DL/$name"; name=""
  done
  [ -n "$name" ] && [ -f "$DL/$name" ] || { echo "gog_fetch: $id failed" >&2; exit 1; }
  case "$name" in *.exe) first=${first:-$DL/$name} ;; esac
done < "$DL/files.txt"

[ -n "$first" ] || { echo "gog_fetch: no setup .exe among the files" >&2; exit 1; }
innoextract -s -d "$OUT" "$first"
exe=$(find "$OUT" -maxdepth 1 -iname FalloutNV.exe | head -1)
got=$(sha256sum "$exe" | cut -c1-64)
echo "gog_fetch: unpacked $(du -sh "$OUT" | cut -f1) into $OUT; FalloutNV.exe sha256 $got"
[ "${GOG_KEEP_INSTALLER:-1}" = 0 ] && rm -f "$DL"/*.exe "$DL"/*.bin
[ "$got" = "$EXE_SHA" ] || { echo "gog_fetch: FalloutNV.exe is not the expected build ($EXE_SHA)" >&2; exit 1; }
echo "gog_fetch: ok. Boot it with FARM_GAME=$OUT tests/s5/farm_boot.sh <c12> <out>"
