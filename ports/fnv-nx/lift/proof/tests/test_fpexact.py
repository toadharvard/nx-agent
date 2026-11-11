"""Tests of proof/gate_fpexact.py on synthetic objects (assembled / compiled here with aarch64-linux-gnu-as and -g++; nothing derived from
game data): the certificate check, the reciprocal-estimate rule, the float -> integer rule (the GCC plugin toolchain/f2i_check/fnvnx_f2i_check.cc is
built here with the host g++ against gcc-13-plugin-dev-aarch64-linux-gnu), the farm program tools/rcpcert.c's refusal on a non-reference CPU."""
import hashlib
import itertools
import json
import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
PROOF = os.path.join(HERE, '..')
LIFT = os.path.join(PROOF, '..')
sys.path.insert(0, HERE)
sys.path.insert(0, LIFT)
import fakeext
from proof import gate_fpexact as G

WORK = os.environ.get('FNVNX_TEST_WORK', '/home/user/l3work/proof/test_work')     # declared input: the directory the fixtures are written below
PLUGIN_INC = os.environ.get('FNVNX_PLUGIN_INC', '/usr/lib/gcc-cross/aarch64-linux-gnu/13/plugin/include')   # declared input: gcc-13-plugin-dev-aarch64-linux-gnu
_SEQ = itertools.count()
NOSITES = json.dumps({'format': 'fnvnx-f2i/1', 'findings': [], 'helpers': [], 'sites': 0, 'unit': 'x'})
CXX = ['aarch64-linux-gnu-g++', '-std=gnu++17', '-O2', '-c', '-w', '-mabi=ilp32', '-ffreestanding', '-fno-exceptions']


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


def build_plugin(d):
    so = os.path.join(d, 'fnvnx_f2i_check.so')
    subprocess.check_call(['g++', '-shared', '-fPIC', '-fno-rtti', '-O1', '-std=gnu++17', '-I' + PLUGIN_INC, os.path.join(LIFT, '..', 'toolchain', 'f2i_check', 'fnvnx_f2i_check.cc'), '-o', so])
    return so


def cxx_object(d, name, src, so):
    """compile src with the plugin; the result file lands next to the object"""
    c = os.path.join(d, name + '.cpp')
    put(c, src)
    o = os.path.join(d, name + '.o')
    subprocess.check_call(CXX + ['-fplugin=' + so, '-fplugin-arg-fnvnx_f2i_check-out=' + o + '.f2i.json', c, '-o', o])
    return o


