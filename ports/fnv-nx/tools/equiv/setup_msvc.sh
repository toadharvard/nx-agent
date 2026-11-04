#!/usr/bin/env bash
# setup_msvc.sh - install the S11 x86 oracle on a fresh Ubuntu 24.04 machine: Wine, and Visual C++ 2008 Express SP1
# (cl 15.00.30729.01) and RTM (cl 15.00.21022.08) unpacked from Microsoft's own ISOs, as the decomp README
# ("Compilers without Windows") describes.
#
#   tools/equiv/setup_msvc.sh <dest>        (idempotent; prints the three environment variables to export)
#   tools/equiv/setup_msvc.sh --download <url> <out> <size> <sha256>     the resuming download alone (tests/s11)
#
# <dest>/msvc/sp1, <dest>/msvc/rtm: VC/ (bin, include, lib) + SDK/ (v6.0A Include, Lib); <dest>/wineprefix (win32).
# A toolchain already in <dest>/msvc (unpacked here before, or fetched by tools/farm/equiv_job.sh from the private
# artifacts repository) is used as it is: no download. Otherwise the ISOs come from download.microsoft.com into
# <dest>/dl, resumed across cut transfers and runs (curl -C -) until size and SHA-256 match, and are deleted after
# unpacking. cl.exe / c1xx.dll / c2.dll must carry a valid Microsoft Authenticode signature (osslsigncode), and both
# cl.exe must print their version under Wine. Needs root (apt) and about 4 GB of free disk while unpacking (440 MB
# kept). Nothing here goes into git.
# Download tuning (environment): DL_PAUSE (10 s between attempts), DL_STALLS (20 attempts in a row without progress
# end it), DL_RETRY (0: curl's own --retry restarts an attempt from that attempt's offset, so a cut transfer would fetch
# the same range again; the loop here resumes from the bytes on disk instead).
set -euo pipefail

download() {  # <url> <out> <size> <sha256>: resume until the file has the size and the checksum
  local url=$1 out=$2 size=$3 sha=$4 have last=-1 stall=0 bad=0 rc name
  name=$(basename "$out"); mkdir -p "$(dirname "$out")"
  while :; do
    have=$(stat -c %s "$out" 2>/dev/null || echo 0)
    if [ "$have" -gt "$size" ]; then rm -f "$out"; have=0; fi
    if [ "$have" -eq "$size" ]; then
      echo "$sha  $out" | sha256sum -c --quiet - >/dev/null 2>&1 && return 0
      bad=$((bad + 1)); rm -f "$out"; have=0; last=-1
      echo "setup_msvc: $name: SHA-256 mismatch at full size ($bad), downloading again" >&2
      [ "$bad" -lt 2 ] || { echo "setup_msvc: $name: checksum mismatch twice" >&2; return 1; }
    fi
    if [ "$have" -gt "$last" ]; then stall=0; else stall=$((stall + 1)); fi
    [ "$stall" -lt "${DL_STALLS:-20}" ] \
      || { echo "setup_msvc: $name: no progress in $stall attempts ($have of $size bytes)" >&2; return 1; }
    last=$have
    rc=0
    curl -sSL --retry "${DL_RETRY:-0}" --retry-delay 5 --retry-all-errors --connect-timeout 60 \
      --speed-limit 4096 --speed-time 120 -C - -o "$out" "$url" || rc=$?
    case $rc in
      0) ;;
      33) rm -f "$out" ;;   # the server ignored the range: start over
      *) echo "setup_msvc: $name: curl exit $rc at $(stat -c %s "$out" 2>/dev/null || echo 0) of $size bytes, resuming" >&2
         sleep "${DL_PAUSE:-10}" ;;
    esac
  done
}

if [ "${1:-}" = --download ]; then
  download "${2:?url}" "${3:?out}" "${4:?size}" "${5:?sha256}"; exit
fi

DEST=$(mkdir -p "${1:?dest}" && cd "$1" && pwd)
MS=https://download.microsoft.com/download
declare -A URL=([sp1]=$MS/E/8/E/E8EEB394-7F42-4963-A2D8-29559B738298/VS2008ExpressWithSP1ENUX1504728.iso
                [rtm]=$MS/8/B/5/8B5804AD-4990-40D0-A6AA-CE894CBBB3DC/VS2008ExpressENUX1397868.iso)
