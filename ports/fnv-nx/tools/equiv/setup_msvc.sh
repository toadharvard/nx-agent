#!/usr/bin/env bash
# setup_msvc.sh - install the S11 x86 oracle on a fresh Ubuntu 24.04 machine: Wine, and Visual C++ 2008 Express SP1
# (cl 15.00.30729.01) and RTM (cl 15.00.21022.08) unpacked from Microsoft's own ISOs, as the decomp README
# ("Compilers without Windows") describes.
#
#   tools/equiv/setup_msvc.sh <dest>        (idempotent; prints the three environment variables to export)
#
# <dest>/msvc/sp1, <dest>/msvc/rtm: VC/ (bin, include, lib) + SDK/ (v6.0A Include, Lib); <dest>/wineprefix (win32).
# The ISOs come from download.microsoft.com, are checked against the SHA-256 below and deleted after unpacking.
# Needs root (apt) and about 4 GB of free disk while unpacking (550 MB kept). Nothing here goes into git.
set -euo pipefail
DEST=$(mkdir -p "${1:?dest}" && cd "$1" && pwd)
MS=https://download.microsoft.com/download
declare -A URL=([sp1]=$MS/E/8/E/E8EEB394-7F42-4963-A2D8-29559B738298/VS2008ExpressWithSP1ENUX1504728.iso
                [rtm]=$MS/8/B/5/8B5804AD-4990-40D0-A6AA-CE894CBBB3DC/VS2008ExpressENUX1397868.iso)
declare -A SHA=([sp1]=6d3b5eedfa461969c5d645871b53002d162a2e238486900d2fdf71774d53fb91
                [rtm]=632318ef0df5bad58fcb99852bd251243610e7a4d84213c45b4f693605a13ead)
declare -A VER=([sp1]=15.00.30729.01 [rtm]=15.00.21022.08)
SUDO=; [ "$(id -u)" = 0 ] || SUDO=sudo

if ! command -v wine >/dev/null || ! command -v 7z >/dev/null || ! command -v cabextract >/dev/null \
    || ! command -v msiextract >/dev/null; then
  $SUDO dpkg --add-architecture i386
  $SUDO apt-get update -q >/dev/null
  DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -q wine wine32:i386 wine64 p7zip-full cabextract msitools >/dev/null
fi

export WINEPREFIX=$DEST/wineprefix WINEDEBUG=-all WINEARCH=win32
for v in sp1 rtm; do
  R=$DEST/msvc/$v
  if [ -f "$R/VC/bin/cl.exe" ] && [ -d "$R/SDK/Include" ]; then continue; fi
  W=$DEST/unpack-$v; rm -rf "$W" "$R"; mkdir -p "$W/dl" "$R"
  iso=$W/dl/$(basename "${URL[$v]}")
  curl -sSL --retry 5 --retry-all-errors -o "$iso" "${URL[$v]}"
  echo "${SHA[$v]}  $iso" | sha256sum -c --quiet - || { echo "setup_msvc: $v ISO checksum mismatch" >&2; exit 1; }
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
  if command -v osslsigncode >/dev/null; then
    for b in cl.exe c1xx.dll c2.dll; do
      osslsigncode verify -in "$R/VC/bin/$b" 2>&1 | tail -1 | grep -q Succeeded || { echo "setup_msvc: $v $b signature" >&2; exit 1; }
    done
  fi
  rm -rf "$W"
done
[ -d "$WINEPREFIX/drive_c" ] || timeout 600 wineboot -i >/dev/null 2>&1 || true
for v in sp1 rtm; do
  wine "$DEST/msvc/$v/VC/bin/cl.exe" 2>&1 | grep -q "Version ${VER[$v]} for 80x86" \
    || { echo "setup_msvc: $v cl.exe does not run under Wine" >&2; exit 1; }
done
echo "export FNV_MSVC9_ROOT=$DEST/msvc/sp1 FNV_MSVC9_RTM_ROOT=$DEST/msvc/rtm FNV_MSVC9_PREFIX=$DEST/wineprefix"
