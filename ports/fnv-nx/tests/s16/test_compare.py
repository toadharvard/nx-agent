#!/usr/bin/env python3
"""tests of S16-compare's tools (tools/compare): metrics, scene-state diff, ESM reader, ini writer, scene scripts and
the gate file. Synthetic data only; no game files.

    python3 -I tests/s16/test_compare.py        (unittest; skimage and flip_evaluator are used when installed)
"""
import json, math, os, struct, subprocess, sys, tempfile, unittest, zlib
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, '..', '..', 'tools', 'compare')
sys.path.insert(0, TOOLS)
import metrics, scenediff, esm, ref_ini, scenes, compare

def img(seed, h=64, w=96):
    rng = np.random.default_rng(seed)
    base = np.linspace(0, 255, w)[None, :, None] * np.ones((h, 1, 3))
    return np.clip(base + rng.normal(0, 20, (h, w, 3)), 0, 255).astype(np.uint8)

def save(a, p):
    from PIL import Image
    os.makedirs(os.path.dirname(p), exist_ok=True); Image.fromarray(a, 'RGB').save(p)

class Metrics(unittest.TestCase):
    def test_identical(self):
        a = img(1); m = metrics.compare(a, a.copy())
        self.assertTrue(m['identical']); self.assertIsNone(m['psnr']); self.assertAlmostEqual(m['ssim'], 1.0, places=9)
        self.assertEqual(m['max_abs'], 0)

    def test_psnr_known_mse(self):
        a = np.full((32, 32, 3), 100, np.uint8); b = a.copy(); b[..., 0] += 3   # mse = 9/3 = 3
        self.assertAlmostEqual(metrics.psnr(a, b), 10 * math.log10(255 ** 2 / 3.0), places=9)

    def test_ssim_order_and_symmetry(self):
        a = img(2); rng = np.random.default_rng(3)
        small = np.clip(a + rng.normal(0, 2, a.shape), 0, 255).astype(np.uint8)
        big = np.clip(a + rng.normal(0, 40, a.shape), 0, 255).astype(np.uint8)
        s1, s2 = metrics.ssim(a, small), metrics.ssim(a, big)
        self.assertGreater(s1, s2); self.assertLess(s1, 1.0); self.assertGreater(s2, -1.0)
        self.assertAlmostEqual(metrics.ssim(a, big), metrics.ssim(big, a), places=12)

    def test_ssim_constant_shift_is_luminance_only(self):
        a = np.full((40, 40, 3), 120, np.uint8); b = np.full((40, 40, 3), 130, np.uint8)
        # flat images: contrast/structure terms are 1, luminance term (2*120*130+C1)/(120^2+130^2+C1)
        C1 = (0.01 * 255) ** 2
        self.assertAlmostEqual(metrics.ssim(a, b), (2 * 120 * 130 + C1) / (120 ** 2 + 130 ** 2 + C1), places=9)

    def test_ssim_matches_skimage(self):
        try:
            from skimage.metrics import structural_similarity
        except Exception:
            self.skipTest('skimage not installed')
        a, b = img(4), img(5)
        ref = structural_similarity(metrics.luma(a), metrics.luma(b), gaussian_weights=True, sigma=1.5,
                                    use_sample_covariance=False, data_range=255)
        # skimage averages over the padded map's interior crop (pad = 5 px), the same region as our 'valid' map
        self.assertAlmostEqual(metrics.ssim(a, b), ref, places=4)

    def test_flip_when_installed(self):
        try:
            import flip_evaluator  # noqa: F401
        except Exception:
            self.assertIsNone(metrics.flip(img(1), img(2))); return
        a = np.zeros((64, 64, 3), np.uint8); b = a.copy(); b[20:40, 20:40] = 200
        self.assertEqual(metrics.flip(a, a), 0.0); self.assertGreater(metrics.flip(a, b), 0.05)

    def test_size_mismatch(self):
        with self.assertRaises(ValueError): metrics.compare(img(1, 32, 32), img(1, 32, 40))

    def test_diff_image_and_cli(self):
        with tempfile.TemporaryDirectory() as t:
            a, b = img(6), img(6); b[10, 10] = [0, 0, 0]
            save(a, f'{t}/a.png'); save(b, f'{t}/b.png')
            out = subprocess.run([sys.executable, '-I', os.path.join(TOOLS, 'metrics.py'), f'{t}/a.png', f'{t}/b.png',
                                  '--json', '--diff', f'{t}/d.png'], capture_output=True, text=True, check=True)
            m = json.loads(out.stdout)
            self.assertFalse(m['identical']); self.assertEqual(m['frac_over_8'], round(1 / (64 * 96), 6))
            d = metrics.load(f'{t}/d.png'); self.assertEqual(int((d.max(axis=2) > 0).sum()), 1)

