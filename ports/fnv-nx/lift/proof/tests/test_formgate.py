"""Tests of proof/formgate.py: the inputs contract (externals fetched by the trunk's loader from local bare repos, no network), every red reason,
the certification rule on synthetic encodings (byte strings written here, nothing derived from game data)."""
import itertools
import json
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PROOF = os.path.join(HERE, '..')
LIFT = os.path.join(PROOF, '..')
sys.path.insert(0, HERE)
sys.path.insert(0, LIFT)
import fakeext
from proof import formgate, shape
import recipe

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


def skip_cache(d, names):
    return [n for n in names if n in ('__pycache__', 'tests')]


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
    """one fake artifacts repo with externals liftpack (shapes.tsv) and certs (certs.tsv), the inputs file and a sail_pins file"""
    def __init__(self, td, encs, certs_for=None, dsl_override=None, model=MODEL, result='proved', pin=MODEL, shapes_text=None, declare=('liftpack', 'certs'),
                 shapes_file='shapes.tsv', cert_rows=None):
        self.td = td
        self.keys = sorted(set(key_of(h) for h in encs))
        shapes = shapes_text if shapes_text is not None else '# fixture\n' + ''.join('%s\t%s\t1\n' % kf for kf in self.keys)
        rows = ['# shape_key\tfamily\tdsl_sha\tmodel_sha\treference\tresult\n']
        for k, f in self.keys:
            if certs_for is not None and k not in certs_for:
                continue
            rows.append('%s\t%s\t%s\t%s\tsail\t%s\n' % (k, f, dsl_override or formgate.dsl_sha(k, MD) or 'none', model, result))
        self.art = fakeext.Artifacts(os.path.join(td, 'art'))
        os.makedirs(os.path.join(td, 'art'), exist_ok=True)
        self.art = fakeext.Artifacts(os.path.join(td, 'art'))
        self.ext = {'liftpack': self.art.publish('liftpack', {shapes_file: shapes, 'README': 'pack\n'}),
                    'certs': self.art.publish('certs', {'certs.tsv': ''.join(cert_rows if cert_rows is not None else rows)})}
        self.inputs = os.path.join(td, 'inputs.json')
        self.pins = os.path.join(td, 'sail_pins.txt')
        self.write(pin=pin, declare=declare)
        self.cache = os.path.join(td, 'cache')
        os.makedirs(self.cache)

    def write(self, pin=MODEL, declare=('liftpack', 'certs'), **over):
        doc = {'format': 'fnvnx-forms-inputs/1', 'external': [self.ext[n] for n in declare], 'shapes': 'liftpack/shapes.tsv',
               'certs': 'certs/certs.tsv', 'model_sha256': MODEL}
        doc.update(over)
        with open(self.inputs, 'w') as fh:
            json.dump(doc, fh)
        with open(self.pins, 'w') as fh:
            fh.write('# pins\nz3-solver  -  5  %s\nx86_proof.ir  -  -  %s\n' % ('0' * 64, pin))

    def judge(self):
        with fakeext.use_artifacts(self.art):
            return formgate.judge(self.cache, self.inputs, self.pins)


class Green(unittest.TestCase):
    def test_green_and_detail(self):
        with workdir() as td:
            f = Fixture(td, GOOD)
            u, c, ids, detail = f.judge()
            self.assertEqual((u, c, ids), (len(GOOD), len(GOOD), []))
            self.assertIn('int %d/%d' % (len(GOOD), len(GOOD)), detail)

    def test_ext_keys_are_the_loaders(self):
        self.assertEqual(formgate.EXT_KEYS, recipe.EXT_KEYS)

    def test_result_file_accepted_by_the_trunk_reader(self):
        for encs, status in ((GOOD, 'green'), (GOOD + NOBUILD, 'red')):
            with workdir() as td:
                f = Fixture(td, encs)
                out = os.path.join(td, 'out')
                os.makedirs(out)
                env = dict(os.environ, GATE_OUT=out, GATE_CACHE=f.cache)
                old = os.environ.get('GATE_OUT')
                os.environ['GATE_OUT'] = out
                try:
                    u, c, ids, detail = f.judge()
                    formgate.gatefile.write('forms', u, c, ids, detail)
                finally:
                    if old is None:
                        del os.environ['GATE_OUT']
                    else:
                        os.environ['GATE_OUT'] = old
                st = fakeext.accepted('forms', out, 0 if status == 'green' else 2)
                self.assertEqual(st[0], status, st)
                with open(os.path.join(out, 'forms.json')) as fh:
                    txt = fh.read()
                d = json.loads(txt)
                self.assertEqual(sorted(d), ['certified', 'detail', 'format', 'name', 'status', 'uncertified', 'universe'])
                self.assertEqual(txt, json.dumps(d, sort_keys=True, separators=(',', ':')) + '\n')


