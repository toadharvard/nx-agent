"""Tests of proof/gate_fpexact.py on synthetic objects (assembled here with aarch64-linux-gnu-as; nothing derived from game data): the certificate
check on a synthetic table root, the reciprocal-estimate rule, the float -> integer rule on result files of the format fnvnx-f2i/1 (the plugin that
writes them is L2's and is tested there)."""
import atexit
import hashlib
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
PROOF = os.path.join(HERE, '..')
LIFT = os.path.join(PROOF, '..')
sys.path.insert(0, HERE)
sys.path.insert(0, LIFT)
import fakeext
from proof import gate_fpexact as G

WORK = tempfile.mkdtemp(prefix='fpexact-test-')       # the fixtures are written below it; removed at exit
atexit.register(shutil.rmtree, WORK, ignore_errors=True)
REPO = os.path.normpath(os.path.join(LIFT, '..', '..', '..'))    # this checkout
_SEQ = itertools.count()


def f2i(unit, findings=(), sites=0, helpers=()):
    return json.dumps({'format': 'fnvnx-f2i/1', 'unit': unit, 'findings': list(findings), 'helpers': list(helpers), 'sites': sites})


def nosites(unit):
    return f2i(unit)


class workdir:
    """a fresh empty directory WORK/fpexact_<n> (n counts the uses in this process), removed on exit"""
    def __enter__(self):
        self.path = os.path.join(WORK, 'fpexact_%d' % next(_SEQ))
        shutil.rmtree(self.path, ignore_errors=True)
        os.makedirs(self.path)
        return self.path

    def __exit__(self, *exc):
        shutil.rmtree(self.path)


def skip_cache(d, names):
    return [n for n in names if n in ('__pycache__', 'tests')]


def put(path, text):
    with open(path, 'w') as fh:
        fh.write(text)


def get(path):
    with open(path) as fh:
        return fh.read()


def asm_object(d, name, text):
    s = os.path.join(d, name + '.s')
    put(s, '.text\n.global f\n.type f, %function\nf:\n' + text + '\nret\n.size f, .-f\n')
    o = os.path.join(d, name + '.o')
    subprocess.check_call(['aarch64-linux-gnu-as', s, '-o', o])
    return o


