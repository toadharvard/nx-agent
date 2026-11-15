"""Tests of proof/formgate.py: the inputs contract (externals fetched by the trunk's loader from local bare repos, no network), every red reason,
the certification rule on synthetic encodings (byte strings written here, nothing derived from game data)."""
import itertools
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PROOF = os.path.join(HERE, '..')
LIFT = os.path.join(PROOF, '..')
sys.path.insert(0, HERE)
sys.path.insert(0, LIFT)
sys.path.insert(0, os.path.join(LIFT, "..", "verify"))     # cas, recipe: the trunk loader the gate itself uses (-I drops PYTHONPATH)
import fakeext
from proof import certbasis, formgate, prove, shape
import recipe

WORK = os.environ.get('FNVNX_TEST_WORK') or __import__('tempfile').mkdtemp(prefix='fnvnx-proof-test-')     # declared input: the directory the fixtures are written below
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
IR_TEXT = 'fixture: the x86_proof.ir of the external x86ir\n'
MODEL = hashlib.sha256(IR_TEXT.encode()).hexdigest()      # derived from the file, as formgate derives it
# encodings with a DSL builder (add / xor / mov / push / ret / nop) and one without (x87 fld st(1))
GOOD = ['01c8', '31c0', '8b4510', '6a05', 'c3', '90']
NOBUILD = ['d9c1']


def key_of(h):
    ins = list(MD.disasm(bytes.fromhex(h), 0))
    assert len(ins) == 1 and ins[0].size == len(h) // 2, h
    return shape.shape_key(ins[0]), shape.family(ins[0])