def ref(rid, base, pos, kind='REFR', rot=(0, 0, 0), disabled=False):
    return {'ref': rid, 'base': base, 'kind': kind, 'pos': list(pos), 'rot': list(rot), 'disabled': disabled}

def state(refs, **kw):
    s = {'format': scenediff.FORMAT, 'scene': 't', 'cell': {'form': 0x103DF9, 'edid': 'C'}, 'refs': refs}
    s.update(kw); return s

class SceneDiff(unittest.TestCase):
    def test_equal(self):
        r = [ref(1, 10, (0, 0, 0)), ref(2, 11, (5, 5, 5))]
        d = scenediff.diff(state(r), state([dict(x) for x in r]))
        self.assertTrue(d['ok']); self.assertEqual(d['refs']['matched'], 2)

    def test_missing_extra_moved_base(self):
        a = [ref(1, 10, (0, 0, 0)), ref(2, 11, (5, 5, 5)), ref(3, 12, (0, 0, 0))]
        b = [ref(1, 10, (0, 0, 0.5)), ref(2, 11, (5, 5, 9)), ref(3, 13, (0, 0, 0)), ref(4, 14, (1, 1, 1))]
        d = scenediff.diff(state(a), state(b))
        self.assertFalse(d['ok'])
        self.assertEqual([x['ref'] for x in d['refs']['moved']], [2])         # 0.5 is within pos_tol 1.0
        self.assertEqual(d['refs']['moved'][0]['delta'], 4.0)
        self.assertEqual([x['ref'] for x in d['refs']['extra']], [4])
        self.assertEqual(d['refs']['base'], [{'ref': 3, 'ref_base': 12, 'cand_base': 13}])
        d2 = scenediff.diff(state(b), state(a))
        self.assertEqual([x['ref'] for x in d2['refs']['missing']], [4])

    def test_actor_tolerance_and_rotation_wrap(self):
        a = [ref(1, 10, (0, 0, 0), 'ACHR'), ref(2, 11, (0, 0, 0), rot=(0, 0, math.pi - 0.001))]
        b = [ref(1, 10, (30, 0, 0), 'ACHR'), ref(2, 11, (0, 0, 0), rot=(0, 0, -math.pi + 0.001))]
        self.assertTrue(scenediff.diff(state(a), state(b))['ok'])
        b[0]['pos'] = [100, 0, 0]
        d = scenediff.diff(state(a), state(b)); self.assertEqual(len(d['refs']['moved']), 1)

    def test_dynamic_refs_match_by_base_and_position(self):
        a = [ref(0xFF000801, 50, (0, 0, 0), 'ACRE'), ref(0xFF000802, 50, (500, 0, 0), 'ACRE')]
        b = [ref(0xFF000A00, 50, (505, 0, 0), 'ACRE'), ref(0xFF000A01, 50, (3, 0, 0), 'ACRE'),
             ref(0xFF000A02, 51, (0, 0, 0), 'ACRE')]
        d = scenediff.diff(state(a), state(b))
        self.assertEqual(d['refs']['matched'], 2); self.assertEqual(len(d['refs']['extra']), 1)
        self.assertEqual(d['refs']['extra'][0]['base'], 51); self.assertEqual(d['refs']['moved'], [])

    def test_disabled_and_ignore(self):
        a = [ref(1, 10, (0, 0, 0), disabled=True)]
        d = scenediff.diff(state(a), state([ref(1, 10, (0, 0, 0))]))
        self.assertEqual(len(d['refs']['disabled']), 1)
        self.assertTrue(scenediff.diff(state(a), state([]), ignore_disabled=True)['ok'])

    def test_scene_fields(self):
        cam = {'pos': [0, 0, 0], 'rot_deg': [0, 0, 90], 'fov': 75}
        a = state(None, camera=cam, time={'gamehour': 12.0}, weather={'form': 0xFFC88},
                  shaders=[{'kind': 'ps', 'hash': 'aa'}, {'kind': 'vs', 'hash': 'bb'}])
        b = state(None, cell={'form': 1, 'edid': 'X'}, camera={'pos': [3, 4, 0], 'rot_deg': [0, 0, 90.5], 'fov': 75},
                  time={'gamehour': 13.0}, weather={'form': 1}, shaders=[{'kind': 'ps', 'hash': 'aa'}])
        d = scenediff.diff(a, b)
        self.assertEqual(d['camera']['pos_delta'], 5.0); self.assertAlmostEqual(d['camera']['rot_delta_deg'], 0.5, 3)
        self.assertEqual(d['shaders'], {'missing': 1, 'extra': 0, 'common': 1})
        txt = ' | '.join(d['problems'])
        for w in ('cell', 'camera moved', 'camera rotated', 'gamehour', 'weather', 'shaders'): self.assertIn(w, txt)

    def test_format_checked(self):
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump({'format': 'other'}, f)
        with self.assertRaises(ValueError): scenediff.load(f.name)
        os.unlink(f.name)