class Cert(unittest.TestCase):
    def fake_lift(self, d, cert):
        rt = os.path.join(d, 'lift', 'rt')
        os.makedirs(rt)
        put(rt + '/rcpref.h', '#define FNVL_RCP_REF_SYMBOLS "fnvl_rcp32 fnvl_rsqrt32"\n#define FNVL_RCP_REF_VENDOR "GenuineIntel"\n#define FNVL_RCP_REF_FAMILY 6\n#define FNVL_RCP_REF_MODEL 207\n#define FNVL_RCP_REF_STEPPING 2\n')
        put(rt + '/rcptab.c', '/* synthetic table */\n')
        if cert is not None:
            c = {'cpu': {'family': 6, 'model': 207, 'stepping': 2, 'vendor': 'GenuineIntel'}, 'format': 'fnvnx-rcpcert/1',
                 'header_sha256': G.sha256_file(rt + '/rcpref.h'), 'mismatches': 0, 'rcp_stream_sha256': '0' * 64,
                 'rsqrt_stream_sha256': '1' * 64, 'source_sha256': G.sha256_file(rt + '/rcptab.c')}
            c.update(cert)
            put(rt + '/rcptab.cert.json', json.dumps(c))
        return rt

    def test_good(self):
        with workdir() as d:
            self.assertEqual(G.check_cert(self.fake_lift(d, {})), [])

    def test_missing(self):
        with workdir() as d:
            r = G.check_cert(self.fake_lift(d, None))
            self.assertEqual(len(r), 1)
            self.assertIn('missing', r[0])

    def test_stale_source(self):
        with workdir() as d:
            lift = self.fake_lift(d, {})
            put(lift + '/rcptab.c', get(lift + '/rcptab.c') + '\n')
            r = G.check_cert(lift)
            self.assertEqual(len(r), 1)
            self.assertIn('source_sha256', r[0])

    def test_stale_header(self):
        with workdir() as d:
            lift = self.fake_lift(d, {})
            put(lift + '/rcpref.h', get(lift + '/rcpref.h') + '\n')
            self.assertTrue(any('header_sha256' in x for x in G.check_cert(lift)))

    def test_mismatches(self):
        with workdir() as d:
            self.assertTrue(any('mismatches' in x for x in G.check_cert(self.fake_lift(d, {'mismatches': 1}))))

    def test_mismatches_false_is_not_zero(self):
        with workdir() as d:
            self.assertTrue(any('mismatches' in x for x in G.check_cert(self.fake_lift(d, {'mismatches': False}))))

    def test_wrong_cpu(self):
        with workdir() as d:
            r = G.check_cert(self.fake_lift(d, {'cpu': {'family': 6, 'model': 143, 'stepping': 2, 'vendor': 'GenuineIntel'}}))
            self.assertEqual(len(r), 1)
            self.assertIn('not the reference', r[0])

    def test_reference_follows_header(self):
        """the identity compared is the header's constant: change the header (and the cert's header hash with it) and the old cpu is refused"""
        with workdir() as d:
            lift = self.fake_lift(d, {})
            p = lift + '/rcpref.h'
            put(p, get(p).replace('FNVL_RCP_REF_MODEL 207', 'FNVL_RCP_REF_MODEL 208'))
            c = json.loads(get(lift + '/rcptab.cert.json'))
            c['header_sha256'] = G.sha256_file(p)
            put(lift + '/rcptab.cert.json', json.dumps(c))
            r = G.check_cert(lift)
            self.assertEqual(len(r), 1)
            self.assertIn('not the reference', r[0])

    def test_malformed(self):
        with workdir() as d:
            lift = self.fake_lift(d, {})
            put(lift + '/rcptab.cert.json', '{"format": "fnvnx-rcpcert/1"}')
            self.assertIn('malformed', G.check_cert(lift)[0])
            put(lift + '/rcptab.cert.json', 'not json')
            self.assertIn('malformed', G.check_cert(lift)[0])

    def test_header_must_define_four(self):
        with workdir() as d:
            lift = self.fake_lift(d, {})
            put(lift + '/rcpref.h', '#define FNVL_RCP_REF_FAMILY 6\n')
            self.assertIn('rcpref.h', G.check_cert(lift)[0])


