#!/usr/bin/env bash
# setup_msvc.sh - install the S11 x86 oracle on a fresh Ubuntu 24.04 machine: Wine, and Visual C++ 2008 Express SP1
# (cl 15.00.30729.01) and RTM (cl 15.00.21022.08) unpacked from Microsoft's own ISOs, and Visual C++ 2005 SP1
# (cl 14.00.50727.762, "vc8") unpacked from the Windows SDK for Windows Vista (February 2007 Update) ISO, as the decomp
# README ("Compilers without Windows") describes.
#
#   toolchain/msvc/setup_msvc.sh <dest>     (idempotent; prints the environment variables to export)
#   toolchain/msvc/setup_msvc.sh --download <url> <out> <size> <sha256>     the resuming download alone (tests/s11)
#
# <dest>/msvc/sp1, <dest>/msvc/rtm, <dest>/msvc/vc8: VC/ (bin, include, lib) + SDK/ (v6.0A Include, Lib); <dest>/wineprefix
# (win32). vc8's VC/ is the Bin, INCLUDE and LIB of the SDK's WinSDKCompiler-x86.msi, with the VC 2005 runtime DLLs
# (msvcr80, msvcp80, msvcm80 and their manifest) in VC/bin/Microsoft.VC80.CRT as the private assembly cl.exe asks for;
# its SDK/ is a copy of sp1's.
# A toolchain already in <dest>/msvc (unpacked here before, or fetched by tools/farm/equiv_job.sh from the private
# artifacts repository) is used as it is: no download. Otherwise the ISOs come from download.microsoft.com into
# <dest>/dl, resumed across cut transfers and runs (curl -C -) until size and SHA-256 match, and are deleted after
# unpacking. For sp1 and rtm cl.exe / c1xx.dll / c2.dll must carry a valid Microsoft Authenticode signature
# (osslsigncode). The 2005 binaries carry none of their own: for vc8 the SDK's WinSDKCompiler-x86.msi and every cab
# unpacked from the ISO must carry a valid Microsoft Authenticode signature, and cl.exe / c1xx.dll / c2.dll must have
# the SHA-256 of the files that came out of those signed cabs. All three cl.exe must print their version under Wine.
# Needs root (apt) and about 4 GB of free disk while unpacking (about 770 MB kept). Nothing here goes into git.
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
                [rtm]=$MS/8/B/5/8B5804AD-4990-40D0-A6AA-CE894CBBB3DC/VS2008ExpressENUX1397868.iso
                [vc8]=$MS/4/2/6/42684501-9ec5-43dd-9dfe-c8c9dfa6a66f/6.1.6000.16384.10.WindowsSDK_Vista_Feb2007Update_rtm.DVD.Rel.iso)
declare -A SHA=([sp1]=6d3b5eedfa461969c5d645871b53002d162a2e238486900d2fdf71774d53fb91
                [rtm]=632318ef0df5bad58fcb99852bd251243610e7a4d84213c45b4f693605a13ead
                [vc8]=f544c8ec782921a64ffed4b41206aa3d6e16d0cf47c78a5bc26308ae0c2251ea)