# a synthetic plugin in the TES4 layout: header, a CELL top group with one interior cell and its children, one
# compressed record, and a second plugin that overrides one reference and deletes another
def sub(t, data): return t + struct.pack('<H', len(data)) + data
def rec(t, fid, data, flags=0):
    if flags & esm.F_COMPRESSED: data = struct.pack('<I', len(data)) + zlib.compress(data)
    return t + struct.pack('<IIIIHH', len(data), flags, fid, 0, 15, 0) + data
def grup(label, gtype, body): return b'GRUP' + struct.pack('<I4siHHHH', 24 + len(body), label, gtype, 0, 0, 0, 0) + body
def refr(fid, base, pos, flags=0, t=b'REFR'):
    return rec(t, fid, sub(b'NAME', struct.pack('<I', base)) + sub(b'DATA', struct.pack('<6f', *pos, 0, 0, 0.5)), flags)

def plugin(path, masters, cell_fid, refs, extra_cells=b''):
    hdr = sub(b'HEDR', struct.pack('<fII', 1.34, 0, 0)) + b''.join(sub(b'MAST', m.encode() + b'\0') +
                                                                   sub(b'DATA', b'\0' * 8) for m in masters)
    cell = rec(b'CELL', cell_fid, sub(b'EDID', b'TestCell\0') + sub(b'DATA', b'\x01'))
    kids = grup(struct.pack('<I', cell_fid), 6, grup(struct.pack('<I', cell_fid), 9, b''.join(refs)))
    body = grup(b'CELL', 0, grup(struct.pack('<i', 0), 2, grup(struct.pack('<i', 0), 3, cell + kids)) + extra_cells)
    open(path, 'wb').write(rec(b'TES4', 0, hdr) + body)

class Esm(unittest.TestCase):
    def test_cell_refs_with_override(self):
        with tempfile.TemporaryDirectory() as t:
            plugin(f'{t}/A.esm', [], 0x000100, [refr(0x000101, 0x000050, (1, 2, 3)),
                                                 refr(0x000102, 0x000051, (4, 5, 6), esm.F_COMPRESSED),
                                                 refr(0x000103, 0x000052, (7, 8, 9), esm.F_DISABLED, b'ACHR')])
            # B.esm: master A; overrides 0x101 (moved), deletes 0x102, adds its own 0x01000200
            plugin(f'{t}/B.esm', ['A.esm'], 0x000100, [refr(0x000101, 0x000050, (10, 2, 3)),
                                                        refr(0x000102, 0x000051, (4, 5, 6), esm.F_DELETED),
                                                        refr(0x01000200, 0x01000060, (0, 0, 0))])
            c, refs = esm.cell_refs(esm.load_order([f'{t}/A.esm']), 'TestCell')
            self.assertTrue(c['interior']); self.assertEqual(c['form'], 0x100)
            self.assertEqual([(r['ref'], r['base'], r['disabled']) for r in refs],
                             [(0x101, 0x50, False), (0x102, 0x51, False), (0x103, 0x52, True)])
            self.assertEqual(refs[1]['pos'], [4.0, 5.0, 6.0]); self.assertEqual(refs[2]['kind'], 'ACHR')
            c, refs = esm.cell_refs(esm.load_order([f'{t}/A.esm', f'{t}/B.esm']), 'testcell')
            self.assertEqual([(r['ref'], r['pos'][0]) for r in refs], [(0x101, 10.0), (0x103, 7.0), (0x01000200, 0.0)])
            self.assertEqual(refs[2]['base'], 0x01000060)
            self.assertEqual(esm.find(esm.load_order([f'{t}/A.esm']), 'CELL'), {0x100: 'TestCell'})

    def test_not_a_plugin(self):
        with tempfile.NamedTemporaryFile(delete=False) as f: f.write(b'NOPE' + b'\0' * 40)
        with self.assertRaises(ValueError): esm.Plugin(f.name)
        os.unlink(f.name)

