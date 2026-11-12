#!/usr/bin/env python3
"""Self-test of tools/gate_eh.py on synthetic packs and synthetic ehtables.json (no game data).   python3 test/gate_eh_test.py"""
import copy, json, os, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); GATE = os.path.join(os.path.dirname(HERE), 'tools', 'gate_eh.py')
h = lambda a: '0x%08x' % a

def facts(base, n):
    """one image at [base, base + 0x1000): a FuncInfo with an unwind action, a catch, a null action, a stub; a v4 scope with finally + except (handler registered by a record);
    a ThrowInfo with one resolved throw site and one rethrow; a continuation; every red class empty"""
    b = base
    return {'text': [h(b), h(b + 0x1000)], 'failed_funcinfo': [], 'failed_throwinfo': [], 'thrower_escapes': [], 'seh_red': [], 'helpers': [{'addr': h(b + 0xc00), 'arg': 0, 'handlers': [h(b + 0x10)], 'frame_disp': -4, 'err': ''}],
            'registrations': [{'site': h(b + 0xc10), 'handlers': [h(b + 0x10)], 'kind': 'inline', 'frame_disp': -4}],
            'throw_sites': [{'site': h(b + 0xd00), 'tail': False, 'kind': 'const', 'tis': [h(b + 0xa00)], 'red': ''}, {'site': h(b + 0xd10), 'tail': False, 'kind': 'rethrow', 'tis': [], 'red': ''}],
            'funcinfo': [{'addr': h(b + 0x800), 'stubs': [{'target': h(b + 0x20), 'live': True}], 'unwind': [[-1, h(b + 0x30)], [0, 0]],
                          'tries': [{'low': 0, 'high': 1, 'catchHigh': 2, 'handlers': [{'adj': 0, 'type': 0, 'disp': 0, 'addr': h(b + 0x40)}]}]}],
            'scopes': [{'addr': h(b + 0x900), 'ver': 4, 'handler': h(b + 0x50), 'handlers': [h(b + 0x50)], 'frame_disp': -4, 'trylevel_max': 1, 'nonconst_stores': [], 'err': '',
                        'records': [[-2, 0, h(b + 0x60)], [0, h(b + 0x70), h(b + 0x80)]]}],
            'throwinfo': [{'addr': h(b + 0xa00), 'attrs': 0, 'unwind': h(b + 0x90), 'types': [{'type': h(b + 0xb00), 'size': 4, 'copy': h(b + 0xa0)}, {'type': h(b + 0xb10), 'size': 4, 'copy': 0}]}],
            'conts': {h(b + 0xb0): h(b + 0x40)}}

def targets(base): return [base + x for x in (0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80, 0x90, 0xa0, 0xb0)]

