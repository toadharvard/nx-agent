#!/usr/bin/env python3
"""Assemble the nx-agent SD bundle: a zip mirroring the SD card layout plus nx-agent-manifest.json."""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER_TID, RESCUE_TID = '420000000000D06B', '420000000000D06C'
KNOWN_HASHES = [
    '21da756c150edf6919fa1197ca1de74f7ca212f307bf5d3350fb1a50b7f75757',
    '9f5ff246a30b1698bd6653fec8236b56ffdfdb0533ed38288cefa87159970854',
    'b4bd77f9dfd7552db63cbd823790b04f7d28b565f5afef1d20c6eaf8148eac18',
    '0607dc22a40af76e1a281d6d0882ee43e3d25954cf76d51b3bfd85397826ea12',
    '10ad453df999cd8b2d9b78b2445f0174f600da2af6b8e67b94121c21b11e39f8',
    '4ab64fdc5d4799a80e12301d59b1874d869d1cbf7670ed9ef38a7b6a329447ae',
    '67ee1c3040c94c1ffbe850600103e9c02763d28db365814c8374e18889c69471',
    'b5f7d26e6e37e0b1ebe49fcf57898e832dd3a233f57ff021e166e91b10b1559f',
    'c59758cb6f2da71c227f99774e5e6fc79564eb973411706f403c91da75605c2b',
    'd769aba5c8faf67846d5c77f2532733ceff9cfd3126233d5be3f33875f3f0eab',
]


def layout(worker, rescue, hekate_bin, nyx_bin, template):
    files = {
        f'atmosphere/contents/{WORKER_TID}/exefs.nsp': worker,
        f'atmosphere/contents/{RESCUE_TID}/exefs.nsp': rescue,
        f'atmosphere/contents/{RESCUE_TID}/flags/boot2.flag': None,
        'config/usb-rescue/known-good.nsp': worker,
        'bootloader/payloads/Remote-Hekate.bin': hekate_bin,
        'bootloader/remote/nyx.bin': nyx_bin,
    }

    for path in sorted(Path(template).rglob('*')):
        if path.is_file():
            files[str(path.relative_to(template))] = path

    return files


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version', required=True)
    p.add_argument('--worker', default=ROOT / 'console/worker/worker.nsp', type=Path)
    p.add_argument('--rescue', default=ROOT / 'console/rescue/rescue.nsp', type=Path)
    p.add_argument('--hekate', default=ROOT / 'console/remote-hekate/build/Remote-Hekate.bin', type=Path)
    p.add_argument('--nyx', default=ROOT / 'console/remote-hekate/build/nyx.bin', type=Path)
    p.add_argument('--template', default=ROOT / 'console/remote-hekate/deploy-template', type=Path)
    p.add_argument('--out', default=Path('dist'), type=Path)
    a = p.parse_args()

    a.out.mkdir(parents=True, exist_ok=True)
    zip_path = a.out / f'nx-agent-sd-{a.version}.zip'
    manifest = {'version': a.version, 'protocol': 7, 'files': {}, 'known_hashes': KNOWN_HASHES}

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for rel, source in layout(a.worker, a.rescue, a.hekate, a.nyx, a.template).items():
            data = b'' if source is None else Path(source).read_bytes()
            info = zipfile.ZipInfo(rel, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
            manifest['files'][rel] = hashlib.sha256(data).hexdigest()

        archive.writestr(zipfile.ZipInfo('nx-agent-manifest.json', date_time=(2026, 1, 1, 0, 0, 0)),
                         json.dumps(manifest, indent=2, sort_keys=True))

    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (a.out / 'SHA256SUMS').write_text(f'{digest}  {zip_path.name}\n')
    print(zip_path, digest)


if __name__ == '__main__':
    main()