class Fixture:
    """one fake artifacts repo with externals liftpack (shapes.tsv) and certs (certs.tsv), the inputs file (and the fake IR as the external x86ir)"""
    def __init__(self, td, encs, certs_for=None, dsl_override=None, model=MODEL, result='proved', shapes_text=None, declare=('liftpack', 'certs', 'x86ir'),
                 shapes_file='shapes.tsv', cert_rows=None, instrs_text=None):
        self.td = td
        self.keys = sorted(set(key_of(h) for h in encs))
        shapes = shapes_text if shapes_text is not None else '# fixture\n' + ''.join('%s\t%s\t1\n' % kf for kf in self.keys)
        rows = ['# shape_key\tfamily\tdsl_sha\tmodel_sha\treference\tresult\n']
        for k, f in self.keys:
            if certs_for is not None and k not in certs_for:
                continue
            rows.append('%s\t%s\t%s\t%s\tsail\t%s\t%s\t-\t0\t0\t-\t-\n' % (k, f, dsl_override or formgate.dsl_sha(k, MD) or 'none', model, result, ','.join(certbasis.OBLIGATIONS)))
        self.art = fakeext.Artifacts(os.path.join(td, 'art'))
        os.makedirs(os.path.join(td, 'art'), exist_ok=True)
        self.art = fakeext.Artifacts(os.path.join(td, 'art'))
        instrs = instrs_text if instrs_text is not None else '# fixture\n' + ''.join('exe\t%08x\t%d\t%s\n' % (0x401000 + 0x10 * i, len(h) // 2, h) for i, h in enumerate(sorted(set(encs))))
        self.ext = {'liftpack': self.art.publish('liftpack', {shapes_file: shapes, 'instrs.tsv': instrs, 'README': 'pack\n'}),
                    'certs': self.art.publish('certs', {'certs.tsv': ''.join(cert_rows if cert_rows is not None else rows)}),
                    'x86ir': self.art.publish('x86ir', {'ir/x86_proof.ir': IR_TEXT})}
        self.inputs = os.path.join(td, 'inputs.json')
        self.write(declare=declare)
        self.cache = os.path.join(td, 'cache')
        os.makedirs(self.cache)

    def write(self, declare=('liftpack', 'certs', 'x86ir'), **over):
        doc = {'format': 'fnvnx-forms-inputs/1', 'external': [self.ext[n] for n in declare], 'shapes': 'liftpack/shapes.tsv',
               'certs': 'certs/certs.tsv', 'instrs': 'liftpack/instrs.tsv'}
        doc.update(over)
        with open(self.inputs, 'w') as fh:
            json.dump(doc, fh)

    def judge(self):
        with fakeext.use_artifacts(self.art):
            return formgate.judge(self.cache, self.inputs)


class Green(unittest.TestCase):
    def test_green_and_detail(self):
        with workdir() as td:
            f = Fixture(td, GOOD)
            u, c, ids, detail = f.judge()
            self.assertEqual((u, c, ids), (2 * len(GOOD), 2 * len(GOOD), []))
            self.assertIn('int %d/%d' % (len(GOOD), len(GOOD)), detail)

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
            self.assertEqual(f.judge()[:3], (2 * len(GOOD), 2 * len(GOOD) - 1, [k0]))

    def test_wrong_dsl_sha_is_red(self):
        with workdir() as td:
            u, c, ids, _ = Fixture(td, GOOD, dsl_override='0' * 64).judge()
            self.assertEqual((u, c, len(ids)), (2 * len(GOOD), len(GOOD), len(GOOD)))

    def test_certificate_stale_after_a_dsl_edit_is_refused(self):
        # certificates written under the current DSL; then the builder of ONE mnemonic (xor) changes: exactly the shapes of xor lose their certificate,
        # the others (their Form did not change) stay certified.  The key of a certificate is (shape, shape_sha of the CURRENT Form, IR sha), nothing else.
        from proof import forms
        with workdir() as td:
            f = Fixture(td, GOOD)
            self.assertEqual(f.judge()[:3], (2 * len(GOOD), 2 * len(GOOD), []))
            orig = forms.H['xor']

            def edited(c):
                orig(c)
                c.F.undefined('df', 'edited')
            forms.H['xor'] = edited
            try:
                u, c, ids, _ = f.judge()
            finally:
                forms.H['xor'] = orig
            self.assertEqual((u, c, ids), (2 * len(GOOD), 2 * len(GOOD) - 1, [key_of('31c0')[0]]))
            self.assertEqual(f.judge()[:3], (2 * len(GOOD), 2 * len(GOOD), []))

    def test_wrong_model_sha_in_the_certificates_is_red(self):
        with workdir() as td:
            self.assertEqual(Fixture(td, GOOD, model='f' * 64).judge()[1], len(GOOD))      # the link check still passes: 0 shape certificates + the linked instances

    def test_result_not_proved_is_red(self):
        for res in ('failed', 'model-gap', 'PROVED'):
            with workdir() as td:
                self.assertEqual(Fixture(td, GOOD, result=res).judge()[1], len(GOOD), res)      # only the linked instances count

    def test_link_check_unlinked_and_mismatch_are_red(self):
        # an instance whose shape has no symbolic Form while the concrete builder covers it: `unlinked`; an undecodable row: red; a bad instrs.tsv: red `inputs`
        with workdir() as td:
            f = Fixture(td, GOOD, instrs_text='exe\t00401000\t2\t01c8\nexe\t00401010\t3\tff\n')
            u, c, ids, detail = f.judge()
            self.assertEqual(ids, ['inputs']); self.assertIn('instrs.tsv', detail)
        with workdir() as td:
            f = Fixture(td, GOOD, instrs_text='exe\t00401000\t2\t01c8\nexe\t00401010\t2\tffff\n')
            u, c, ids, detail = f.judge()
            self.assertEqual(ids, ['link:undecodable:exe:401010'], detail)

    def test_shape_without_builder_is_uncertified(self):
        with workdir() as td:
            u, c, ids, detail = Fixture(td, GOOD + NOBUILD).judge()
            self.assertEqual((u, c, ids), (2 * len(GOOD) + 1, 2 * len(GOOD), [key_of(NOBUILD[0])[0]]))
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
            f = Fixture(td, GOOD, declare=('certs', 'x86ir'))
            self.red(f, 'inputs: external liftpack not declared')

    def test_bad_external_entry_and_path(self):
        with workdir() as td:
            f = Fixture(td, GOOD)
            f.write(shapes='liftpack/../x')
            self.red(f, 'NAME/path')
            f.write(external=[dict(f.ext['liftpack'], commit='abc'), f.ext['certs'], f.ext['x86ir']])
            self.red(f, 'bad name, repo, commit')

    def test_hash_mismatch(self):
        with workdir() as td:
            f = Fixture(td, GOOD)
            f.write(external=[dict(f.ext['liftpack'], tree_sha256='0' * 64), f.ext['certs'], f.ext['x86ir']])
            self.red(f, 'tree_sha256 is')

    def test_external_does_not_fetch(self):
        with workdir() as td:
            f = Fixture(td, GOOD)
            f.write(external=[dict(f.ext['liftpack'], commit='1' * 40), f.ext['certs'], f.ext['x86ir']])
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

    def test_the_ir_external_is_required(self):
        with workdir() as td:
            f = Fixture(td, GOOD, declare=('liftpack', 'certs'))
            self.red(f, 'external x86ir (the pinned x86_proof.ir) not declared')

    def test_a_certificate_of_another_ir_is_not_a_certificate(self):      # the model sha is derived from the file of the external, not typed anywhere
        with workdir() as td:
            f = Fixture(td, GOOD, model='a' * 64)
            u, c, ids, detail = f.judge()
            self.assertEqual((u, c), (2 * len(GOOD), len(GOOD)))

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
        """the committed file: loads up to the missing liftpack; certs external is the pinned one"""
        with open(formgate.INPUTS) as fh:
            doc = json.load(fh)
        self.assertNotIn('model_sha256', doc)      # the IR is pinned in one place: the x86ir external
        self.assertEqual([e['name'] for e in doc['external']], ['certs', 'x86ir'])
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
                self.assertEqual(fakeext.accepted('forms', out, rc)[:2], (status, len(GOOD) + len(encs)))

    def test_missing_pack_is_red_with_reason(self):
        with workdir() as td:
            f = Fixture(td, GOOD, declare=('certs', 'x86ir'))
            r, out = self.run_gate(td, f)
            self.assertEqual(r.returncode, 2)
            with open(os.path.join(out, 'forms.json')) as fh:
                d = json.load(fh)
            self.assertEqual((d['status'], d['detail'], d['uncertified']), ('red', 'inputs: external liftpack not declared', ['inputs']))

    def test_arguments_are_refused(self):
        r = subprocess.run([sys.executable, '-I', os.path.join(PROOF, 'formgate.py'), '--shapes', 'x'], capture_output=True, text=True, env={'GATE_OUT': '/'})
        self.assertEqual(r.returncode, 1)


class DeclaredOutputs(unittest.TestCase):
    def test_declared_column_is_checked_against_the_contract_and_the_tree(self):
        sha = certbasis.platform_sha(['cpuid0'])
        self.assertTrue(formgate.declared_ok('eax:cpuid0,edx:cpuid3@' + sha))
        self.assertFalse(formgate.declared_ok('eax:cpuid0@' + '0' * 64))               # the platform source changed
        self.assertFalse(formgate.declared_ok('esi:cpuid0@' + sha))                    # an output the contract does not allow
        self.assertTrue(formgate.declared_ok('eax:rdtsc,edx:rdtsc@' + certbasis.platform_sha(['rdtsc'])))      # rdtsc is declared (rt/cpumodel.c)
        self.assertFalse(formgate.declared_ok('eax:nosuchfn@' + sha))                  # a function that is not in declared.json
        self.assertFalse(formgate.declared_ok('garbage'))



class EaRule(unittest.TestCase):
    """item ea-rule (proof/eaaudit.py): the Form builders of this tree hold the rule; a builder that loads or stores raw, or hands an mload / mstore an address that no address function made, is one violation each"""
    def test_this_tree_holds_the_rule(self):
        from proof import eaaudit
        self.assertEqual(eaaudit.violations(PROOF), [])

    def test_violations_are_found(self):
        from proof import eaaudit
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'forms.py'), 'w') as f:
                f.write('def mload(m, a, n): return a\ndef good(c):\n    a = ea(c)\n    return mload(a, 4)\ndef bad(c):\n    return mload(c.raw, 4)\n')
            with open(os.path.join(d, 'forms_x.py'), 'w') as f:
                f.write('from dsl import load\ndef g(c):\n    x = load(c)\n    c.store(1)\n    return mstore(c, c.r, 1, 4)\n')
            got = eaaudit.violations(d)
        self.assertEqual(got, [('forms.py', 6, 'address-source: mload argument is not from an address function'),
                               ('forms_x.py', 1, 'raw-access: import load'), ('forms_x.py', 3, 'raw-access: load('), ('forms_x.py', 4, 'raw-access: .store('),
                               ('forms_x.py', 5, 'address-source: mstore argument is not from an address function')])


if __name__ == '__main__':
    unittest.main()