class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory(); self.root = self.d.name
        self.pack = os.path.join(self.root, 'pack'); self.fx = os.path.join(self.root, 'facts'); self.out = os.path.join(self.root, 'out')
        for p in (self.pack, self.fx, self.out): os.mkdir(p)
        self.bases = {'exe': 0x400000, 'mod': 0x10000000}
    def tearDown(self): self.d.cleanup()
    def pack_write(self, drop=(), kinds=None, images=2, extra=()):
        rows = [('000fffd0', 'fnvl_msvcrt_eh_nested_catch', 'runtime'), ('00100000', 'I_00100000', 'import')]
        for b in self.bases.values():
            for a in sorted(set(targets(b)) | {b + 0x100} | set(extra)):
                if a in drop: continue
                rows.append(('%08x' % a, 'L_%08x' % a, (kinds or {}).get(a, 'code')))
        open(self.pack + '/entries.tsv', 'w').write(''.join('\t'.join(r) + '\n' for r in rows) + '# table_id 00\n')
        json.dump({'images': images}, open(self.pack + '/gate_liftgen.json', 'w'))
    def facts_write(self, mut=None, skip=()):
        for name, b in self.bases.items():
            if name in skip: continue
            J = facts(b, 0)
            if mut: mut(name, J)
            p = self.fx + ('/ehtables.json' if name == 'exe' else '/' + name)
            if name != 'exe': os.makedirs(p, exist_ok=True); p += '/ehtables.json'
            json.dump(J, open(p, 'w'))
    def run_gate(self):
        r = subprocess.run([sys.executable, '-I', GATE, self.pack, self.fx], env=dict(os.environ, GATE_OUT=self.out), capture_output=True, text=True)
        res = json.load(open(self.out + '/eh.json')); self.assertEqual(res['format'], 'fnvnx-gate/1'); self.assertEqual(res['name'], 'eh')
        self.assertEqual(r.returncode == 0, res['status'] == 'green', r.stderr); return res

    def test_green(self):
        self.pack_write(); self.facts_write(); r = self.run_gate()
        self.assertEqual((r['status'], r['universe'], r['certified'], r['uncertified']), ('green', 2 * 11, 2 * 11, []))
    def test_null_none_is_not_universe(self):
        # per image: 1 unwind (the 0 action is none) + 1 catch + 1 registered handler + 1 frame handler + 1 cont + 1 scope handler + 3 (filter 0 is none: handler, filter, handler) + 2 throw (copy[1] 0 is none) = 11 (scope: handler, 2 rec handlers, 1 filter = 4; so 1+1+1+1+1+4+2 = 11)
        self.pack_write(); self.facts_write(); self.assertEqual(self.run_gate()['universe'], 22)
    def test_missing_target_red(self):
        self.pack_write(drop={0x10000000 + 0x70}); self.facts_write(); r = self.run_gate()
        self.assertEqual((r['status'], r['uncertified']), ('red', ['mod:10000900:rec[1].filter:10000070']))
        self.assertEqual(r['certified'], r['universe'] - 1)
    def test_non_code_kind_red(self):
        self.pack_write(kinds={0x400000 + 0x30: 'runtime'}); self.facts_write(); r = self.run_gate()
        self.assertEqual((r['status'], r['uncertified']), ('red', ['exe:00400800:unwind[0].action:00400030']))
    def test_each_field_counted_once(self):
        self.pack_write(drop={0x400000 + 0x40}); self.facts_write(); r = self.run_gate()   # 0x40 is the catch addr of one table (and the funclet that owns a cont): one field
        self.assertEqual((r['status'], r['uncertified']), ('red', ['exe:00400800:try[0].handler[0].addr:00400040']))
    def test_missing_ehtables_red(self):
        self.pack_write(); self.facts_write(skip=('mod',)); r = self.run_gate(); self.assertEqual(r['status'], 'red'); self.assertIn('1 facts files for 2 images', r['detail'])
    def test_missing_exe_facts_red(self):
        self.pack_write(); self.facts_write(skip=('exe',)); r = self.run_gate(); self.assertEqual(r['status'], 'red'); self.assertIn('exe', r['detail'])
    def test_image_without_facts_even_if_count_matches(self):
        self.bases['third'] = 0x20000000; self.pack_write(images=2)       # three images of code, two facts files, gate_liftgen says 2
        self.facts_write(skip=('third',)); r = self.run_gate(); self.assertEqual(r['status'], 'red'); self.assertIn('no image span', r['detail'])
    def test_empty_tables_green_failed_funcinfo_red(self):
        def empty(n, J): J.update(funcinfo=[], scopes=[], throwinfo=[], registrations=[], helpers=[], throw_sites=[], conts={})
        self.pack_write(); self.facts_write(empty); r = self.run_gate(); self.assertEqual((r['status'], r['universe']), ('green', 0))
        def failed(n, J): J['failed_funcinfo'] = [{'addr': h(0x401000), 'err': 'size'}] if n == 'exe' else []
        self.facts_write(failed); r = self.run_gate(); self.assertEqual(r['status'], 'red'); self.assertEqual(r['violations']['exe']['failed_funcinfo']['count'], 1)
    def red(self, cls, mut, img='exe'):
        def f(n, J):
            if n == img: mut(J)
        self.pack_write(); self.facts_write(f); r = self.run_gate()
        self.assertEqual(r['status'], 'red'); self.assertEqual(list(r['violations']), [img]); self.assertEqual(list(r['violations'][img]), [cls]); return r
    def test_throw_site_without_thrownfo_red(self):
        r = self.red('throw_site', lambda J: J['throw_sites'].append({'site': h(0x400d20), 'tail': False, 'kind': 'red', 'tis': [], 'red': 'arg of an address-taken function'}))
        self.assertEqual(r['violations']['exe']['throw_site']['count'], 1)
    def test_failed_throwinfo_red(self): self.red('failed_throwinfo', lambda J: J['failed_throwinfo'].append({'site': h(0x400d00), 'ti': h(0x500000)}))
    def test_thrower_escape_red(self): self.red('thrower_escapes', lambda J: J['thrower_escapes'].append(h(0x400e00)), 'mod')
    def test_seh_red(self): self.red('seh_red', lambda J: J['seh_red'].append({'site': h(0x400c10), 'why': 'store of an unknown value to fs:[0]'}))
    def test_helper_nonconst_red(self): self.red('helper', lambda J: J['helpers'][0].update(err='argument 0 not constant at 0x401234'))
    def test_nonconst_trylevel_store_red(self): self.red('nonconst_stores', lambda J: J['scopes'][0]['nonconst_stores'].append(h(0x400950)))
    def test_scope_length_vs_trylevel_red(self):
        r = self.red('scope_len', lambda J: J['scopes'][0].update(trylevel_max=2))          # 2 records, but the largest stored level is 2: 3 needed
        self.assertEqual(r['violations']['exe']['scope_len']['first'], ['0x00400900:2!=1+2'])
    def test_scope_err_red(self): self.red('scope_err', lambda J: J['scopes'][0].update(err='frame register not derivable'))
    def test_empty_scope_valid_without_levels(self):
        def f(n, J): J['scopes'][0].update(records=[], trylevel_max=-1)
        self.pack_write(); self.facts_write(f); self.assertEqual(self.run_gate()['status'], 'green')
    def test_unlifted_registered_handler_red(self):
        self.pack_write(drop={0x400000 + 0x10}); self.facts_write(); r = self.run_gate()
        self.assertEqual((r['status'], r['uncertified']), ('red', ['exe:00400c10:frame_handler:00400010']))
    def test_stub_target_needs_landing_red(self):
        self.pack_write(drop={0x10000000 + 0x20}); self.facts_write(); r = self.run_gate()
        self.assertEqual((r['status'], r['uncertified']), ('red', ['mod:10000800:stub[0].target:10000020']))
    def test_missing_scope_len_key_red(self):
        def f(n, J): del J['throw_sites']
        self.pack_write(); self.facts_write(f); self.assertEqual(self.run_gate()['status'], 'red')
    def test_null_where_format_has_no_none_red(self):
        def f(n, J): J['funcinfo'][0]['tries'][0]['handlers'][0]['addr'] = 0
        self.pack_write(); self.facts_write(f); r = self.run_gate(); self.assertEqual(r['status'], 'red'); self.assertIn('null', r['detail'])
    def test_missing_key_red(self):
        def f(n, J): del J['text']
        self.pack_write(); self.facts_write(f); r = self.run_gate(); self.assertEqual(r['status'], 'red')
    def test_entries_beyond_50_truncated_sorted(self):
        def f(n, J): J['funcinfo'][0]['unwind'] = [[-1, h(0x500000 + 4 * i + (0 if n == 'exe' else 0x10000000))] for i in range(80)]
        self.pack_write(); self.facts_write(f); r = self.run_gate(); self.assertEqual(len(r['uncertified']), 50); self.assertEqual(r['uncertified'], sorted(r['uncertified']))
        self.assertEqual(r['status'], 'red')

if __name__ == '__main__': unittest.main()
