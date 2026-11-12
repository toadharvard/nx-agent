"""abi_gaterun - run a gate's main() in-process with a scratch GATE_OUT and return (exit status, result dict).  The scene's exe is
bytes the test built, so it is handed to the gate as an Image parsed from those bytes (exefacts.Image.parse), not read as the
player's exe: the pin (exepin.load_exe) guards the player's exe only."""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'contract'))
import exefacts  # noqa: E402


def run(mod, argv, name):
    out = tempfile.mkdtemp()
    old = os.environ.get('GATE_OUT')
    os.environ['GATE_OUT'] = out
    try:
        img = None
        if '--exe' in argv:
            with open(argv[argv.index('--exe') + 1], 'rb') as fh:
                img = exefacts.Image.parse(fh.read())
        rc = mod.main(argv) if img is None else mod.main(argv, image=img)
    finally:
        if old is None:
            del os.environ['GATE_OUT']
        else:
            os.environ['GATE_OUT'] = old
    try:
        with open(os.path.join(out, name + '.json')) as fh:
            return rc, json.load(fh)
    finally:
        shutil.rmtree(out, True)