class Recip(unittest.TestCase):
    def scan(self, text):
        with workdir() as d:
            return G.scan_object(asm_object(d, 'o', text))

    def test_each_estimate_and_step_instruction(self):
        for ins in ('frecpe s0, s1', 'frecpe d0, d1', 'frecpe v0.4s, v1.4s', 'frsqrte s0, s1', 'frsqrte v0.2d, v1.2d', 'urecpe v0.4s, v1.4s',
                    'ursqrte v0.4s, v1.4s', 'frecps s0, s1, s2', 'frsqrts v0.4s, v1.4s, v2.4s', 'frecpx s0, s1', 'frecpe h0, h1'):
            r = self.scan('.arch armv8.2-a+fp16\n' + ins)
            self.assertEqual([m for _, m in r['recip']], [ins.split()[0]], ins)

    def test_ieee_exact_instructions_pass(self):
        r = self.scan('fadd s0, s1, s2\nfdiv d0, d1, d2\nfsqrt s0, s1\nfmul v0.4s, v1.4s, v2.4s\nfcvt d0, s1\nscvtf s0, w1\nfrintz d0, d1\nfmadd d0, d1, d2, d3')
        self.assertEqual((r['recip'], r['fcvt'], r['undecodable']), ([], [], []))

    def test_function_is_named(self):
        r = self.scan('fadd s0, s1, s2\nfrecpe s0, s1')
        self.assertEqual(r['recip'], [('f', 'frecpe')])

    def test_undecodable_bytes_in_code(self):
        r = self.scan('fadd s0, s1, s2\n.inst 0x0f0f0f0f\nnop')
        self.assertEqual(len(r['undecodable']), 1)
        self.assertEqual(r['recip'], [])

    def test_data_ranges_are_not_decoded(self):
        """literal-pool words after a $d mapping symbol (the assembler's own .word in a text section) are not code"""
        r = self.scan('nop\nb 1f\n.word 0x0f0f0f0f\n.word 0x2e61d800\n1:\nnop')
        self.assertEqual((r['undecodable'], r['recip']), ([], []))

    def test_estimate_after_data_is_seen(self):
        r = self.scan('b 1f\n.word 0x0f0f0f0f\n1:\nfrecpe s0, s1')
        self.assertEqual([m for _, m in r['recip']], ['frecpe'])

    def fake_rt(self, d):
        return Cert().fake_lift(d, {})

    def test_gate_item_red(self):
        with workdir() as d:
            os.makedirs(d + '/objs/sub')
            asm_object(d + '/objs', 'ok', 'fadd s0, s1, s2')
            asm_object(d + '/objs/sub', 'bad', 'frsqrte s0, s1')
            for n in ('ok', 'sub/bad'):
                put(d + '/objs/%s.o.f2i.json' % n, nosites(n + '.o'))
            items = G.judge_objects('R', [('ok.o', d + '/objs/ok.o'), ('sub/bad.o', d + '/objs/sub/bad.o')], None, None)
            self.assertEqual(sorted(k for k, v in items.items() if v), ['rcp:R/sub/bad.o'])
            self.assertEqual(sorted(items), ['R:forms', 'f2i:R/ok.o', 'f2i:R/sub/bad.o', 'rcp:R/ok.o', 'rcp:R/sub/bad.o'])
            rt = self.fake_rt(d)
            self.assertEqual(sorted(G.judge_objects('R', [], rt, None)), ['R:forms'])      # a table declared, no symbol used: no item
            for body, items in (('.global fnvl_rcp32\nfnvl_rcp32:\nret', ['R:rcpcert']), ('bl fnvl_rsqrt32', ['R:rcpcert']), ('.global other\nother:\nbl fnvl_rcp32x', [])):
                o2 = asm_object(d, 'sym', body)
                put(o2 + '.f2i.json', nosites('s.o'))
                got = G.judge_objects('R', [('s.o', o2)], rt, None)
                self.assertEqual([k for k in got if k.endswith('rcpcert')], items, body)
                if items:
                    self.assertEqual(got['R:rcpcert'], [])
            os.remove(rt + '/rcptab.cert.json')
            o2 = asm_object(d, 'sym2', 'bl fnvl_rcp32')
            self.assertIn('missing', G.judge_objects('R', [('s.o', o2)], rt, None)['R:rcpcert'][0])
            os.remove(rt + '/rcptab.c')
            self.assertIn('no table', G.judge_objects('R', [('s.o', o2)], rt, None)['R:rcpcert'][0])
            put(rt + '/rcpref.h', '#define FNVL_RCP_REF_FAMILY 6\n')
            self.assertIn('FNVL_RCP_REF_SYMBOLS', G.judge_objects('R', [('s.o', o2)], rt, None)['R:rcpcert'][0])
            self.assertEqual(G.judge_objects('R', [('s.o', o2)], None, None).get('R:rcpcert'), None)