class IniAndScenes(unittest.TestCase):
    def test_apply(self):
        text = '[General]\r\nbUseThreadedAI=1\r\nfoo=1\r\n[Display]\r\n;bFull Screen=1\r\nbFull Screen=1\r\n'
        out = ref_ini.apply(text, {'General': {'bUseThreadedAI': '0', 'new': 'x'}, 'Display': {'bFull Screen': '0'},
                                   'Audio': {'bEnableAudio': '0'}})
        lines = out.split('\r\n')
        self.assertIn('bUseThreadedAI=0', lines); self.assertIn('foo=1', lines); self.assertIn('new=x', lines)
        self.assertIn(';bFull Screen=1', lines); self.assertIn('bFull Screen=0', lines)
        self.assertLess(lines.index('new=x'), lines.index('[Display]'))
        self.assertIn('[Audio]', lines); self.assertIn('bEnableAudio=0', lines)
        self.assertEqual(out.count('bUseThreadedAI'), 1)

    def test_overrides_pin_device_and_size(self):
        ov = ref_ini.overrides(640, 480)
        self.assertEqual(ov['Display']['iSize W'], '640'); self.assertEqual(ov['Display']['sD3DDevice'], ref_ini.D3D_DEVICE)

    def test_chunks_and_state(self):
        d = {'common_pre': ['tgm'], 'common_post': ['tm'], 'state': ['player.getpos x', 'player.getincell {cell}']}
        s = {'id': 'x', 'cell': 'C', 'cmds': ['coc C', 'wait:20', 'tai', 'wait:5', 'fov 75']}
        self.assertEqual(scenes.chunks(d, s), [(['tgm', 'coc C'], 20), (['tai'], 5), (['fov 75', 'tm'], 0)])
        self.assertEqual(scenes.chunks(d, {'id': 'm', 'menu': 1, 'cmds': []}), [])
        self.assertEqual(scenes.state_lines(d, s), ['player.getpos x', 'player.getincell C'])
        self.assertEqual(scenes.state_lines(d, {'id': 'm'}), ['player.getpos x'])

    def test_parse_console(self):
        v = scenes.parse_console('GetPos: X >> -71591.52\nGetPos: Y >> 2312.63\nGetPos: Z >> 8365.79\n'
                                 'GetAngle: Z >> 45.00\nGameHour = 12.0000\nGetInCell >> 1.00\n')
        self.assertEqual(v['pos_x'], -71591.52); self.assertEqual(v['rot_z'], 45.0); self.assertEqual(v['gamehour'], 12.0)
        self.assertEqual(v['incell'], 1.0)

    def test_shipped_scene_set(self):
        d = json.load(open(os.path.join(TOOLS, 'scenes.json')))
        ids = [s['id'] for s in d['scenes']]
        self.assertEqual(len(ids), len(set(ids)))
        for s in d['scenes']:
            self.assertIn(s['class'], ('deterministic', 'stochastic'))
            for k, (lines, w) in enumerate(scenes.chunks(d, s)):
                self.assertTrue(all(l and 'wait:' not in l for l in lines))

