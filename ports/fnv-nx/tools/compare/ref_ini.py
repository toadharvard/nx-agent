#!/usr/bin/env python3
"""ref_ini.py - the ini files of a comparison run (S16-compare), the same for the reference and our port.

    ref_ini.py <Fallout_default.ini> <My Games/FalloutNV dir> [--width W --height H] [--print]

Writes Fallout.ini (the player's Fallout_default.ini with the overrides below) and FalloutPrefs.ini (only the
overrides). The overrides take randomness and wall-clock out of the frame where an ini can: no intro movies,
windowed at a fixed size, no vsync, no MSAA, no audio, AI and other work on the main thread, the game keeps running
without input focus. The same function is used to write the ini our guest reads, so both sides start equal.
"""
import argparse, hashlib, os, re, sys

# The game compares FalloutPrefs.ini's sD3DDevice with the adapter's description at start. When they differ it
# resets Fallout.ini to the default and starts FalloutNVLauncher.exe. wine_ref.sh pins the adapter Wine reports
# (registry VideoPciVendorID/VideoPciDeviceID = NVIDIA GeForce GTX 470), so the name is fixed too.
D3D_DEVICE = '"NVIDIA GeForce GTX 470"'

def overrides(w, h, dev=D3D_DEVICE):
    return {
        'General': {
            'bAlwaysActive': '1', 'SIntroSequence': '', 'sIntroMovie': '', 'SMainMenuMovieIntro': '',
            'bPreloadIntroSequence': '0', 'iIntroSequencePriority': '0', 'fStaticScreenWaitTime': '0.0000',
            'bUseThreadedAI': '0', 'bUseThreadedTempEffects': '0', 'bUseThreadedParticleSystem': '0',
            'bUseThreadedBlood': '0', 'bUseThreadedMorpher': '0', 'bUseThreadedTextures': '0',
            'bUseThreadedMeshes': '0', 'bUseThreadedLODNodes': '0', 'bBackgroundLoadLipFiles': '0',
            'bUseBackgroundPathing': '0', 'iNumHWThreads': '1', 'sLanguage': 'ENGLISH',
        },
        'Display': {
            'bFull Screen': '0', 'iSize W': str(w), 'iSize H': str(h), 'iPresentInterval': '0',
            'iMultiSample': '0', 'iMaxAnisotropy': '0', 'fDefaultFOV': '75.0000', 'bAllowScreenShot': '1',
            'bDoDepthOfField': '0', 'bDo30Fade': '1', 'iAdapter': '0', 'bShowMenuTextureUse': '0',
            'sD3DDevice': dev,
        },
        'Audio': {'bEnableAudio': '0', 'bMultiThreadAudio': '0', 'bMusicEnabled': '0'},
        'BackgroundLoad': {'bBackgroundLoadLipFiles': '0', 'bUseMultiThreadedFaceGen': '0',
                           'bUseMultiThreadedTrees': '0', 'bBackgroundCellLoads': '0', 'bLoadHelmetsInBackground': '0'},
        'Interface': {'bUseFuzzyPicking': '0'},
        'GamePlay': {'bDisableDynamicCrosshair': '0'},
        'Controls': {'bBackground Mouse': '0'},
        'Launcher': {'bEnableFileSelection': '1', 'bShowAllResolutions': '1'},
    }

SEC = re.compile(r'^\s*\[([^\]]+)\]\s*$')

def apply(text, ov):
    """text with every override applied: existing keys replaced in place, missing ones added at the section end"""
    out, sec, done = [], None, set()
    def flush(s):
        for k, v in ov.get(s, {}).items():
            if (s, k.lower()) not in done:
                out.append(f'{k}={v}'); done.add((s, k.lower()))
    for line in text.splitlines():
        m = SEC.match(line)
        if m:
            if sec is not None: flush(sec)
            sec = m.group(1); out.append(line); continue
        if sec is not None and '=' in line and not line.lstrip().startswith(';'):
            k = line.split('=', 1)[0].strip()
            for ok, v in ov.get(sec, {}).items():
                if ok.lower() == k.lower():
                    line = f'{k}={v}'; done.add((sec, ok.lower())); break
        out.append(line)
    if sec is not None: flush(sec)
    for s in ov:
        if not any(SEC.match(l) and SEC.match(l).group(1) == s for l in out):
            out.append(f'[{s}]'); flush(s)
    return '\r\n'.join(out) + '\r\n'

def prefs(ov):
    return ''.join(f'[{s}]\r\n' + ''.join(f'{k}={v}\r\n' for k, v in kv.items()) for s, kv in ov.items())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('default_ini'); ap.add_argument('out_dir')
    ap.add_argument('--width', type=int, default=1280); ap.add_argument('--height', type=int, default=720)
    ap.add_argument('--d3d-device', default=D3D_DEVICE)
    a = ap.parse_args()
    ov = overrides(a.width, a.height, a.d3d_device)
    text = open(a.default_ini, encoding='latin-1').read()
    os.makedirs(a.out_dir, exist_ok=True)
    ini, pref = apply(text, ov), prefs(ov)
    for f in ('Fallout.ini', 'FalloutPrefs.ini'):   # the game leaves its reset copy read-only
        fp = os.path.join(a.out_dir, f)
        if os.path.exists(fp): os.chmod(fp, 0o644)
    open(os.path.join(a.out_dir, 'Fallout.ini'), 'w', encoding='latin-1', newline='').write(ini)
    open(os.path.join(a.out_dir, 'FalloutPrefs.ini'), 'w', encoding='latin-1', newline='').write(pref)
    print('ini sha256', hashlib.sha256((ini + pref).encode('latin-1')).hexdigest()[:16])

if __name__ == '__main__':
    sys.exit(main())
