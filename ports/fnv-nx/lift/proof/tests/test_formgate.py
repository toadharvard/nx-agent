"""Tests of proof/formgate.py: the inputs contract (externals fetched by the trunk's loader from local bare repos, no network), every red reason,
the certification rule on synthetic encodings (byte strings written here, nothing derived from game data)."""
import atexit
import itertools
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
import fakeext
from proof import formgate, shape
import recipe

WORK = tempfile.mkdtemp(prefix='formgate-test-')       # the fixtures are written below it; removed at exit
atexit.register(shutil.rmtree, WORK, ignore_errors=True)
ROOT = 'ports/fnv-nx/liftroot'                         # the toy lifted root of the fixture repo
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
    """one fake artifacts repo with externals liftpack (shapes.tsv) and certs (certs.tsv), the inputs file, a sail_pins file and a toy checkout
    (repo/) whose admit.json has the lifted root ROOT: cxx_origin {lifted_by, pack: liftpack}, the recipe build.json declares the pack external"""
    def __init__(self, td, encs, certs_for=None, dsl_override=None, model=MODEL, result='proved', pin=MODEL, shapes_text=None, declare=('certs',),
                 shapes_file='shapes.tsv', cert_rows=None, lifted=True):
        self.td = td
        self.keys = sorted(set(key_of(h) for h in encs))
        shapes = shapes_text if shapes_text is not None else '# fixture\n' + ''.join('%s\t%s\t1\n' % kf for kf in self.keys)
        rows = ['# shape_key\tfamily\tdsl_sha\tmodel_sha\treference\tresult\n']
        for k, f in self.keys:
            if certs_for is not None and k not in certs_for:
                continue
            rows.append('%s\t%s\t%s\t%s\tsail\t%s\n' % (k, f, dsl_override or formgate.dsl_sha(k, MD) or 'none', model, result))
        os.makedirs(os.path.join(td, 'art'), exist_ok=True)
        self.art = fakeext.Artifacts(os.path.join(td, 'art'))
        self.ext = {'liftpack': self.art.publish('liftpack', {shapes_file: shapes, 'README': 'pack\n'}),
                    'certs': self.art.publish('certs', {'certs.tsv': ''.join(cert_rows if cert_rows is not None else rows)})}
        self.inputs = os.path.join(td, 'inputs.json')
        self.pins = os.path.join(td, 'sail_pins.txt')
        self.repo = os.path.join(td, 'repo')
        self.write(pin=pin, declare=declare)
        self.set_roots([(ROOT, 'liftpack', [self.ext['liftpack']])] if lifted else [])
        self.cache = os.path.join(td, 'cache')
        os.makedirs(self.cache)

    def set_roots(self, roots, native=()):
        """the toy admit.json: ROOTS [(path, pack name, externals of its recipe)] lifted product roots, NATIVE [path] native ones"""
        ents = []
        for path, pack, exts in roots:
            os.makedirs(os.path.join(self.repo, path), exist_ok=True)
            with open(os.path.join(self.repo, path, 'build.json'), 'w') as fh:
                json.dump({'format': 'fnvnx-recipe/1', 'external': exts}, fh)
            ents.append({'path': path, 'class': 'product', 'target': 'guest', 'gates': [], 'recipe': path + '/build.json',
                         'cxx_origin': {'lifted_by': 'ports/fnv-nx/gen/lift.py', 'pack': pack}})
        for path in native:
            os.makedirs(os.path.join(self.repo, path), exist_ok=True)
            with open(os.path.join(self.repo, path, 'build.json'), 'w') as fh:
                json.dump({'format': 'fnvnx-recipe/1'}, fh)
            ents.append({'path': path, 'class': 'product', 'target': 'guest', 'gates': [], 'recipe': path + '/build.json', 'cxx_origin': 'native'})
        os.makedirs(os.path.join(self.repo, 'ports', 'fnv-nx', 'verify'), exist_ok=True)
        with open(os.path.join(self.repo, 'ports', 'fnv-nx', 'verify', 'admit.json'), 'w') as fh:
            json.dump({'format': 'fnvnx-admit/1', 'roots': ents}, fh)

    def write(self, pin=MODEL, declare=('certs',), **over):
        doc = {'format': 'fnvnx-forms-inputs/1', 'external': [self.ext[n] for n in declare], 'certs': 'certs/certs.tsv', 'model_sha256': MODEL}
        doc.update(over)
        with open(self.inputs, 'w') as fh:
            json.dump(doc, fh)
        with open(self.pins, 'w') as fh:
            fh.write('# pins\nz3-solver  -  5  %s\nx86_proof.ir  -  -  %s\n' % ('0' * 64, pin))

    def judge(self):
        with fakeext.use_artifacts(self.art):
            return formgate.judge(self.repo, self.cache, self.inputs, self.pins)


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
    """every red reason of the inputs file or the certificates: item `inputs`, universe 1, certified 0, the reason in detail"""
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
            f.write(shapes='liftpack/shapes.tsv')
            self.red(f, 'keys must be exactly')       # the pack is declared by the root's recipe, no longer here

    def test_certs_external_not_declared(self):
        with workdir() as td:
            f = Fixture(td, GOOD, declare=())
            self.red(f, 'inputs: external certs not declared')

    def test_bad_external_entry_and_path(self):
        with workdir() as td:
            f = Fixture(td, GOOD)
            f.write(certs='certs/../x')
            self.red(f, 'NAME/path')
            f.write(external=[dict(f.ext['certs'], commit='abc')])
            self.red(f, 'bad name, repo, commit')

    def test_certs_hash_mismatch(self):
        with workdir() as td:
            f = Fixture(td, GOOD)
            f.write(external=[dict(f.ext['certs'], tree_sha256='0' * 64)])
            self.red(f, 'tree_sha256 is')

    def test_certs_external_does_not_fetch(self):
        with workdir() as td:
            f = Fixture(td, GOOD)
            f.write(external=[dict(f.ext['certs'], commit='1' * 40)])
            self.red(f, 'does not fetch')

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

    def test_certs_malformed(self):
        with workdir() as td:
            self.red(Fixture(td, GOOD, cert_rows=['a\tb\n']), 'certs.tsv: bad row')

    def test_committed_inputs_are_valid(self):
        """the committed file loads against the real sail_pins.txt; the certs external is the pinned one, the pack is not declared here"""
        with open(formgate.INPUTS) as fh:
            doc = json.load(fh)
        self.assertEqual(doc['model_sha256'], formgate.model_pin())
        self.assertEqual([e['name'] for e in doc['external']], ['certs'])
        self.assertEqual(sorted(formgate.load_inputs()), sorted(formgate.KEYS))


