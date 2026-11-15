#!/usr/bin/env python3
"""Self-test of proof/ehoracle.py on a synthetic PE32 built here (no game data): the oracle reads the tables from the image bytes, so a lifter fact that differs from the bytes is a finding.
Green: facts equal to the bytes -> no finding. Red (one finding class each): a wrong catch address, a wrong unwind prev, a wrong scope filter, a thunk whose jump is no frame handler.
The Wine reference (dlls/msvcrt/except_i386.c, cppexcept.h, cpp.c of the published wine-9.0 tag) is pinned by wine_pin.json; check_wine is tested against a synthetic tree.
python3 -I tests/test_ehoracle.py"""
import hashlib, json, os, struct, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import ehoracle as E

BASE, TEXT = 0x400000, 0x1000                      # image base, RVA of the one section
def va(off): return BASE + TEXT + off

def build_pe(thunk_import=b'__CxxFrameHandler3'):
    """one RWX section at RVA 0x1000; offsets (in the section): 0x000 thunk `mov eax, FuncInfo ; jmp [slot]`, 0x020/0x030/0x040/0x050 funclets (ret), 0x100 FuncInfo, 0x140 unwind map, 0x160 try block,
    0x180 catch block, 0x200 SCOPETABLE v3 (2 entries), 0x300 import table of msvcrt.dll naming THUNK_IMPORT (IAT at 0x350)"""
    s = bytearray(0x1000)
    fi, um, tb, cb, sc = va(0x100), va(0x140), va(0x160), va(0x180), va(0x200)
    s[0:5] = b'\xb8' + struct.pack('<I', fi); s[5:7] = b'\xff\x25'; s[7:11] = struct.pack('<I', va(0x350))
    for o in (0x20, 0x30, 0x40, 0x50): s[o] = 0xc3
    s[0x100:0x124] = struct.pack('<9I', E.CXX_FRAME_MAGIC_VC6, 2, um, 1, tb, 0, 0, 0, 0)
    s[0x140:0x150] = struct.pack('<iIiI', -1, va(0x30), 0, 0)                                  # state 0: prev -1, funclet; state 1: prev 0, no action
    s[0x160:0x174] = struct.pack('<iiiiI', 0, 0, 1, 1, cb)                                       # try [0,0], catch level 1, one catch block
    s[0x180:0x190] = struct.pack('<IIiI', 0, 0, 0, va(0x20))                                     # catch(...) handler at 0x20
    s[0x200:0x224] = struct.pack('<iIIiII', -1, va(0x40), va(0x50), 0, 0, va(0x50))              # level 0: prev -1 filter 0x40 handler 0x50; level 1: prev 0, finally handler 0x50
    s[0x300:0x314] = struct.pack('<5I', TEXT + 0x340, 0, 0, TEXT + 0x380, TEXT + 0x350)           # import descriptor: OFT, time, fwd, name, FT
    s[0x340:0x344] = struct.pack('<I', TEXT + 0x360); s[0x350:0x354] = struct.pack('<I', TEXT + 0x360)
    s[0x360:0x362] = b'\0\0'; s[0x362:0x362 + len(thunk_import) + 1] = thunk_import + b'\0'; s[0x380:0x38b] = b'msvcrt.dll\0'
    dos = b'MZ' + bytes(58) + struct.pack('<I', 0x40)
    coff = struct.pack('<HHIIIHH', 0x14c, 1, 0, 0, 0, 224, 0x102)
    dirs = [(0, 0)] * 16; dirs[1] = (TEXT + 0x300, 40)
    opt = struct.pack('<HBBIIIIIIIIIHHHHHHIIIIHHIIIIII', 0x10b, 1, 0, 0x1000, 0, 0, 0x1000, 0x1000, 0x1000, BASE, 0x1000, 0x200, 4, 0, 0, 0, 4, 0, 0, 0x2000, 0x200, 0, 3, 0, 0x100000, 0x1000, 0x100000, 0x1000, 0, 16)
    opt += b''.join(struct.pack('<II', *d) for d in dirs)
    sec = struct.pack('<8sIIIIIIHHI', b'.text', 0x1000, TEXT, 0x1000, 0x200, 0, 0, 0, 0, 0xe0000060)
    hdr = dos + b'PE\0\0' + coff + opt + sec
    return hdr.ljust(0x200, b'\0') + bytes(s)

