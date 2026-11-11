"""abi_gaterun - run a gate's main() in-process with a scratch GATE_OUT and return (exit status, result dict)."""
import json
import os
import tempfile


def run(mod, argv, name):
    out = tempfile.mkdtemp()
    old = os.environ.get('GATE_OUT')
    os.environ['GATE_OUT'] = out
    try:
        rc = mod.main(argv)
    finally:
        if old is None:
            del os.environ['GATE_OUT']
        else:
            os.environ['GATE_OUT'] = old
    with open(os.path.join(out, name + '.json')) as fh:
        return rc, json.load(fh)