declare -A SHA=([sp1]=6d3b5eedfa461969c5d645871b53002d162a2e238486900d2fdf71774d53fb91
                [rtm]=632318ef0df5bad58fcb99852bd251243610e7a4d84213c45b4f693605a13ead)
declare -A SIZE=([sp1]=784902144 [rtm]=938108928)
declare -A VER=([sp1]=15.00.30729.01 [rtm]=15.00.21022.08)
SUDO=; [ "$(id -u)" = 0 ] || SUDO=sudo

if ! command -v wine >/dev/null || ! command -v 7z >/dev/null || ! command -v cabextract >/dev/null \
    || ! command -v msiextract >/dev/null || ! command -v osslsigncode >/dev/null; then
  $SUDO dpkg --add-architecture i386
  $SUDO apt-get update -q >/dev/null
  DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -q wine wine32:i386 wine64 p7zip-full cabextract msitools \
    osslsigncode >/dev/null
fi

export WINEPREFIX=$DEST/wineprefix WINEDEBUG=-all WINEARCH=win32
for v in sp1 rtm; do
  R=$DEST/msvc/$v
  if [ -f "$R/VC/bin/cl.exe" ] && [ -d "$R/SDK/Include" ]; then continue; fi
  W=$DEST/unpack-$v; rm -rf "$W" "$R"; mkdir -p "$W" "$R"
  iso=$DEST/dl/$(basename "${URL[$v]}")
  download "${URL[$v]}" "$iso" "${SIZE[$v]}" "${SHA[$v]}" || { echo "setup_msvc: $v ISO download failed" >&2; exit 1; }
  7z x -y -o"$W/tc" "$iso" VCExpress >/dev/null
  rm -f "$iso"
  cabextract -q -d "$W/ixp" "$W/tc/VCExpress/Ixpvc.exe"
  (cd "$W/ixp" && msiextract -C ../vcx vs_setup.msi >/dev/null)
  V="$W/vcx/Program Files/Microsoft Visual Studio 9.0"
  cp -r "$V/VC:Vc7" "$R/VC"
  for f in mspdb80.dll msobj80.dll mspdbcore.dll mspdbsrv.exe; do find "$V/Common7" -iname "$f" -exec cp {} "$R/VC/bin/" \; ; done
  cabextract -q -d "$W/sdkx" "$W/tc/VCExpress/WCU/WinSDK/WinSDK_Build.exe"
  (cd "$W/sdkx" && msiextract -C ../sdko VistaClientHeadersLibs-x86.msi >/dev/null)
  SD="$W/sdko/SOURCEDIR/Program Files/Microsoft SDKs:./Windows:./v6.0A:."
  mkdir -p "$R/SDK"; cp -r "$SD/Include" "$R/SDK/Include"; cp -r "$SD/Lib" "$R/SDK/Lib"
  rm -rf "$W"
done
for v in sp1 rtm; do   # unpacked here or fetched: Microsoft's signatures on the compiler binaries
  for b in cl.exe c1xx.dll c2.dll; do
    osslsigncode verify -in "$DEST/msvc/$v/VC/bin/$b" 2>&1 | tail -1 | grep -q Succeeded \
      || { echo "setup_msvc: $v $b: no valid signature" >&2; exit 1; }
  done
done
[ -d "$WINEPREFIX/drive_c" ] || timeout 600 wineboot -i >/dev/null 2>&1 || true
for v in sp1 rtm; do
  wine "$DEST/msvc/$v/VC/bin/cl.exe" 2>&1 | grep -q "Version ${VER[$v]} for 80x86" \
    || { echo "setup_msvc: $v cl.exe does not run under Wine" >&2; exit 1; }
done
echo "export FNV_MSVC9_ROOT=$DEST/msvc/sp1 FNV_MSVC9_RTM_ROOT=$DEST/msvc/rtm FNV_MSVC9_PREFIX=$DEST/wineprefix"