class Gate(unittest.TestCase):
    def run_cmp(self, t, *extra):
        return subprocess.run([sys.executable, '-I', os.path.join(TOOLS, 'compare.py'), '--ref', f'{t}/ref',
                               '--cand', f'{t}/cand', '--scenes', f'{t}/scenes.json', '--out', f'{t}/out',
                               '--candidate-kind', 'test', *extra], capture_output=True, text=True)

    def setup_runs(self, t, cand_noise, scenes_def):
        json.dump({'format': 'fnvnx-scenes/1', 'scenes': scenes_def}, open(f'{t}/scenes.json', 'w'))
        rng = np.random.default_rng(9)
        for s in scenes_def:
            for i in range(2):
                a = img(20 + i, 64, 64)
                save(a, f"{t}/ref/{s['id']}/frame_{i}.png")
                save(np.clip(a + rng.normal(0, 1.0, a.shape), 0, 255).astype(np.uint8), f"{t}/ref2/{s['id']}/frame_{i}.png")
                save(np.clip(a + rng.normal(0, cand_noise, a.shape), 0, 255).astype(np.uint8),
                     f"{t}/cand/{s['id']}/frame_{i}.png")

    def test_pass_fail_incomplete(self):
        sd = [{'id': 'a', 'class': 'deterministic'}, {'id': 'b', 'class': 'stochastic'}]
        with tempfile.TemporaryDirectory() as t:
            self.setup_runs(t, 0.5, sd)
            p = self.run_cmp(t, '--ref2', f'{t}/ref2')
            g = json.load(open(f'{t}/out/gate.json'))
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr); self.assertEqual(g['format'], compare.GATE_FORMAT)
            self.assertEqual(g['status'], 'pass'); self.assertEqual(g['summary']['pass'], 2)
            self.assertTrue(os.path.isfile(f'{t}/out/report.md'))
            for s in g['scenes']:
                self.assertEqual(len(s['frames']), 2); self.assertIn('psnr', s['frames'][0])
        with tempfile.TemporaryDirectory() as t:
            self.setup_runs(t, 60.0, sd)
            p = self.run_cmp(t, '--ref2', f'{t}/ref2')
            g = json.load(open(f'{t}/out/gate.json'))
            self.assertEqual(p.returncode, 1); self.assertEqual(g['status'], 'fail')
            self.assertTrue(all(s['status'] == 'fail' for s in g['scenes']))
            self.assertTrue(any('ssim' in r for r in g['scenes'][0]['reasons']))
        with tempfile.TemporaryDirectory() as t:
            self.setup_runs(t, 0.5, sd)
            json.dump({'format': 'fnvnx-scenes/1', 'scenes': sd + [{'id': 'c', 'class': 'deterministic'}]},
                      open(f'{t}/scenes.json', 'w'))
            p = self.run_cmp(t)
            g = json.load(open(f'{t}/out/gate.json'))
            self.assertEqual(p.returncode, 2); self.assertEqual(g['status'], 'incomplete')
            self.assertEqual([s['status'] for s in g['scenes']], ['pass', 'pass', 'incomplete'])

    def test_state_diff_fails_scene(self):
        sd = [{'id': 'a', 'class': 'deterministic'}]
        with tempfile.TemporaryDirectory() as t:
            self.setup_runs(t, 0.0, sd)
            json.dump(state([ref(1, 10, (0, 0, 0))]), open(f'{t}/ref/a/state.json', 'w'))
            json.dump(state([]), open(f'{t}/cand/a/state.json', 'w'))
            p = self.run_cmp(t)
            g = json.load(open(f'{t}/out/gate.json'))
            self.assertEqual(p.returncode, 1)
            self.assertEqual(g['scenes'][0]['state']['refs']['missing'], 1)

    def test_unstable_reference(self):
        sd = [{'id': 'a', 'class': 'deterministic'}]
        with tempfile.TemporaryDirectory() as t:
            self.setup_runs(t, 0.0, sd)
            for i in range(2): save(img(99 + i, 64, 64), f'{t}/ref2/a/frame_{i}.png')
            p = self.run_cmp(t, '--ref2', f'{t}/ref2')
            g = json.load(open(f'{t}/out/gate.json'))
            self.assertEqual(g['scenes'][0]['status'], 'unstable'); self.assertEqual(p.returncode, 2)

if __name__ == '__main__':
    unittest.main(verbosity=1)