class F2I(unittest.TestCase):
    def judge(self, unit, body, asm_text='fadd s0, s1, s2'):
        with workdir() as d:
            o = asm_object(d, 'o', asm_text)
            if body is not None:
                put(o + '.f2i.json', body)
            r = G.judge_object(unit, o, unit=unit)
            return r['f2i:' + unit], r['rcp:' + unit]

    def test_no_findings_is_green(self):
        self.assertEqual(self.judge('a.o', f2i('a.o', sites=2, helpers=['u32 conv(double)'])), ([], []))

    def test_every_finding_kind_is_red(self):
        for kind in ('conv', 'builtin', 'asm'):
            f2, _ = self.judge('a.o', f2i('a.o', [{'function': 'bare', 'file': 'x.cpp', 'line': 7, 'kind': kind}], 1))
            self.assertEqual(len(f2), 1, kind)
            self.assertIn('%s outside' % kind, f2[0])
            self.assertIn('bare', f2[0])

    def test_one_red_line_per_finding(self):
        fs = [{'function': 'f%d' % i, 'file': 'x.cpp', 'line': i, 'kind': 'conv'} for i in range(3)]
        self.assertEqual(len(self.judge('a.o', f2i('a.o', fs, 3))[0]), 3)

    def test_unit_field_must_equal_the_unit(self):
        f2, _ = self.judge('a.o', f2i('b.o'))
        self.assertEqual(len(f2), 1)
        self.assertIn("names the unit 'b.o', not 'a.o'", f2[0])

    def test_missing_result_file_is_red(self):
        f2, _ = self.judge('a.o', None)
        self.assertEqual(len(f2), 1)
        self.assertIn('no result file', f2[0])

    def test_malformed_result_file_is_red(self):
        for body in ('not json', '{"format": "fnvnx-f2i/1"}', '{"format": "other", "findings": [], "sites": 0, "helpers": [], "unit": "a.o"}',
                     '{"format": "fnvnx-f2i/1", "findings": [], "sites": 0, "helpers": []}',
                     '{"format": "fnvnx-f2i/1", "findings": [{"kind": "conv"}], "sites": 1, "helpers": [], "unit": "a.o"}'):
            f2, _ = self.judge('a.o', body)
            self.assertEqual(len(f2), 1, body)
            self.assertIn('malformed', f2[0])

    def test_bare_fcvtzs_without_a_source_site_is_red(self):
        """3b: a conversion instruction in an object whose unit has no conversion site (an asm without FP operands, a .S file)"""
        for ins in ('fcvtzs w0, d0', 'fcvtzu x0, s1', 'fcvtns w0, s0', 'fcvtmu w0, d0', 'fcvtps x0, d0', 'fcvtas w0, s0', 'fcvtzs v0.4s, v1.4s', 'fcvtzs w0, d0, #3'):
            f2, _ = self.judge('fc.o', nosites('fc.o'), ins)
            self.assertEqual(len(f2), 1, ins)
            self.assertIn('no conversion site', f2[0])

    def test_fjcvtzs(self):
        f2, _ = self.judge('fj.o', nosites('fj.o'), '.arch armv8.3-a\nfjcvtzs w0, d0')
        self.assertEqual(len(f2), 1)

    def test_conversion_instruction_with_a_site_passes_3b(self):
        with workdir() as d:
            o = asm_object(d, 'ok3b', 'fcvtzs w0, d0')
            put(o + '.f2i.json', f2i('ok3b.o', sites=1, helpers=['conv']))
            self.assertTrue(G.scan_object(o)['fcvt'])
            self.assertEqual(G.judge_object('ok3b.o', o, unit='ok3b.o')['f2i:ok3b.o'], [])

    def test_undecodable_bytes_make_f2i_red_too(self):
        with workdir() as d:
            o = asm_object(d, 'u', '.inst 0x0f0f0f0f')
            put(o + '.f2i.json', nosites('u.o'))
            r = G.judge_object('u.o', o, unit='u.o')
            self.assertEqual(len(r['f2i:u.o']), 1)
            self.assertEqual(len(r['rcp:u.o']), 1)


class Assembler(unittest.TestCase):
    def test_asm_unit_needs_no_result_file_but_a_conversion_is_a_finding(self):
        with workdir() as d:
            o = asm_object(d, 'a', 'fadd s0, s1, s2')
            self.assertEqual(G.judge_object('R/a.S', o, asm=True)['f2i:R/a.S'], [])
            self.assertEqual(len(G.judge_object('R/a.c', o)['f2i:R/a.c']), 1)           # a C unit without the file is a finding
            c = asm_object(d, 'c', 'fcvtzs w0, s0')
            self.assertEqual(len(G.judge_object('R/c.S', c, asm=True)['f2i:R/c.S']), 1)  # 3b: an instruction without a source site
            self.assertEqual([k for k, v in G.judge_objects('R', [('a.S', o), ('a.c', o)], None, None).items() if v and k != 'R:rcpcert'], ['f2i:R/a.c'])


class Dependency(unittest.TestCase):
    def test_forms_not_green_makes_the_item_red(self):
        with workdir() as d:
            o = asm_object(d, 'ok', 'fadd s0, s1, s2')
            put(o + '.f2i.json', nosites('ok.o'))
            self.assertEqual(G.judge_objects('R', [('ok.o', o)], None, None)['R:forms'], [])
            self.assertEqual(G.judge_objects('R', [('ok.o', o)], None, 'forms: not green: x')['R:forms'], ['forms: not green: x'])

    def test_forms_reason_follows_the_gate_forms(self):
        with mock.patch.object(G.formgate, 'judge', return_value=(3, 3, [], 'fine')):
            self.assertIsNone(G.forms_reason('.', 'c'))
        with mock.patch.object(G.formgate, 'judge', return_value=(3, 2, ['k'], 'one shape')):
            self.assertEqual(G.forms_reason('.', 'c'), 'forms: not green: one shape')

    def test_committed_state_forms_is_green_with_no_lifted_root(self):
        """this tree's admit.json has no lifted root: the gate forms is universe 0, green, so the dependency holds"""
        with workdir() as d:
            self.assertIsNone(G.forms_reason(REPO, d))


