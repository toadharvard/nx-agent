#!/usr/bin/env python3
"""tests of S16-compare's capture side (tools/compare): the NXD9 reader/writer, the capture verdict and its metric,
the synthetic scene list, and (when the tools are built) the whole loop on the synthetic scenes. Synthetic data only.

    python3 -I tests/s16/test_capture.py
    S16_E2E=<dir with nxd9play.exe, nx_render, s6b_asm, sdp2dksh> python3 -I tests/s16/test_capture.py   (+ Wine run)
"""
import json, os, shutil, struct, subprocess, sys, tempfile, unittest
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, '..', '..', 'tools', 'compare')
sys.path.insert(0, TOOLS)
import nxd9io, capcompare


def save(a, p):
    from PIL import Image
    os.makedirs(os.path.dirname(p), exist_ok=True)
    Image.fromarray(a, 'RGBA').save(p)


def frame(seed, h=48, w=64):
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 256, (h, w, 4), dtype=np.uint8)
    a[..., 3] = 255
    return a


class Nxd9(unittest.TestCase):
    def test_round_trip_and_padding(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, 'a.nxd9')
            w = nxd9io.Writer(p)
            w.u32('DEV ', 320, 240, 21, 75)
            w.rec('VBU ', b'\1\2\3')          # 3 bytes: one byte of padding
            w.rec('PRES')
            w.close()
            r = [(t_, bytes(p_)) for t_, p_ in nxd9io.records(p)]
            self.assertEqual(r, [('DEV ', struct.pack('<4I', 320, 240, 21, 75)), ('VBU ', b'\1\2\3'), ('PRES', b'')])
            self.assertEqual(os.path.getsize(p), 8 + 8 + 16 + 8 + 4 + 8)
            s = nxd9io.stat(p)
            self.assertEqual((s['frames'], s['draws']), (1, 0))

    def test_bad_magic_and_truncation(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, 'b.nxd9')
            open(p, 'wb').write(b'NXCAP\0\0\1')
            with self.assertRaises(ValueError): list(nxd9io.records(p))
            open(p, 'wb').write(nxd9io.MAGIC + struct.pack('<II', nxd9io.tag_u32('VBU '), 100) + b'x' * 10)
            with self.assertRaises(ValueError): list(nxd9io.records(p))
            open(p, 'wb').write(b'')
            with self.assertRaises(ValueError): list(nxd9io.records(p))

    def test_fnv1a64(self):
        self.assertEqual(nxd9io.fnv1a64(b''), 0xcbf29ce484222325)
        self.assertEqual(nxd9io.fnv1a64(b'a'), 0xaf63dc4c8601ec8c)


class CapGate(unittest.TestCase):
    def runs(self, t, cand_delta=0, missing=False, floor_delta=0):
        for side in ('ref', 'ref2', 'cand'):
            for c in ('x', 'y'):
                for f in range(2):
                    a = frame(10 * ord(c) + f)
                    if side == 'cand' and c == 'y':
                        a[..., :3] = np.clip(a[..., :3].astype(int) + cand_delta, 0, 255).astype(np.uint8)
                    if side == 'ref2' and c == 'y':
                        a[..., :3] = np.clip(a[..., :3].astype(int) + floor_delta, 0, 255).astype(np.uint8)
                    if side == 'cand' and missing and c == 'y' and f == 1:
                        continue
                    save(a, f'{t}/{side}/{c}/f{f:05d}.png')

    def gate(self, t):
        p = subprocess.run([sys.executable, '-I', os.path.join(TOOLS, 'capcompare.py'), '--ref', f'{t}/ref', '--ref2',
                            f'{t}/ref2', '--cand', f'{t}/cand', '--out', f'{t}/out'], capture_output=True, text=True)
        return p, json.load(open(f'{t}/out/capgate.json'))

    def test_pass(self):
        with tempfile.TemporaryDirectory() as t:
            self.runs(t)
            p, g = self.gate(t)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            m = g['summary']['metric']
            self.assertEqual((m['frames'], m['frames_over'], m['max_abs'], m['floor_max_abs']), (4, 0, 0, 0))
            self.assertIn('metric: 0/4 over', p.stdout)

    def test_fail_counts_frames_over(self):
        with tempfile.TemporaryDirectory() as t:
            self.runs(t, cand_delta=40)
            p, g = self.gate(t)
            self.assertEqual(p.returncode, 1)
            m = g['summary']['metric']
            self.assertEqual(m['frames_over'], 2)
            self.assertEqual(m['max_abs'], 40)
            self.assertGreater(m['mean_abs'], 0)

    def test_missing_frame_is_over(self):
        with tempfile.TemporaryDirectory() as t:
            self.runs(t, missing=True)
            p, g = self.gate(t)
            self.assertEqual(p.returncode, 1)
            self.assertEqual(g['summary']['metric']['frames_over'], 1)
        with tempfile.TemporaryDirectory() as t:
            self.runs(t)
            shutil.rmtree(f'{t}/cand/y')
            p, g = self.gate(t)
            self.assertEqual(g['summary']['metric']['frames_over'], 2)
            self.assertEqual(g['summary']['metric']['captures_incomplete'], 1)

    def test_nonzero_floor_widens_bounds(self):
        with tempfile.TemporaryDirectory() as t:
            self.runs(t, floor_delta=3)
            p, g = self.gate(t)
            m = g['summary']['metric']
            self.assertEqual(m['floor_max_abs'], 3)
            y = [c for c in g['captures'] if c['capture'] == 'y'][0]
            self.assertEqual(y['status'], 'unstable')