class Universe(unittest.TestCase):
    """the universe is read from the admitted roots: the pack external of every lifted root's recipe"""
    def test_no_lifted_root_is_universe_0_green(self):
        with workdir() as td:
            f = Fixture(td, GOOD, lifted=False)
            self.assertEqual(f.judge()[:3], (0, 0, []))
            f.set_roots([], native=[ROOT])           # a native root has no pack
            self.assertEqual(f.judge()[:3], (0, 0, []))

    def test_no_lifted_root_still_needs_valid_inputs(self):
        with workdir() as td:
            f = Fixture(td, GOOD, lifted=False)
            os.remove(f.inputs)
            self.assertEqual(f.judge()[:3], (1, 0, ['inputs']))

    def test_pack_without_shapes_tsv_is_red_naming_the_root(self):
        with workdir() as td:
            f = Fixture(td, GOOD, shapes_file='other.tsv')
            u, c, ids, detail = f.judge()
            self.assertEqual((u, c, ids), (1, 0, [ROOT + ':shapes']), detail)
            self.assertIn('shapes.tsv is missing from external liftpack', detail)
            self.assertEqual(formgate.FINDINGS, [{'id': ROOT + '/shapes', 'code': 'forms-missing-shapes'}])

    def test_pack_not_declared_by_the_recipe_is_red(self):
        with workdir() as td:
            f = Fixture(td, GOOD)
            f.set_roots([(ROOT, 'liftpack', [])])
            u, c, ids, detail = f.judge()
            self.assertEqual((u, c, ids), (1, 0, [ROOT + ':shapes']), detail)

    def test_pack_hash_mismatch_or_unfetchable_is_red_for_that_root_only(self):
        for bad in (dict(tree_sha256='0' * 64), dict(commit='1' * 40)):
            with workdir() as td:
                f = Fixture(td, GOOD)
                f.set_roots([(ROOT, 'liftpack', [dict(f.ext['liftpack'], **bad)])])
                u, c, ids, detail = f.judge()
                self.assertEqual((u, c, ids), (1, 0, [ROOT + ':shapes']), detail)

    def test_shapes_malformed_is_red_for_the_root(self):
        a, b = key_of(GOOD[0]), key_of(GOOD[1])
        lo, hi = sorted([a, b])
        for text, why in (('%s\tint\n' % lo[0], 'bad row'), ('%s\tfoo\t1\n' % lo[0], 'bad row'), ('', 'no shapes'),
                          ('%s\tint\t1\n%s\tint\t1\n' % (hi[0], lo[0]), 'not sorted'), ('%s\tint\t1\n%s\tint\t1\n' % (lo[0], lo[0]), 'not sorted')):
            with workdir() as td:
                u, c, ids, detail = Fixture(td, GOOD, shapes_text=text).judge()
                self.assertEqual((u, c, ids), (1, 0, [ROOT + ':shapes']), detail)
                self.assertEqual(formgate.FINDINGS, [{'id': ROOT + '/shapes', 'code': 'forms-bad-shapes'}])
                self.assertIn(why, detail)

    def test_union_of_two_lifted_roots_counts_a_shared_shape_once(self):
        with workdir() as td:
            f = Fixture(td, GOOD)
            more = f.art.publish('pack2', {'shapes.tsv': ''.join(sorted('%s\t%s\t1\n' % kf for kf in {key_of(h) for h in GOOD[:2] + NOBUILD}))})
            f.set_roots([(ROOT, 'liftpack', [f.ext['liftpack']]), (ROOT + '2', 'pack2', [more])])
            u, c, ids, _ = f.judge()
            self.assertEqual((u, c, ids), (len(GOOD) + 1, len(GOOD), [key_of(NOBUILD[0])[0]]))

    def test_one_bad_root_does_not_hide_the_other(self):
        with workdir() as td:
            f = Fixture(td, GOOD)
            f.set_roots([(ROOT, 'liftpack', [f.ext['liftpack']]), (ROOT + '2', 'nope', [])])
            u, c, ids, _ = f.judge()
            self.assertEqual((u, c, ids), (len(GOOD) + 1, len(GOOD), [ROOT + '2:shapes']))