class Native(unittest.TestCase):
    DOC = {'inputs': [{'at': '/plug/fnvnx_ftoi', 'tree': 't'}], 'argv': ['/c/g++', '-fplugin=/plug/fnvnx_ftoi/fnvnx_ftoi.so', '-c']}

    def test_ftoi_why(self):
        self.assertEqual(G.ftoi_why(self.DOC), [])
        self.assertEqual(G.ftoi_why(None), ['no CC stage document'])
        self.assertEqual(len(G.ftoi_why({'inputs': [], 'argv': self.DOC['argv']})), 1)
        self.assertEqual(len(G.ftoi_why({'inputs': self.DOC['inputs'], 'argv': ['/c/g++']})), 1)
        self.assertEqual(len(G.ftoi_why({'inputs': [], 'argv': []})), 2)

    def test_native_cxx_unit_needs_the_plugin_c_and_asm_units_do_not(self):
        with workdir() as d:
            o = asm_object(d, 'o', 'fadd s0, s1, s2')
            for u in ('a.cc', 'b.c'):
                put(o + '.f2i.json', nosites(u))
                items = G.judge_objects('R', [(u, o)], None, None, {})
                self.assertEqual(items['f2i:R/' + u] != [], u.endswith('.cc'), u)
            put(o + '.f2i.json', nosites('a.cc'))
            self.assertEqual(G.judge_objects('R', [('a.cc', o)], None, None, {'a.cc': self.DOC})['f2i:R/a.cc'], [])
            self.assertEqual(G.judge_objects('R', [('a.cc', o)], None, None)['f2i:R/a.cc'], [])      # lifted root: no document judged


class Roots(unittest.TestCase):
    """judge_root with the recipe's build replaced by a given objects.json result (the build needs the sandbox and the compiler mirror)"""
    def judge(self, d, res, forms=None, repo='.'):
        with mock.patch.object(G.recipe, 'load_recipe', return_value={}), mock.patch.object(G.recipe, 'load_admit', return_value={}), \
                mock.patch.object(G.gate_build, 'ensure', return_value=(res, d)):
            return G.judge_root('X', repo, d, lambda: forms)

    def test_green(self):
        with workdir() as d:
            o = asm_object(d, 'a', 'fadd s0, s1, s2')
            put(o + '.f2i.json', nosites('a.o'))
            self.assertEqual(self.judge(d, {'status': 'green', 'objects': [{'unit': 'a.o', 'object': o}]}), (3, 3, [], ''))

    def test_red_items_sorted_and_named(self):
        with workdir() as d:
            os.makedirs(d + '/z')
            objs = []
            for n in ('z/b', 'a'):
                o = asm_object(d, n.replace('/', '_'), 'frecpe s0, s1')
                objs.append({'unit': n + '.o', 'object': o})
            r = self.judge(d, {'status': 'green', 'objects': objs}, forms='forms: not green: y')
            self.assertEqual(r[:3], (5, 0, ['X:forms', 'f2i:X/a.o', 'f2i:X/z/b.o', 'rcp:X/a.o', 'rcp:X/z/b.o']))

    def test_root_using_the_table_gets_the_certificate_item(self):
        """the item exists iff an object uses a declared table symbol; the table files are the root's own"""
        with workdir() as d:
            o = asm_object(d, 'a', 'bl fnvl_rcp32')
            put(o + '.f2i.json', nosites('a.o'))
            res = {'status': 'green', 'objects': [{'unit': 'a.o', 'object': o}]}
            os.makedirs(d + '/repo')
            rt = Cert().fake_lift(d + '/repo', {})
            os.rename(rt, d + '/repo/X')
            self.assertEqual(self.judge(d, res, repo=d + '/repo'), (4, 4, [], ''))
            os.remove(d + '/repo/X/rcptab.cert.json')
            r = self.judge(d, res, repo=d + '/repo')
            self.assertEqual(r[:3], (4, 3, ['X:rcpcert']))

    def test_build_red_is_one_input_item(self):
        with workdir() as d:
            r = self.judge(d, {'status': 'red', 'objects': [], 'failure': {'stage': 'cc:a.c'}})
            self.assertEqual(r, (1, 0, ['X:inputs'], 'root does not build (stage cc:a.c)'))

    def test_not_an_elf_is_one_input_item(self):
        with workdir() as d:
            put(d + '/x.o', 'hello')
            r = self.judge(d, {'status': 'green', 'objects': [{'unit': 'x.o', 'object': d + '/x.o'}]})
            self.assertEqual(r[:3], (1, 0, ['X:inputs']))
            self.assertIn('input error', r[3])

    def test_read_inputs_is_sorted_by_unit(self):
        res = {'objects': [{'unit': u, 'object': '/p/' + u} for u in ('z/b.o', 'a.o', 'z/a.o', 'm/q.o')]}
        self.assertEqual([u for u, _ in G.read_inputs(res)], ['a.o', 'm/q.o', 'z/a.o', 'z/b.o'])


