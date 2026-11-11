"""Tests of proof/formgate.py on synthetic fixtures (byte strings written here, nothing derived from game data)."""
import itertools
import json
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PROOF = os.path.join(HERE, '..')
sys.path.insert(0, os.path.join(PROOF, '..'))
from proof import formgate, shape

WORK = os.environ.get('FNVNX_TEST_WORK', '/home/user/l3work/proof/test_work')     # declared input: the directory the fixtures are written below
_SEQ = itertools.count()


class workdir:
    """a fresh empty directory WORK/formgate_<n> (n counts the uses in this process), removed on exit"""
    def __enter__(self):
        self.path = os.path.join(WORK, 'formgate_%d' % next(_SEQ))
        shutil.rmtree(self.path, ignore_errors=True)
        os.makedirs(self.path)
        return self.path

    def __exit__(self, *exc):
        shutil.rmtree(self.path)

MD = shape.decoder()
MODEL = 'e436ee6f79856a7ac9543400b7973dd1f0057410426f08b1e456c1d1c43e654a'
# encodings with a DSL builder (add / xor / mov / push / ret / nop) and one without (x87 fld st(1))
GOOD = ['01c8', '31c0', '8b4510', '6a05', 'c3', '90']
NOBUILD = ['d9c1']


def key_of(h):
    ins = list(MD.disasm(bytes.fromhex(h), 0))
    assert len(ins) == 1 and ins[0].size == len(h) // 2, h
    return shape.shape_key(ins[0]), shape.family(ins[0])


class Fixture:
    def __init__(self, td, encs, certs_for=None, dsl_override=None, model=MODEL, result='proved'):
        self.td = td
        os.makedirs(os.path.join(td, 'shapes', 'modA'))
        self.keys = sorted(key_of(h) for h in encs)
        lines = ['# fixture\n'] + ['%s\t%s\tfk\t1\t00001000\n' % (k, f) for k, f in self.keys]
        sp = os.path.join(td, 'shapes', 'modA', 'shapes.tsv')
        with open(sp, 'w') as fh:
            fh.writelines(lines)
        with open(os.path.join(td, 'shapes', 'KEYS.tsv'), 'w') as fh:
            fh.write('modA\t%s\t%s\t%s\t%s\n' % ('0' * 64, '1' * 64, '2' * 64, formgate.sha_file(sp)))
        rows = ['# shape_key\tfamily\tdsl_sha\tmodel_sha\treference\tresult\n']
        for k, f in self.keys:
            if certs_for is not None and k not in certs_for:
                continue
            d = formgate.dsl_sha(k, MD) or 'none'
            if dsl_override:
                d = dsl_override
            rows.append('%s\t%s\t%s\t%s\tsail\t%s\n' % (k, f, d, model, result))
        self.certs = os.path.join(td, 'certs.tsv')
        with open(self.certs, 'w') as fh:
            fh.writelines(rows)
        self.shapes = os.path.join(td, 'shapes')
        self.pins = os.path.join(td, 'pins.json')
        self.write_pins()

    def write_pins(self, certs_sha=None):
        with open(self.pins, 'w') as fh:
            json.dump({'certs': {'branch': 'b', 'path': 'certs.tsv', 'sha256': certs_sha or formgate.sha_file(self.certs)},
                       'model_sha256': MODEL,
                       'shapes': {'branch': 'b', 'path': 'KEYS.tsv', 'sha256': formgate.sha_file(os.path.join(self.shapes, 'KEYS.tsv'))}}, fh)