class Cli(unittest.TestCase):
    """the gate as the verdict runs it: python -I, cwd = checkout root, GATE_OUT; the checkout is a copy of lift/ beside the real verify/ and contract/
    (admit.json replaced by the toy one)"""
    def run_gate(self, td, f):
        co = os.path.join(td, 'co')
        tree = os.path.join(co, 'ports', 'fnv-nx')
        os.makedirs(os.path.join(tree, 'verify'))
        shutil.copytree(LIFT, os.path.join(tree, 'lift'), ignore=skip_cache, copy_function=shutil.copyfile)
        for n in os.listdir(fakeext.VERIFY):
            if n not in ('admit.json', '__pycache__', 'tests'):
                os.symlink(os.path.abspath(os.path.join(fakeext.VERIFY, n)), os.path.join(tree, 'verify', n))
        for n in os.listdir(os.path.join(fakeext.VERIFY, '..')):       # every sibling tool tree of the real checkout (verify/recipe.py imports ../link, formgate ../contract)
            if n not in ('lift', 'verify') and os.path.isdir(os.path.join(fakeext.VERIFY, '..', n)) and not n.startswith('.'):
                os.symlink(os.path.abspath(os.path.join(fakeext.VERIFY, '..', n)), os.path.join(tree, n))
        shutil.copyfile(os.path.join(f.repo, 'ports', 'fnv-nx', 'verify', 'admit.json'), os.path.join(tree, 'verify', 'admit.json'))
        if os.path.isdir(os.path.join(f.repo, ROOT)):
            shutil.copytree(os.path.join(f.repo, ROOT), os.path.join(co, ROOT))
        shutil.copyfile(f.inputs, os.path.join(tree, 'lift', 'proof', 'formgate_inputs.json'))
        shutil.copyfile(f.pins, os.path.join(tree, 'lift', 'proof', 'sail_pins.txt'))
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

    def test_no_lifted_root_green_universe_0(self):
        with workdir() as td:
            f = Fixture(td, GOOD, lifted=False)
            r, out = self.run_gate(td, f)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertEqual(fakeext.accepted('forms', out, 0)[:3], ('green', 0, 0))

    def test_missing_shapes_is_red_with_reason(self):
        with workdir() as td:
            f = Fixture(td, GOOD, shapes_file='other.tsv')
            r, out = self.run_gate(td, f)
            self.assertEqual(r.returncode, 2)
            with open(os.path.join(out, 'forms.json')) as fh:
                d = json.load(fh)
            self.assertEqual((d['status'], d['uncertified']), ('red', [ROOT + ':shapes']))

    def test_arguments_are_refused(self):
        r = subprocess.run([sys.executable, '-I', os.path.join(PROOF, 'formgate.py'), '--shapes', 'x'], capture_output=True, text=True, env={'GATE_OUT': '/'})
        self.assertEqual(r.returncode, 1)


if __name__ == '__main__':
    unittest.main()