declare -A SIZE=([sp1]=784902144 [rtm]=938108928 [vc8]=1237436416)
declare -A VER=([sp1]=15.00.30729.01 [rtm]=15.00.21022.08 [vc8]=14.00.50727.762)
declare -A VC8SHA=([cl.exe]=5f6489b0d211b14ae09d93d3fb58a7bce84d070287f008e06416c9baa5564219
                   [c1xx.dll]=391b9d99bfd3566922623b156e38b7e3f780f2a18fdaa440982ad09961297b0d
                   [c2.dll]=497d1ffe57f75db8b458167bdea475f3962829e139b355a2672a4ef51c716413)
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
v=vc8; R=$DEST/msvc/$v
if [ ! -f "$R/VC/bin/cl.exe" ] || [ ! -d "$R/SDK/Include" ] || [ ! -f "$R/VC/bin/Microsoft.VC80.CRT/msvcr80.dll" ]; then
  W=$DEST/unpack-$v; rm -rf "$W" "$R"; mkdir -p "$W" "$R"
  iso=$DEST/dl/$(basename "${URL[$v]}")
  download "${URL[$v]}" "$iso" "${SIZE[$v]}" "${SHA[$v]}" || { echo "setup_msvc: $v ISO download failed" >&2; exit 1; }
  7z x -y -o"$W/iso" "$iso" 'Setup/WinSDKCompiler*' Setup/WinSDK-WinSDK_BIN_VC8_Runtime_X86_CRT-common.0.cab >/dev/null
  rm -f "$iso"
  for f in "$W"/iso/Setup/*.msi "$W"/iso/Setup/*.cab; do   # the packages the compiler files come out of
    osslsigncode verify -in "$f" 2>&1 | tail -1 | grep -q Succeeded \
      || { echo "setup_msvc: $v $(basename "$f"): no valid signature" >&2; exit 1; }
  done
  (cd "$W/iso/Setup" && msiextract -C ../../cx WinSDKCompiler-x86.msi >/dev/null)
  C="$W/cx/SOURCEDIR/Program Files/Microsoft SDKs:./Windows:./v6.0:./VC"
  mkdir -p "$R/VC"; cp -r "$C/Bin" "$R/VC/bin"; cp -r "$C/INCLUDE" "$R/VC/include"; cp -r "$C/LIB" "$R/VC/lib"
  cabextract -q -d "$W/crt" "$W/iso/Setup/WinSDK-WinSDK_BIN_VC8_Runtime_X86_CRT-common.0.cab"
  A=$R/VC/bin/Microsoft.VC80.CRT; mkdir -p "$A"
  for f in msvcr80 msvcp80 msvcm80; do cp "$W/crt/$f.dll.8.0.50727.762.98CB24AD_52FB_DB5F_FF1F_C8B3B9A1E18E" "$A/$f.dll"; done
  cp "$W/crt/manifest.8.0.50727.762.98CB24AD_52FB_DB5F_FF1F_C8B3B9A1E18E" "$A/Microsoft.VC80.CRT.manifest"
  [ -d "$DEST/msvc/sp1/SDK/Include" ] || { echo "setup_msvc: $v needs the sp1 SDK" >&2; exit 1; }
  cp -r "$DEST/msvc/sp1/SDK" "$R/SDK"
  rm -rf "$W"
fi
for f in "${!VC8SHA[@]}"; do   # the compiler binaries are the ones from the signed cabs
  echo "${VC8SHA[$f]}  $DEST/msvc/vc8/VC/bin/$f" | sha256sum -c --quiet - >/dev/null 2>&1 \
    || { echo "setup_msvc: vc8 $f: not the file from Microsoft's signed cab" >&2; exit 1; }
done
for v in sp1 rtm; do   # unpacked here or fetched: Microsoft's signatures on the compiler binaries
  for b in cl.exe c1xx.dll c2.dll; do
    osslsigncode verify -in "$DEST/msvc/$v/VC/bin/$b" 2>&1 | tail -1 | grep -q Succeeded \
      || { echo "setup_msvc: $v $b: no valid signature" >&2; exit 1; }
  done
done
[ -d "$WINEPREFIX/drive_c" ] || timeout 600 wineboot -i >/dev/null 2>&1 || true
for v in sp1 rtm vc8; do
  wine "$DEST/msvc/$v/VC/bin/cl.exe" 2>&1 | grep -q "Version ${VER[$v]} for 80x86" \
    || { echo "setup_msvc: $v cl.exe does not run under Wine" >&2; exit 1; }
done
echo "export FNV_MSVC9_ROOT=$DEST/msvc/sp1 FNV_MSVC9_RTM_ROOT=$DEST/msvc/rtm FNV_MSVC8_ROOT=$DEST/msvc/vc8 FNV_MSVC9_PREFIX=$DEST/wineprefix"