def facts(**mut):
    """the lifter's facts for the synthetic image, equal to its bytes; MUT overrides"""
    h = lambda a: '0x%08x' % a
    f = {'funcinfo': [{'addr': h(va(0x100)), 'magic': h(E.CXX_FRAME_MAGIC_VC6), 'flags': 0, 'ipmap': [], 'maxState': 2, 'ESTypeList': 0, 'stubs': [{'target': h(va(0)), 'live': True}], 'unwind': [[-1, h(va(0x30))], [0, 0]],
                       'tries': [{'low': 0, 'high': 0, 'catchHigh': 1, 'handlers': [{'adj': 0, 'type': 0, 'disp': 0, 'addr': h(va(0x20))}]}]}],
         'scopes': [{'addr': h(va(0x200)), 'ver': 3, 'records': [[-1, h(va(0x40)), h(va(0x50))], [0, 0, h(va(0x50))]]}],
         'throwinfo': [], 'throw_sites': []}
    f.update(mut); return f

class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
    def tearDown(self): self.d.cleanup()
    def check(self, f, **kw):
        p = os.path.join(self.d.name, 'x.dll'); open(p, 'wb').write(build_pe(**kw))
        img = E.Image(p)
        return E.check_image('syn', img, f, [], lambda ptr: '', [(va(0), 11), (va(0x20), 1)])
    def kinds(self, r): return sorted({x['kind'] for x in r['findings']})

    def test_green(self):
        r = self.check(facts())
        self.assertEqual(r['findings'], []); self.assertEqual((r['tables']['funcinfo'], r['tables']['scope'], r['frame_handler_thunks']), (1, 1, 1))
        self.assertEqual(r['rows'], r['agree_rows']); self.assertGreater(r['rows'], 0)
    def test_wrong_catch_address_red(self):
        f = facts(); f['funcinfo'][0]['tries'][0]['handlers'][0]['addr'] = '0x%08x' % va(0x30)
        self.assertIn('funcinfo_field', self.kinds(self.check(f)))
    def test_wrong_unwind_prev_red(self):
        f = facts(); f['funcinfo'][0]['unwind'][1][0] = -1
        r = self.check(f); self.assertIn('funcinfo_field', self.kinds(r)); self.assertIn('funcinfo_decision', self.kinds(r))   # the row of state 1 changes too (local unwind runs to -1)
    def test_wrong_scope_filter_red(self):
        f = facts(); f['scopes'][0]['records'][0][1] = '0x%08x' % va(0x50)
        r = self.check(f); self.assertIn('scope_field', self.kinds(r)); self.assertIn('scope_decision', self.kinds(r))
    def test_wrong_scope_prev_red(self):
        f = facts(); f['scopes'][0]['records'][1][0] = -1
        self.assertIn('scope_field', self.kinds(self.check(f)))
    def test_funcinfo_missing_from_facts_red(self):
        self.assertIn('funcinfo_not_in_facts', self.kinds(self.check(facts(funcinfo=[]))))
    def test_funcinfo_not_reached_by_a_thunk_red(self):
        f = facts(); f['funcinfo'].append(dict(f['funcinfo'][0], addr='0x%08x' % va(0x500)))
        self.assertIn('funcinfo_not_found_by_oracle', self.kinds(self.check(f)))
    def test_thunk_to_non_frame_handler_red(self):
        r = self.check(facts(), thunk_import=b'memcpy')
        self.assertIn('thunk_operand_funcinfo_target_not_frame_handler', self.kinds(r)); self.assertEqual(r['frame_handler_thunks'], 0)

class W(unittest.TestCase):
    """check_wine against a synthetic tree and a synthetic pin: the pin's sha256 and every cited line are verified"""
    def setUp(self):
        self.d = tempfile.TemporaryDirectory(); w = self.d.name
        self.src = 'line1\nstatic void cxx_local_unwind( x )\n/* FIXME: no cookie validation yet */\n'
        os.makedirs(w + '/t'); open(w + '/t/a.c', 'w').write(self.src)
        self.pin = w + '/pin.json'
        json.dump({'files': {'t/a.c': hashlib.sha256(self.src.encode()).hexdigest()}, 'rules': [['t/a.c', 2, 'cxx_local_unwind('], ['t/a.c', 3, 'no cookie validation yet']]}, open(self.pin, 'w'))
    def tearDown(self): self.d.cleanup()
    def test_green(self): self.assertEqual(E.check_wine(self.d.name + '/t/..', self.pin)[0], [])
    def test_changed_file_red(self):
        open(self.d.name + '/t/a.c', 'w').write(self.src + 'x\n'); self.assertEqual([x['kind'] for x in E.check_wine(self.d.name, self.pin)[0]], ['wine_file_sha'])
    def test_citation_off_by_one_red(self):
        p = json.load(open(self.pin)); p['rules'][0][1] = 3; json.dump(p, open(self.pin, 'w'))
        self.assertEqual([x['kind'] for x in E.check_wine(self.d.name, self.pin)[0]], ['wine_rule_citation'])
    def test_missing_file_red(self):
        os.remove(self.d.name + '/t/a.c'); self.assertEqual([x['kind'] for x in E.check_wine(self.d.name, self.pin)[0]], ['wine_file_missing'])

if __name__ == '__main__': unittest.main()