class CertificationRule(unittest.TestCase):
    def test_missing_row_is_red_and_named(self):
        with workdir() as td:
            k0 = key_of(GOOD[0])[0]
            f = Fixture(td, GOOD, certs_for={key_of(h)[0] for h in GOOD[1:]})
            self.assertEqual(f.judge()[:3], (len(GOOD), len(GOOD) - 1, [k0]))

    def test_wrong_dsl_sha_is_red(self):
        with workdir() as td:
            u, c, ids, _ = Fixture(td, GOOD, dsl_override='0' * 64).judge()
            self.assertEqual((u, c, len(ids)), (len(GOOD), 0, len(GOOD)))

    def test_certificate_stale_after_a_dsl_edit_is_refused(self):
        # certificates written under the current DSL; then the builder of ONE mnemonic (xor) changes: exactly the shapes of xor lose their certificate,
        # the others (their Form did not change) stay certified.  The key of a certificate is (shape, shape_sha of the CURRENT Form, IR sha), nothing else.
        from proof import forms
        with workdir() as td:
            f = Fixture(td, GOOD)
            self.assertEqual(f.judge()[:3], (len(GOOD), len(GOOD), []))
            orig = forms.H['xor']

            def edited(c):
                orig(c)
                c.F.undefined('df', 'edited')
            forms.H['xor'] = edited
            try:
                u, c, ids, _ = f.judge()
            finally:
                forms.H['xor'] = orig
            self.assertEqual((u, c, ids), (len(GOOD), len(GOOD) - 1, [key_of('31c0')[0]]))
            self.assertEqual(f.judge()[:3], (len(GOOD), len(GOOD), []))

    def test_wrong_model_sha_in_the_certificates_is_red(self):
        with workdir() as td:
            self.assertEqual(Fixture(td, GOOD, model='f' * 64).judge()[1], 0)

    def test_result_not_proved_is_red(self):
        for res in ('failed', 'model-gap', 'PROVED'):
            with workdir() as td:
                self.assertEqual(Fixture(td, GOOD, result=res).judge()[1], 0, res)

    def test_shape_without_builder_is_uncertified(self):
        with workdir() as td:
            u, c, ids, detail = Fixture(td, GOOD + NOBUILD).judge()
            self.assertEqual((u, c, ids), (len(GOOD) + 1, len(GOOD), [key_of(NOBUILD[0])[0]]))
            self.assertIn('without a DSL builder: 1', detail)

    def test_uncertified_ids_are_sorted(self):
        with workdir() as td:
            ids = Fixture(td, GOOD, dsl_override='0' * 64).judge()[2]
            self.assertEqual(ids, sorted(ids))


class InputsRed(unittest.TestCase):
    """every red reason: item `inputs`, universe 1, certified 0, the reason in detail"""
    def red(self, f, text):
        u, c, ids, detail = f.judge()
        self.assertEqual((u, c, ids), (1, 0, ['inputs']), detail)
        self.assertIn(text, detail)

    def test_no_inputs_file(self):
        with workdir() as td:
            f = Fixture(td, GOOD)
            os.remove(f.inputs)
            self.red(f, 'no inputs.json')

    def test_not_json_wrong_format_wrong_keys(self):
        with workdir() as td:
            f = Fixture(td, GOOD)
            with open(f.inputs, 'w') as fh:
                fh.write('{')
            self.red(f, 'not JSON')
            f.write(format='other/1')
            self.red(f, 'format is not')
            f.write(extra=1)
            self.red(f, 'keys must be exactly')

    def test_external_not_declared(self):
        with workdir() as td:
            f = Fixture(td, GOOD, declare=('certs',))
            self.red(f, 'inputs: external liftpack not declared')

    def test_bad_external_entry_and_path(self):
        with workdir() as td:
            f = Fixture(td, GOOD)
            f.write(shapes='liftpack/../x')
            self.red(f, 'NAME/path')
            f.write(external=[dict(f.ext['liftpack'], commit='abc'), f.ext['certs']])
            self.red(f, 'bad name, repo, commit')

    def test_hash_mismatch(self):
        with workdir() as td:
            f = Fixture(td, GOOD)
            f.write(external=[dict(f.ext['liftpack'], tree_sha256='0' * 64), f.ext['certs']])
            self.red(f, 'tree_sha256 is')

    def test_external_does_not_fetch(self):
        with workdir() as td:
            f = Fixture(td, GOOD)
            f.write(external=[dict(f.ext['liftpack'], commit='1' * 40), f.ext['certs']])
            self.red(f, 'does not fetch')

    def test_shapes_tsv_missing(self):
        with workdir() as td:
            f = Fixture(td, GOOD, shapes_file='other.tsv')
            self.red(f, 'shapes: shapes.tsv is missing from external liftpack')

    def test_certs_tsv_missing(self):
        with workdir() as td:
            f = Fixture(td, GOOD)
            f.write(certs='certs/none.tsv')
            self.red(f, 'certs: none.tsv is missing')

    def test_model_pin_differs_from_sail_pins(self):
        with workdir() as td:
            f = Fixture(td, GOOD, pin='a' * 64)
            self.red(f, 'differs from the x86_proof.ir pin')

    def test_model_sha_not_a_sha(self):
        with workdir() as td:
            f = Fixture(td, GOOD)
            f.write(model_sha256='zz')
            self.red(f, 'not a SHA-256')

    def test_shapes_malformed(self):
        a, b = key_of(GOOD[0]), key_of(GOOD[1])
        lo, hi = sorted([a, b])
        for text, why in (('%s\tint\n' % lo[0], 'bad row'), ('%s\tfoo\t1\n' % lo[0], 'bad row'), ('', 'no shapes'),
                          ('%s\tint\t1\n%s\tint\t1\n' % (hi[0], lo[0]), 'not sorted'), ('%s\tint\t1\n%s\tint\t1\n' % (lo[0], lo[0]), 'not sorted')):
            with workdir() as td:
                self.red(Fixture(td, GOOD, shapes_text=text), why)

    def test_certs_malformed(self):
        with workdir() as td:
            self.red(Fixture(td, GOOD, cert_rows=['a\tb\n']), 'certs.tsv: bad row')

    def test_committed_inputs_are_valid_and_red_for_the_pack(self):
        """the committed file: loads against the real sail_pins.txt up to the missing liftpack; certs external is the pinned one"""
        with open(formgate.INPUTS) as fh:
            doc = json.load(fh)
        self.assertEqual(doc['model_sha256'], formgate.model_pin())
        self.assertEqual([e['name'] for e in doc['external']], ['certs'])
        with workdir() as td:
            u, c, ids, detail = formgate.judge(td)
        self.assertEqual((u, c, ids, detail), (1, 0, ['inputs'], 'inputs: external liftpack not declared'))