class Cli(unittest.TestCase):
    """the gate as the verdict runs it: python -I, cwd = a git checkout holding a copy of lift/ and of the overlay of verify/ with its own admit.json"""
    def checkout(self, td, roots):
        co = os.path.join(td, 'co')
        nx = os.path.join(co, 'ports', 'fnv-nx')
        os.makedirs(nx)
        shutil.copytree(LIFT, os.path.join(nx, 'lift'), ignore=skip_cache, copy_function=shutil.copyfile)
        shutil.copytree(fakeext.VERIFY, os.path.join(nx, 'verify'), ignore=skip_cache, copy_function=shutil.copyfile)
        shutil.copytree(os.path.join(fakeext.VERIFY, '..', 'contract'), os.path.join(nx, 'contract'), ignore=skip_cache, copy_function=shutil.copyfile)
        put(os.path.join(nx, 'verify', 'admit.json'), json.dumps({'format': 'fnvnx-admit/1', 'roots': roots}))
        for a in (['init', '-q'], ['add', '-A'], ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'x']):
            subprocess.run(['git', '-C', co] + a, check=True, capture_output=True)
        return co

    def run_gate(self, td, co):
        out = os.path.join(td, 'out')
        os.makedirs(out)
        env = {'PATH': os.environ['PATH'], 'GATE_OUT': out, 'HOME': td}
        r = subprocess.run([sys.executable, '-I', 'ports/fnv-nx/lift/proof/gate_fpexact.py'], cwd=co, env=env, capture_output=True, text=True)
        return r, out

    def test_no_product_root_is_universe_0_green(self):
        with workdir() as d:
            r, out = self.run_gate(d, self.checkout(d, [{'path': 'ports/fnv-nx/verify', 'class': 'tool', 'gates': []}]))
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertEqual(fakeext.accepted('fpexact', out, 0)[:3], ('green', 0, 0))
            self.assertEqual(sorted(json.loads(get(out + '/fpexact.json'))), ['certified', 'detail', 'format', 'name', 'status', 'uncertified', 'universe'])

    def test_product_root_with_an_invalid_recipe_is_red_with_the_reason(self):
        with workdir() as d:
            co = self.checkout(d, [{'path': 'ports/fnv-nx/xroot', 'class': 'product', 'recipe': 'ports/fnv-nx/xroot/build.json', 'gates': []}])
            os.makedirs(co + '/ports/fnv-nx/xroot')
            put(co + '/ports/fnv-nx/xroot/build.json', '{}')
            subprocess.run(['git', '-C', co, 'add', '-A'], check=True)
            subprocess.run(['git', '-C', co, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'y'], check=True)
            r, out = self.run_gate(d, co)
            self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
            res = json.loads(get(out + '/fpexact.json'))
            self.assertEqual((res['status'], res['uncertified'], res['universe']), ('red', ['ports/fnv-nx/xroot:inputs'], 1))
            self.assertIn('invalid recipe', res['detail'])

    def test_arguments_are_refused(self):
        r = subprocess.run([sys.executable, '-I', os.path.join(PROOF, 'gate_fpexact.py'), '--objects', 'x'], capture_output=True, text=True, env={'GATE_OUT': '/'})
        self.assertEqual(r.returncode, 1)


if __name__ == '__main__':
    unittest.main()