class Cert(unittest.TestCase):
    def fake_lift(self, d, cert):
        rt = os.path.join(d, 'lift', 'rt')
        os.makedirs(rt)
        for f in ('rcpref.h', 'rcptab.c'):
            shutil.copyfile(os.path.join(LIFT, 'rt', f), os.path.join(rt, f))
        if cert is not None:
            c = {'cpu': {'family': 6, 'model': 207, 'stepping': 2, 'vendor': 'GenuineIntel'}, 'format': 'fnvnx-rcpcert/1',
                 'header_sha256': G.sha256_file(rt + '/rcpref.h'), 'mismatches': 0, 'rcp_stream_sha256': '0' * 64,
                 'rsqrt_stream_sha256': '1' * 64, 'source_sha256': G.sha256_file(rt + '/rcptab.c')}
            c.update(cert)
            put(rt + '/rcptab.cert.json', json.dumps(c))
        return os.path.join(d, 'lift')

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
            put(lift + '/rt/rcptab.c', get(lift + '/rt/rcptab.c') + '\n')
            r = G.check_cert(lift)
            self.assertEqual(len(r), 1)
            self.assertIn('source_sha256', r[0])

    def test_stale_header(self):
        with workdir() as d:
            lift = self.fake_lift(d, {})
            put(lift + '/rt/rcpref.h', get(lift + '/rt/rcpref.h') + '\n')
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
            p = lift + '/rt/rcpref.h'
            put(p, get(p).replace('FNVL_RCP_REF_MODEL 207', 'FNVL_RCP_REF_MODEL 208'))
            c = json.loads(get(lift + '/rt/rcptab.cert.json'))
            c['header_sha256'] = G.sha256_file(p)
            put(lift + '/rt/rcptab.cert.json', json.dumps(c))
            r = G.check_cert(lift)
            self.assertEqual(len(r), 1)
            self.assertIn('not the reference', r[0])

    def test_malformed(self):
        with workdir() as d:
            lift = self.fake_lift(d, {})
            put(lift + '/rt/rcptab.cert.json', '{"format": "fnvnx-rcpcert/1"}')
            self.assertIn('malformed', G.check_cert(lift)[0])
            put(lift + '/rt/rcptab.cert.json', 'not json')
            self.assertIn('malformed', G.check_cert(lift)[0])

    def test_header_must_define_four(self):
        with workdir() as d:
            lift = self.fake_lift(d, {})
            put(lift + '/rt/rcpref.h', '#define FNVL_RCP_REF_FAMILY 6\n')
            self.assertIn('rcpref.h', G.check_cert(lift)[0])

    def test_the_pinned_certificate(self):
        """the certificate committed in this tree is valid for this tree's sources"""
        self.assertEqual(G.check_cert(LIFT), [])

    def test_farm_program_refuses_other_cpu(self):
        """tools/rcpcert.c built against a header that names another CPU exits 2 and writes no certificate"""
        with workdir() as d:
            os.makedirs(d + '/tools')
            os.makedirs(d + '/rt')
            shutil.copyfile(os.path.join(LIFT, 'tools', 'rcpcert.c'), d + '/tools/rcpcert.c')
            shutil.copyfile(os.path.join(LIFT, 'rt', 'rcptab.c'), d + '/rt/rcptab.c')
            h = get(os.path.join(LIFT, 'rt', 'rcpref.h')).replace('FNVL_RCP_REF_STEPPING 2', 'FNVL_RCP_REF_STEPPING 15').replace('"GenuineIntel"', '"NoSuchVendor"')
            put(d + '/rt/rcpref.h', h)
            subprocess.check_call(['gcc', '-O2', '-msse2', '-pthread', '-o', d + '/c', d + '/tools/rcpcert.c', d + '/rt/rcptab.c'])
            r = subprocess.run([d + '/c', '0' * 64, '0' * 64, d + '/out.json'], capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            self.assertFalse(os.path.exists(d + '/out.json'))


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

    def test_gate_item_red(self):
        with workdir() as d:
            os.makedirs(d + '/objs/sub')
            asm_object(d + '/objs', 'ok', 'fadd s0, s1, s2')
            asm_object(d + '/objs/sub', 'bad', 'frsqrte s0, s1')
            for n in ('ok', 'sub/bad'):
                put(d + '/objs/%s.o.f2i.json' % n, NOSITES)
            items = G.judge_objects('R', [('ok.o', d + '/objs/ok.o'), ('sub/bad.o', d + '/objs/sub/bad.o')], LIFT, None)
            self.assertEqual(sorted(k for k, v in items.items() if v and k != 'R:rcpcert'), ['rcp:R/sub/bad.o'])
            self.assertEqual(sorted(items), ['R:forms', 'R:rcpcert', 'f2i:R/ok.o', 'f2i:R/sub/bad.o', 'rcp:R/ok.o', 'rcp:R/sub/bad.o'])


class F2I(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(WORK, exist_ok=True)
        cls.pdir = os.path.join(WORK, 'fpexact_plugin')
        shutil.rmtree(cls.pdir, ignore_errors=True)
        os.makedirs(cls.pdir)
        cls.so = build_plugin(cls.pdir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.pdir)

    HELPER = ('#define F2I __attribute__((fnvnx_f2i))\n'
              'typedef unsigned u32; typedef int s32;\n'
              'F2I static inline u32 conv(double v){ double r=__builtin_trunc(v); if(!(r>=-2147483648.0&&r<=2147483647.0)) return 0x80000000u; return (u32)(s32)r; }\n'
              'extern "C" u32 use1(double v){ return conv(v); }\n')

    def judge(self, name, src):
        with workdir() as d:
            o = cxx_object(d, name, src, self.so)
            res = G.read_f2i(o)[0]
            r = G.judge_object(name + '.o', o)
            return res, r['f2i:' + name + '.o'], r['rcp:' + name + '.o']

    def test_helper_only_is_green(self):
        res, f2i, rcp = self.judge('good', self.HELPER)
        self.assertEqual((f2i, rcp), ([], []))
        self.assertEqual(res['helpers'], ['u32 conv(double)'])
        self.assertGreater(res['sites'], 0)

    def test_bare_cast_is_red(self):
        res, f2i, _ = self.judge('bare', self.HELPER + 'extern "C" s32 bare(double v){ return (s32)v; }\n')
        self.assertEqual(len(f2i), 1)
        self.assertIn('conv outside', f2i[0])
        self.assertIn('bare', f2i[0])

    def test_implicit_and_compound_conversions_are_red(self):
        _, f2i, _ = self.judge('impl', 'extern "C" int a(double v){ int r = v; return r; }\nextern "C" int b(double v, int x){ x += v; return x; }\n'
                               'extern "C" long long c(float v){ return v; }\n')
        self.assertEqual(len(f2i), 3)

    def test_rounding_builtins_are_red(self):
        _, f2i, _ = self.judge('rnd', 'extern "C" long a(float v){ return __builtin_lrintf(v); }\nextern "C" long long b(double v){ return __builtin_llround(v); }\n'
                               'extern "C" int c(double v){ return __builtin_ifloor(v); }\n')
        self.assertEqual(len(f2i), 3)
        self.assertTrue(all('builtin outside' in x for x in f2i))

    def test_vector_conversion_is_red(self):
        _, f2i, _ = self.judge('vec', 'typedef float v4 __attribute__((vector_size(16))); typedef int i4 __attribute__((vector_size(16)));\n'
                               'extern "C" i4 a(v4 v){ return __builtin_convertvector(v, i4); }\n')
        self.assertEqual(len(f2i), 1)

    def test_asm_with_float_operand_is_red_and_plain_asm_is_not(self):
        _, f2i, _ = self.judge('asm', 'extern "C" int a(float v){ int r; __asm__("fcvtzs %w0, %s1" : "=r"(r) : "w"(v)); return r; }\n'
                               'extern "C" unsigned b(){ unsigned r; __asm__ volatile("mrs %0, fpcr" : "=r"(r)); return r; }\n'
                               'extern "C" void c(){ __asm__ volatile("yield"); }\n')
        self.assertEqual(len(f2i), 2)      # the asm with the float operand, and (3b) its FCVT in a unit with no conversion site
        self.assertTrue(any('asm outside' in x for x in f2i) and any('no conversion site' in x for x in f2i))
        self.assertEqual(sum('asm outside' in x for x in f2i), 1)

    def test_inlined_helper_cannot_hide_a_bare_site(self):
        """the helper inlined into a caller that also converts bare: only the bare site is a finding, at its own line"""
        _, f2i, _ = self.judge('mix', self.HELPER + 'extern "C" u32 both(double v, double w){ return conv(v) + (u32)(s32)w; }\n')
        self.assertEqual(len(f2i), 1)
        self.assertIn('both', f2i[0])

    def test_non_conversions_are_green(self):
        _, f2i, rcp = self.judge('plain', 'extern "C" double a(int v){ return v; }\nextern "C" float b(double v){ return (float)v; }\n'
                                 'extern "C" double c(double v){ return __builtin_trunc(v) + __builtin_floor(v); }\nextern "C" int d(double v){ return v < 3.0; }\n')
        self.assertEqual((f2i, rcp), ([], []))

    def test_missing_result_file_is_red(self):
        with workdir() as d:
            o = asm_object(d, 'noresult', 'fadd s0, s1, s2')
            f2i = G.judge_object('noresult.o', o)['f2i:noresult.o']
            self.assertEqual(len(f2i), 1)
            self.assertIn('no result file', f2i[0])

    def test_malformed_result_file_is_red(self):
        with workdir() as d:
            o = asm_object(d, 'bad', 'fadd s0, s1, s2')
            for body in ('not json', '{"format": "fnvnx-f2i/1"}', '{"format": "other", "findings": [], "sites": 0, "helpers": []}'):
                put(o + '.f2i.json', body)
                f2i = G.judge_object('bad.o', o)['f2i:bad.o']
                self.assertEqual(len(f2i), 1, body)
                self.assertIn('malformed', f2i[0])

    def test_bare_fcvtzs_without_a_source_site_is_red(self):
        """3b: a conversion instruction in an object whose unit has no conversion site (an asm without FP operands, a .S file)"""
        for ins in ('fcvtzs w0, d0', 'fcvtzu x0, s1', 'fcvtns w0, s0', 'fcvtmu w0, d0', 'fcvtps x0, d0', 'fcvtas w0, s0', 'fcvtzs v0.4s, v1.4s', 'fcvtzs w0, d0, #3'):
            with workdir() as d:
                o = asm_object(d, 'fc', ins)
                put(o + '.f2i.json', NOSITES)
                f2i = G.judge_object('fc.o', o)['f2i:fc.o']
                self.assertEqual(len(f2i), 1, ins)
                self.assertIn('no conversion site', f2i[0])

    def test_fjcvtzs(self):
        with workdir() as d:
            o = asm_object(d, 'fj', '.arch armv8.3-a\nfjcvtzs w0, d0')
            put(o + '.f2i.json', NOSITES)
            self.assertEqual(len(G.judge_object('fj.o', o)['f2i:fj.o']), 1)

    def test_conversion_instruction_with_a_site_passes_3b(self):
        with workdir() as d:
            o = cxx_object(d, 'ok3b', self.HELPER, self.so)
            self.assertTrue(G.scan_object(o)['fcvt'])       # the object really holds the conversion instruction the helper's site produced
            self.assertEqual(G.judge_object('ok3b.o', o)['f2i:ok3b.o'], [])

    def test_undecodable_bytes_make_f2i_red_too(self):
        with workdir() as d:
            o = asm_object(d, 'u', '.inst 0x0f0f0f0f')
            put(o + '.f2i.json', NOSITES)
            r = G.judge_object('u.o', o)
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
            self.assertEqual([k for k, v in G.judge_objects('R', [('a.S', o), ('a.c', o)], LIFT, None).items() if v and k != 'R:rcpcert'], ['f2i:R/a.c'])


class Dependency(unittest.TestCase):
    def test_forms_not_green_makes_the_item_red(self):
        with workdir() as d:
            o = asm_object(d, 'ok', 'fadd s0, s1, s2')
            put(o + '.f2i.json', NOSITES)
            self.assertEqual(G.judge_objects('R', [('ok.o', o)], LIFT, None)['R:forms'], [])
            self.assertEqual(G.judge_objects('R', [('ok.o', o)], LIFT, 'forms: not green: x')['R:forms'], ['forms: not green: x'])

    def test_forms_reason_follows_the_gate_forms(self):
        with mock.patch.object(G.formgate, 'judge', return_value=(3, 3, [], 'fine')):
            self.assertIsNone(G.forms_reason('c'))
        with mock.patch.object(G.formgate, 'judge', return_value=(3, 2, ['k'], 'one shape')):
            self.assertEqual(G.forms_reason('c'), 'forms: not green: one shape')

    def test_committed_state_forms_is_red(self):
        with workdir() as d:
            self.assertEqual(G.forms_reason(d), 'forms: not green: inputs: external liftpack not declared')


class Roots(unittest.TestCase):
    """judge_root with the recipe's build replaced by a given objects.json result (the build needs the sandbox and the compiler mirror)"""
    def judge(self, d, res, forms=None):
        with mock.patch.object(G.recipe, 'load_recipe', return_value={}), mock.patch.object(G.recipe, 'load_admit', return_value={}), \
                mock.patch.object(G.gate_build, 'ensure', return_value=(res, d)):
            return G.judge_root('X', '.', d, LIFT, lambda: forms)

    def test_green(self):
        with workdir() as d:
            o = asm_object(d, 'a', 'fadd s0, s1, s2')
            put(o + '.f2i.json', NOSITES)
            self.assertEqual(self.judge(d, {'status': 'green', 'objects': [{'unit': 'a.o', 'object': o}]}), (4, 4, [], ''))

    def test_red_items_sorted_and_named(self):
        with workdir() as d:
            os.makedirs(d + '/z')
            objs = []
            for n in ('z/b', 'a'):
                o = asm_object(d, n.replace('/', '_'), 'frecpe s0, s1')
                objs.append({'unit': n + '.o', 'object': o})
            r = self.judge(d, {'status': 'green', 'objects': objs}, forms='forms: not green: y')
            self.assertEqual(r[:3], (6, 1, ['X:forms', 'f2i:X/a.o', 'f2i:X/z/b.o', 'rcp:X/a.o', 'rcp:X/z/b.o']))

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