class FormGate(unittest.TestCase):
    def test_green(self):
        with workdir() as td:
            f = Fixture(td, GOOD)
            self.assertEqual(formgate.check(f.shapes, f.certs, f.pins), (len(GOOD), len(GOOD), []))

    def test_wrong_dsl_sha_is_red(self):
        with workdir() as td:
            f = Fixture(td, GOOD, dsl_override='0' * 64)
            f.write_pins()
            u, c, unc = formgate.check(f.shapes, f.certs, f.pins)
            self.assertEqual((u, c, len(unc)), (len(GOOD), 0, len(GOOD)))

    def test_missing_row_is_red(self):
        with workdir() as td:
            k0 = key_of(GOOD[0])[0]
            f = Fixture(td, GOOD, certs_for={key_of(h)[0] for h in GOOD[1:]})
            u, c, unc = formgate.check(f.shapes, f.certs, f.pins)
            self.assertEqual((u, c, unc), (len(GOOD), len(GOOD) - 1, [k0]))

    def test_wrong_model_or_result_is_red(self):
        with workdir() as td:
            f = Fixture(td, GOOD, model='f' * 64)
            self.assertEqual(formgate.check(f.shapes, f.certs, f.pins)[1], 0)
        with workdir() as td:
            f = Fixture(td, GOOD, result='failed')
            self.assertEqual(formgate.check(f.shapes, f.certs, f.pins)[1], 0)

    def test_shape_without_builder_is_uncertified(self):
        with workdir() as td:
            f = Fixture(td, GOOD + NOBUILD)
            u, c, unc = formgate.check(f.shapes, f.certs, f.pins)
            self.assertEqual((u, c, unc), (len(GOOD) + 1, len(GOOD), [key_of(NOBUILD[0])[0]]))

    def test_pin_mismatch_refuses(self):
        with workdir() as td:
            f = Fixture(td, GOOD)
            f.write_pins(certs_sha='0' * 64)
            with self.assertRaises(formgate.PinError):
                formgate.check(f.shapes, f.certs, f.pins)
            out = os.path.join(td, 'out')
            os.makedirs(out)
            r = subprocess.run([sys.executable, '-I', os.path.join(PROOF, 'formgate.py'), '--shapes', f.shapes, '--certs', f.certs, '--pins', f.pins],
                               capture_output=True, text=True, env={'GATE_OUT': out})
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn('REFUSED', r.stderr)
            self.assertFalse(os.path.exists(os.path.join(out, 'forms.json')))

    def test_shapes_tsv_tamper_refuses(self):
        with workdir() as td:
            f = Fixture(td, GOOD)
            with open(os.path.join(f.shapes, 'modA', 'shapes.tsv'), 'a') as fh:
                fh.write('c3/0.0.0.0\tint\tfk\t1\t00001000\n')
            with self.assertRaises(formgate.PinError):
                formgate.check(f.shapes, f.certs, f.pins)

    def test_cli_output_and_no_z3(self):
        """the gate run as the verdict runs it (python -I, GATE_OUT) with the z3 import blocked: green result file in the fnvnx-gate/1 format"""
        blocker = ("import sys\n"
                   "class B:\n"
                   "    def find_spec(self, name, path=None, target=None):\n"
                   "        if name.split('.')[0] == 'z3': raise ImportError('z3 is blocked')\n"
                   "sys.meta_path.insert(0, B())\n"
                   "import runpy\n"
                   "sys.argv = ['formgate.py'] + sys.argv[1:]\n"
                   "try:\n"
                   "    runpy.run_path(%r, run_name='__main__')\n"
                   "except SystemExit as e:\n"
                   "    assert not any(m.split('.')[0] == 'z3' for m in sys.modules)\n"
                   "    raise\n" % os.path.join(PROOF, 'formgate.py'))
        for encs, status, rc in ((GOOD, 'green', 0), (GOOD + NOBUILD, 'red', 1)):
            with workdir() as td:
                f = Fixture(td, encs)
                out = os.path.join(td, 'out')
                os.makedirs(out)
                r = subprocess.run([sys.executable, '-I', '-c', blocker, '--shapes', f.shapes, '--certs', f.certs, '--pins', f.pins],
                                   capture_output=True, text=True, env={'GATE_OUT': out})
                self.assertEqual(r.returncode, rc, r.stderr)
                with open(os.path.join(out, 'forms.json')) as fh:
                    txt = fh.read()
                d = json.loads(txt)
                self.assertEqual(sorted(d), ['certified', 'detail', 'format', 'name', 'status', 'uncertified', 'universe'])
                self.assertEqual((d['format'], d['name'], d['status'], d['universe']), ('fnvnx-gate/1', 'forms', status, len(encs)))
                self.assertEqual(txt, json.dumps(d, sort_keys=True, separators=(',', ':')) + '\n')


if __name__ == '__main__':
    unittest.main()