class Cli(unittest.TestCase):
    """the gate as the verdict runs it: python -I, cwd = checkout root, GATE_OUT; the checkout is a copy of lift/ beside the overlay of verify/"""
    def run_gate(self, td, f):
        co = os.path.join(td, 'co')
        os.makedirs(os.path.join(co, 'ports', 'fnv-nx'))
        shutil.copytree(LIFT, os.path.join(co, 'ports', 'fnv-nx', 'lift'), ignore=skip_cache, copy_function=shutil.copyfile)
        os.symlink(os.path.abspath(fakeext.VERIFY), os.path.join(co, 'ports', 'fnv-nx', 'verify'))
        os.symlink(os.path.abspath(os.path.join(fakeext.VERIFY, '..', 'contract')), os.path.join(co, 'ports', 'fnv-nx', 'contract'))
        shutil.copyfile(f.inputs, os.path.join(co, 'ports', 'fnv-nx', 'lift', 'proof', 'formgate_inputs.json'))
        shutil.copyfile(f.pins, os.path.join(co, 'ports', 'fnv-nx', 'lift', 'proof', 'sail_pins.txt'))
        out = os.path.join(td, 'out')
        os.makedirs(out)
        env = {'PATH': os.environ['PATH'], 'GATE_OUT': out, 'ARTIFACTS_URL': f.art.url, 'HOME': td, 'GIT_TERMINAL_PROMPT': '0'}
        r = subprocess.run([sys.executable, '-I', 'ports/fnv-nx/lift/proof/formgate.py'], cwd=co, env=env, capture_output=True, text=True)
        return r, out

    def test_green_and_red_without_cache_and_without_z3(self):
        for encs, status, rc in ((GOOD, 'green', 0), (GOOD + NOBUILD, 'red', 2)):
            with workdir() as td:
                f = Fixture(td, encs)
                r, out = self.run_gate(td, f)
                self.assertEqual(r.returncode, rc, r.stderr + r.stdout)
                self.assertEqual(fakeext.accepted('forms', out, rc)[:2], (status, len(encs)))

    def test_missing_pack_is_red_with_reason(self):
        with workdir() as td:
            f = Fixture(td, GOOD, declare=('certs',))
            r, out = self.run_gate(td, f)
            self.assertEqual(r.returncode, 2)
            with open(os.path.join(out, 'forms.json')) as fh:
                d = json.load(fh)
            self.assertEqual((d['status'], d['detail'], d['uncertified']), ('red', 'inputs: external liftpack not declared', ['inputs']))

    def test_arguments_are_refused(self):
        r = subprocess.run([sys.executable, '-I', os.path.join(PROOF, 'formgate.py'), '--shapes', 'x'], capture_output=True, text=True, env={'GATE_OUT': '/'})
        self.assertEqual(r.returncode, 1)


if __name__ == '__main__':
    unittest.main()