class DrawGate(unittest.TestCase):
    """drawgate.py on hand-made per-draw runs: verdicts, skipped/unaligned draws, and the first-divergence class"""
    def capture(self, path, fmts):
        w = nxd9io.Writer(path)
        w.u32('DEV ', 64, 48, 21, 0)
        for i, f in enumerate(fmts, 1):
            w.rec('TEX ', struct.pack('<6I', i, f, 4, 4, 1, 0) + struct.pack('<I', 0))
        for fr in range(2):
            for i in range(len(fmts)):
                w.u32('RS  ', 22, 1 if i < 2 else 3)        # draw 2 of each frame uses a CULLMODE no pass has
                w.u32('STEX', 0, i + 1)
                w.rec('DRWI', struct.pack('<IiIIII', 4, 0, 0, 3, 0, 1))
            w.rec('PRES')
        w.close()

    def run_gate(self, t, cand_bad=(), skip=None, fmts=(21, 21, 21)):
        os.makedirs(f'{t}/caps')
        self.capture(f'{t}/caps/c.nxd9', list(fmts))
        for side in ('ref', 'cand'):
            for fr in range(2):
                for d in range(len(fmts)):
                    a = frame(100 * fr + d)
                    if side == 'cand' and (fr, d) in cand_bad:
                        a[..., :3] = 255 - a[..., :3]
                    if side == 'cand' and skip and (fr, d) >= skip:
                        continue
                    save(a, f'{t}/{side}/c/f{fr:05d}_d{d:05d}.png')
        if skip:
            open(f'{t}/cand/c/summary.txt', 'w').write(f'nx_render d3d9: skipped 1; first skip: frame {skip[0]} draw {skip[1]}: x')
        p = subprocess.run([sys.executable, '-I', os.path.join(TOOLS, 'drawgate.py'), '--set',
                            f'game={t}/caps:{t}/ref:{t}/cand', '--out', f'{t}/out'], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.load(open(f'{t}/out/drawgate.json'))

    def test_all_pass(self):
        with tempfile.TemporaryDirectory() as t:
            g = self.run_gate(t)
            self.assertEqual((g['draws'], g['pass'], g['fail'], g['skipped']), (6, 6, 0, 0))
            self.assertEqual(g['first_divergences'], [])

    def test_classes(self):
        with tempfile.TemporaryDirectory() as t:
            g = self.run_gate(t, cand_bad={(0, 1), (0, 2), (1, 2)})
            self.assertEqual((g['pass'], g['fail']), (3, 3))
            f = {(x['frame'], x['draw']): x['class'] for x in g['first_divergences']}
            self.assertEqual(f, {(0, 1): 'constants', (1, 2): 'state'})   # one first divergence per frame
        with tempfile.TemporaryDirectory() as t:
            g = self.run_gate(t, cand_bad={(0, 0), (1, 0)}, fmts=(0x31545844, 21, 21))
            self.assertEqual([x['class'] for x in g['first_divergences']], ['format', 'format'])

    def test_skipped_draws_misalign(self):
        with tempfile.TemporaryDirectory() as t:
            g = self.run_gate(t, skip=(1, 1))
            self.assertEqual((g['pass'], g['skipped']), (4, 2))
            self.assertEqual(g['first_divergence_by_class'], {'skipped': 1})


class SweepPieces(unittest.TestCase):
    """nxd9trim (last K complete frames, state carried), sweep_report (programs/shard summaries)"""
    def test_trim(self):
        with tempfile.TemporaryDirectory() as t:
            w = nxd9io.Writer(f'{t}/j.nxd9')
            w.u32('DEV ', 64, 48, 21, 0)
            for fr in range(5):
                w.u32('RS  ', 22, fr)                                   # state of every frame is kept
                w.rec('CLR ', struct.pack('<IIfII', 1, fr, 1.0, 0, 0))   # pixel records only for the kept frames
                w.rec('DRWI', struct.pack('<IiIIII', 4, 0, 0, 3, 0, 1))
                w.rec('PRES')
            w.u32('RS  ', 7, 1); w.rec('DRWI', struct.pack('<IiIIII', 4, 0, 0, 3, 0, 1))   # incomplete frame: dropped
            w.close()
            r = subprocess.run([sys.executable, '-I', os.path.join(TOOLS, 'nxd9trim.py'), f'{t}/j.nxd9', f'{t}/o.nxd9',
                                '--frames', '3'], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            recs = [(tg, bytes(p)) for tg, p in nxd9io.records(f'{t}/o.nxd9')]
            self.assertEqual(sum(1 for tg, _ in recs if tg == 'PRES'), 3)
            self.assertEqual(sum(1 for tg, _ in recs if tg == 'DRWI'), 3)
            self.assertEqual([struct.unpack('<2I', p)[1] for tg, p in recs if tg == 'RS  '], [0, 1, 2, 3, 4])
            self.assertEqual([struct.unpack_from('<2I', p)[1] for tg, p in recs if tg == 'CLR '], [2, 3, 4])
            r = subprocess.run([sys.executable, '-I', os.path.join(TOOLS, 'nxd9trim.py'), f'{t}/j.nxd9', f'{t}/o2.nxd9',
                                '--frames', '6'], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)

    def test_shard_summary(self):
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(f'{t}/s/scenes')
            open(f'{t}/s/programs.tsv', 'w').write('name\thash\tstage\tmodel\tworst_translated\tworst_families\tmaxwell\tgprs\ttex\tns_per_pixel\n'
                                                  'A.pso\taaaa\tpixel\tps_3_0\tpass\tpass\t40\t8\t2\t0.4\n'
                                                  'B.pso\tbbbb\tpixel\tps_3_0\tover\tpass\t90\t20\t4\t0.9\n')
            json.dump({'capture': 'c1', 'stats': {'frame': {'draws': 10, 'passes': 2}, 'capture': {'programs': {'vs': [], 'ps': ['aaaa']}}},
                       'paths': {'translated': {'frames_over': 1, 'draws_pass_frame': 4,
                                                'first_divergence': {'class': 'state', 'draw': 4, 'ps_names': ['A.pso']}}},
                       'gpu': {'frame_ms': 2.5, 'by_family_ms': {'A': 2.5}}, 'ref': {'frame_ms': [30.0]}},
                      open(f'{t}/s/scenes/c1.json', 'w'))
            open(f'{t}/s/done.tsv', 'w').write('c1\tok\t100\nc2\ttimeout\t1200\n')
            r = subprocess.run([sys.executable, '-I', os.path.join(TOOLS, 'sweep_report.py'), 'shard', '--shard-dir', f'{t}/s',
                                '--head', 'f' * 40, '--k', '1', '--n', '2', '--out', f'{t}/o.json', '--md', f'{t}/o.md'],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            o = json.load(open(f'{t}/o.json'))
            self.assertEqual((o['programs_checked'], o['draws'], o['draws_pass']), (2, 10, 4))
            self.assertEqual(o['program_classes_translated'], {'pass': 1, 'over': 1})
            self.assertEqual(o['first_divergence_by_scenes'], {'state': 1})
            self.assertEqual(o['first_divergence_by_draws'], {'state': 6})
            self.assertEqual(o['scene_status'], {'ok': 1, 'timeout': 1})
            self.assertEqual([p['name'] for p in o['failing_programs']], ['B.pso'])
            self.assertEqual(o['top_programs_ns_per_pixel'][0]['name'], 'B.pso')

    def test_program_class_over_sets(self):
        """a program's class over its parameter sets: over if any set is over, else pass if any set passed; a set
        that drew nothing (vacuous) never outranks a pass of another set; vacuous only when no set drew anything"""
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(f'{t}/s/scenes')
            open(f'{t}/s/programs.tsv', 'w').write(
                'name\thash\tstage\tmodel\tatest:translated\tbase:translated\tworst_translated\tworst_families\n'
                'A.pso\taaaa\tpixel\tps_3_0\tvacuous\tpass\tvacuous\t\n'
                'B.vso\tbbbb\tvertex\tvs_2_0\tvacuous\tvacuous\tvacuous\t\n'
                'C.pso\tcccc\tpixel\tps_2_1\tvacuous\tover\tover\t\n'
                'D.pso\tdddd\tpixel\tps_2_1\till\tpass\till\t\n')
            r = subprocess.run([sys.executable, '-I', os.path.join(TOOLS, 'sweep_report.py'), 'shard', '--shard-dir', f'{t}/s',
                                '--head', 'f' * 40, '--k', '1', '--n', '1', '--out', f'{t}/o.json', '--md', f'{t}/o.md'],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            o = json.load(open(f'{t}/o.json'))
            self.assertEqual(o['program_classes_translated'], {'pass': 2, 'vacuous': 1, 'over': 1})
            self.assertEqual(o['programs_vacuous_in_some_set'], 2)
            self.assertEqual([p['name'] for p in o['failing_programs']], ['C.pso'])


class WineRefMirror(unittest.TestCase):
    def test_base_load_order_only(self):
        """the Data mirror leaves out .nam files, masters outside scenes.json's plugins and their BSAs (a .nam loads
        its add-on even when plugins.txt leaves it out); the base game's BSAs stay"""
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(f'{t}/game/Data')
            for n in ('FalloutNV.esm', 'DeadMoney.esm', 'CaravanPack.esm', 'CaravanPack.nam', 'DEADMONEY.NAM',
                      'Fallout - Textures.bsa', 'Update.bsa', 'DeadMoney - Main.bsa', 'CaravanPack - Main.bsa'):
                open(f'{t}/game/Data/{n}', 'w').close()
            r = subprocess.run(['bash', os.path.join(TOOLS, 'wine_ref.sh'), 'mirror', f'{t}/game', f'{t}/m'],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(sorted(os.listdir(f'{t}/m/Data')), ['Fallout - Textures.bsa', 'FalloutNV.esm', 'Update.bsa'])


class Synth(unittest.TestCase):
    def test_list(self):
        p = subprocess.run([sys.executable, '-I', os.path.join(TOOLS, 'synth.py'), 'list'], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        names = [l.split('\t')[0] for l in p.stdout.splitlines()]
        for n in ('vcolor', 'texture', 'blend', 'stencil', 'rtt', 'lit', 'sm2fog', 'srgb', 'reldef'):
            self.assertIn(n, names)


@unittest.skipUnless(os.environ.get('S16_E2E'), 'S16_E2E=<bin dir> (s6b_asm, sdp2dksh) builds a package')
class Perm(unittest.TestCase):
    def test_own_package(self):
        """perm.py on a package of our own programs (synth.py's shaders): one frame per unique program, the
        manifest names each, and --perturb changes the float inputs only"""
        import synth
        b = os.environ['S16_E2E']
        with tempfile.TemporaryDirectory() as t:
            names = ['vs3_ct', 'ps3_tex', 'ps3_vcolor', 'vs20_rel', 'ps20_vcolor']
            args = []
            for n in names:
                open(f'{t}/{n}.asm', 'w').write(synth.SHADERS[n])
                r = subprocess.run([f'{b}/s6b_asm', f'{t}/{n}.asm', f'{t}/{n}.bin'], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                args.append(f"{n}.{'vso' if n.startswith('vs') else 'pso'}={t}/{n}.bin")
            r = subprocess.run([f'{b}/sdp2dksh', '--pack', f'{t}/own.sdp'] + args, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            for out, eps in (('a', '0'), ('b', '0'), ('p', '1e-5')):
                r = subprocess.run([sys.executable, '-I', os.path.join(TOOLS, 'perm.py'), 'build', '--sdp', f'{t}/own.sdp',
                                    '--asm', f'{b}/s6b_asm', '--sdp2dksh', f'{b}/sdp2dksh', '--out', f'{t}/{out}',
                                    '--per-capture', '3', '--perturb', eps], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            m = json.load(open(f'{t}/a/manifest.json'))
            self.assertEqual(m['format'], 'fnvnx-perm/1')
            got = sorted(e['name'] for v in m['captures'].values() for e in v)
            self.assertEqual(got, sorted(a.split('=')[0] for a in args))
            self.assertEqual(len(m['captures']), 2)
            for c in m['captures']:
                self.assertEqual(open(f'{t}/a/{c}.nxd9', 'rb').read(), open(f'{t}/b/{c}.nxd9', 'rb').read())
                s = nxd9io.stat(f'{t}/a/{c}.nxd9')
                self.assertEqual(s['frames'], len(m['captures'][c]))
                self.assertNotEqual(open(f'{t}/a/{c}.nxd9', 'rb').read(), open(f'{t}/p/{c}.nxd9', 'rb').read())
            # parameter sets change the capture; shards partition the programs
            run = lambda out, *x: subprocess.run([sys.executable, '-I', os.path.join(TOOLS, 'perm.py'), 'build', '--sdp',
                                                  f'{t}/own.sdp', '--asm', f'{b}/s6b_asm', '--sdp2dksh', f'{b}/sdp2dksh',
                                                  '--out', f'{t}/{out}', *x], capture_output=True, text=True)
            for v in ('zero', 'large', 'dxt', 'atest', 'fog'):
                r = run('v_' + v, '--variant', v)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotEqual(open(f'{t}/v_{v}/perm_000.nxd9', 'rb').read(), open(f'{t}/a/perm_000.nxd9', 'rb').read(), v)
            got = []
            for k in (1, 2, 3):
                self.assertEqual(run(f's{k}', '--shard', f'{k}/3').returncode, 0)
                got += [e['hash'] for v in json.load(open(f'{t}/s{k}/manifest.json'))['captures'].values() for e in v]
            allh = [e['hash'] for v in m['captures'].values() for e in v]
            self.assertEqual(sorted(got), sorted(allh))


@unittest.skipUnless(os.environ.get('S16_E2E'), 'S16_E2E=<bin dir> runs the Wine loop')
class EndToEnd(unittest.TestCase):
    def test_synthetic_loop(self):
        b = os.environ['S16_E2E']
        with tempfile.TemporaryDirectory() as t:
            py = lambda *a: subprocess.run([sys.executable, '-I', *a], capture_output=True, text=True, timeout=1200)
            r = py(os.path.join(TOOLS, 'synth.py'), 'build', '--asm', f'{b}/s6b_asm', '--sdp2dksh', f'{b}/sdp2dksh',
                   '--out', f'{t}/syn')
            self.assertEqual(r.returncode, 0, r.stderr)
            caps = sorted(os.path.join(t, 'syn', f) for f in os.listdir(f'{t}/syn'))
            for side, extra in (('ref1', ['--lp-threads', '4']), ('ref2', ['--lp-threads', '1'])):
                r = py(os.path.join(TOOLS, 'replay.py'), 'ref', '--exe', f'{b}/nxd9play.exe', '--prefix', f'{b}/pfx',
                       '--out', f'{t}/{side}', *extra, *caps)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = py(os.path.join(TOOLS, 'replay.py'), 'cand', '--bin', f'{b}/nx_render', '--out', f'{t}/cand', *caps)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = py(os.path.join(TOOLS, 'capcompare.py'), '--ref', f'{t}/ref1', '--ref2', f'{t}/ref2', '--cand',
                   f'{t}/cand', '--out', f'{t}/gate')
            g = json.load(open(f'{t}/gate/capgate.json'))
            self.assertEqual(g['summary']['metric']['floor_max_abs'], 0, 'the Wine reference must repeat itself')
            # --frames limits the PNGs (a long journal otherwise writes one per frame), every frame still runs
            r = py(os.path.join(TOOLS, 'replay.py'), 'ref', '--exe', f'{b}/nxd9play.exe', '--prefix', f'{b}/pfx',
                   '--out', f'{t}/only1', os.path.join(t, 'syn', 'texture.nxd9'), '--', '--frames', '1')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(sorted(f for f in os.listdir(f'{t}/only1/texture') if f.endswith('.png')), ['f00001.png'])
            self.assertEqual(open(f'{t}/only1/texture/f00001.png', 'rb').read(), open(f'{t}/ref1/texture/f00001.png', 'rb').read())


if __name__ == '__main__':
    unittest.main(verbosity=1)
