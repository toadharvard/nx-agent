#!/usr/bin/env python3
"""tests/s13/test_exe_oracle.py - difffuzz's exe-image oracle (tools/difffuzz/exeimage.py, FNVNX_ORACLE=exe) and the
recon fuzz acceptance driver (recon_fuzz.py), on a synthetic PE32 image and synthetic rows (no game data).

    python3 tests/s13/test_exe_oracle.py        (needs capstone, as the harness does)
"""
import json
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent.parent / 'tools' / 'difffuzz'
sys.path.insert(0, str(TOOLS))
FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def pe32(path):
    """.text 0x00401000 (two functions), .rdata 0x00402000 ("hello"), .data 0x00403000 (initial 0x11223344), .reloc."""
    f1 = (b'\xa1' + struct.pack('<I', 0x00402000) +            # mov eax, [0x00402000]      (fixup at +1)
          b'\x8b\x0d' + struct.pack('<I', 0x00403000) +        # mov ecx, [0x00403000]      (fixup at +7)
          b'\xe8' + struct.pack('<i', 0x00401100 - (0x00401000 + 11 + 5)) +   # call 0x00401100
          b'\xeb\x00' +                                        # jmp short (inside)
          b'\xc3')                                             # ret
    text = f1.ljust(0x100, b'\xcc') + b'\x33\xc0\xc3'          # 0x00401100: xor eax, eax; ret
    rdata, data = b'hello\0', struct.pack('<I', 0x11223344)
    relocs = struct.pack('<II', 0x1000, 8 + 2 * 2) + struct.pack('<HH', 0x3000 | 1, 0x3000 | 7)
    secs = [(b'.text', 0x1000, text, 0x60000020), (b'.rdata', 0x2000, rdata, 0x40000040),
            (b'.data', 0x3000, data, 0xc0000040), (b'.reloc', 0x4000, relocs, 0x42000040)]
    hdr_size, raw = 0x400, []
    off = hdr_size
    for name, rva, blob, fl in secs:
        raw.append((name, rva, blob, fl, off))
        off += (len(blob) + 0x1ff) & ~0x1ff
    opt = bytearray(224)
    struct.pack_into('<H', opt, 0, 0x10b)
    struct.pack_into('<I', opt, 28, 0x00400000)            # ImageBase
    struct.pack_into('<I', opt, 92, 16)                    # NumberOfRvaAndSizes
    struct.pack_into('<II', opt, 96 + 8 * 5, 0x4000, len(relocs))   # base relocation directory
    h = bytearray(hdr_size)
    h[0:2] = b'MZ'
    struct.pack_into('<I', h, 0x3c, 0x80)
    h[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', h, 0x84, 0x14c, len(secs), 0, 0, 0, len(opt), 0x102)
    h[0x98:0x98 + len(opt)] = opt
    sh = 0x98 + len(opt)
    for k, (name, rva, blob, fl, o) in enumerate(raw):
        struct.pack_into('<8sIIIIIIHHI', h, sh + 40 * k, name, len(blob), rva, (len(blob) + 0x1ff) & ~0x1ff, o,
                         0, 0, 0, 0, fl)
    img = bytearray(h)
    for name, rva, blob, fl, o in raw:
        img += blob.ljust((len(blob) + 0x1ff) & ~0x1ff, b'\0')
    Path(path).write_bytes(bytes(img))


t = Path(tempfile.mkdtemp(prefix='exeoracle.'))
pe32(t / 'x.exe')
import exeimage  # noqa: E402
from coffobj import REL32, DIR32  # noqa: E402

try:
    exeimage.PE(t / 'x.exe')
    check(False, 'the sha256 pin refuses another image')
except ValueError as e:
    check('sha256' in str(e), 'the sha256 pin refuses another image')
pe = exeimage.PE(t / 'x.exe', check=False)
check(pe.base == 0x00400000 and len(pe.sections) == 4 and pe.relocs == {0x00401001, 0x00401007},
      'PE: base, sections, HIGHLOW fixups %s' % sorted(map(hex, pe.relocs)))
unit = dict(source='src/x.cpp', functions=[dict(address='0x00401000', symbol='?f@@YAHXZ', size=19),
                                           dict(address='0x00401100', symbol='?g@@YAHXZ', size=3)], symbols={})
img = exeimage.ExeImage(pe, unit)
f = img.funcs[0x00401000]
check(f['code'] == pe.read(0x00401000, 19) and not f['unresolved'] and not img.errors, 'code is the image bytes')
check((1, DIR32, 0x00402000, '') in f['relocs'] and (7, DIR32, 0x00403000, '') in f['relocs'],
      'DIR32 from the base relocations, target = the stored word')
check((12, REL32, 0x00401100, '') in f['relocs'] and not any(r[1] == REL32 and r[2] == 0x00401011 for r in f['relocs']),
      'REL32 for the call out of the function; the jump inside it is not one')
lit, wr = img.literal_bytes(), img.writable_initial()
check(lit.get(0x00402000, b'').startswith(b'hello\0') and 0x00403000 not in lit, 'read-only targets pinned, writable not')
check(wr.get(0x00403000, b'')[:4] == struct.pack('<I', 0x11223344), 'writable targets seed with the initial value')
check(lit.get(exeimage.TLS_INDEX) == b'\0\0\0\0', '__tls_index pinned to 0')
bad = exeimage.ExeImage(pe, dict(source='s', functions=[dict(address='0x00402000', symbol='d', size=4)]))
check(bad.errors and not bad.funcs, 'an extent outside the code section is an error, not a function')

# recon_fuzz.py: targets (in the tree, not in the tree, no native object) and the report's verdicts
tree = t / 'tree' / 'build'; tree.mkdir(parents=True)
(tree / 'units.json').write_text(json.dumps(dict(units=[dict(source='src/x.cpp', functions=unit['functions'])])))
obj = t / 'obj'; obj.mkdir()
(obj / 'src_x.cpp.o').write_bytes(b'x')
rd = t / 'recon'
(rd / 'x').mkdir(parents=True)
(rd / 'x' / 'recon.json').write_text(json.dumps(dict(unit='src/x.cpp', functions=[
    dict(address='0x00401000', symbol='?f@@YAHXZ', size=19), dict(address='0x00401100', symbol='?g@@YAHXZ', size=3),
    dict(address='0x00405000', symbol='?h@@YAHXZ', size=8)])))
(rd / 'y').mkdir()
(rd / 'y' / 'recon.json').write_text(json.dumps(dict(unit='src/x.cpp', functions=[
    dict(address='0x00401100', symbol='?g@@YAHXZ', size=3)])))
cat = t / 'catalog.jsonl'
with open(cat, 'w') as h:
    for a in ('00401000', '00401100'):
        h.write(json.dumps(dict(unit=0, source='src/x.cpp', address=a, symbol='s', size=3, calls=[], tails=[],
                                indirect=0, x87=0, sse=0, fs=False, unresolved=0)) + '\n')
env = dict(os.environ, FNVNX_TREE=str(t / 'tree'), FNVNX_A64OBJ=str(obj), FNVNX_DFWORK=str(t / 'work'))
r = subprocess.run([sys.executable, str(TOOLS / 'recon_fuzz.py'), 'targets', str(rd), str(cat), str(t / 'targets.jsonl'),
                    str(t / 'plan.json')], cwd=TOOLS, env=env, capture_output=True, text=True)
tg = [json.loads(x) for x in open(t / 'targets.jsonl')] if r.returncode == 0 else []
plan = json.load(open(t / 'plan.json')) if r.returncode == 0 else []
check(r.returncode == 0 and sorted(x['address'] for x in tg) == ['00401000', '00401100', '00401100'],
      'targets: the recons\' functions in the tree (%s)' % (r.stderr[-300:] or r.stdout.strip()))
check(any('not in this head' in fn.get('not_run', '') for e in plan for fn in e['functions']),
      'a function missing from the tree is "not run" with the reason')
camp = t / 'camp'; camp.mkdir()
with open(camp / 'results.jsonl', 'w') as h:
    h.write(json.dumps(dict(address='00401000', status='ok', execs=200, divergences=[])) + '\n')
    h.write(json.dumps(dict(address='00401100', status='ok', execs=200, divergences=[])) + '\n')
r = subprocess.run([sys.executable, str(TOOLS / 'recon_fuzz.py'), 'report', str(t / 'plan.json'), str(camp),
                    str(t / 'r.md'), str(t / 'r.json')], cwd=TOOLS, env=env, capture_output=True, text=True)
s = json.load(open(t / 'r.json')) if r.returncode == 0 else {}
v = {x['branch']: x['verdict'] for x in s.get('records', [])}
check(v == {'recon/x': 'not-accepted', 'recon/y': 'fuzz-clean'} and s.get('functions', {}).get('clean') == 3,
      'report: a recon is fuzz-clean only when every function is clean (%s)' % (v or r.stderr[-300:]))
with open(camp / 'results.jsonl', 'a') as h:
    h.write(json.dumps(dict(address='00401100', status='ok', execs=9,
                            divergences=[dict(kind='ret', cause='x87', soft=False)])) + '\n')
subprocess.run([sys.executable, str(TOOLS / 'recon_fuzz.py'), 'report', str(t / 'plan.json'), str(camp),
                str(t / 'r.md'), str(t / 'r.json')], cwd=TOOLS, env=env, capture_output=True, text=True)
s = json.load(open(t / 'r.json'))
check({x['branch']: x['verdict'] for x in s['records']}.get('recon/y') == 'not-accepted' and s['functions']['hard'] >= 1
      and 'fnvnx-recon-fuzz' in (t / 'r.md').read_text(), 'report: a hard divergence refuses the recon')

# recon_fuzz.py queue: the recon fleet's fuzz-queue.tsv (rows name the containing exe functions)
q = t / 'fuzz-queue.tsv'
q.write_text('source\tname\tkind\texe_addresses\tconvention\tseed_hints\tnote\n'
             'recon/x\t_ZN1A1fEv\tinline-confirm\t0x00401000\tmember\tseeds\t0x00401005 (in 0x00401000)\n'
             'recon/y\t_ZN1B1gEv\tpending-fuzz:no-exe-copy\t\tmember\tfuzz the callers\tnone\n'
             'recon/z\t_ZN1C1hEv\tinline-confirm\t0x00499000\tmember\tseeds\t\n')
r = subprocess.run([sys.executable, str(TOOLS / 'recon_fuzz.py'), 'queue-units', str(q)], cwd=TOOLS, env=env,
                   capture_output=True, text=True)
check(r.returncode == 0 and r.stdout.strip() == '0', 'queue-units: the units of the queue\'s addresses (%s)' % (r.stdout or r.stderr)[-200:])
r = subprocess.run([sys.executable, str(TOOLS / 'recon_fuzz.py'), 'queue', str(q), str(cat), str(t / 'qt.jsonl'),
                    str(t / 'qp.json')], cwd=TOOLS, env=env, capture_output=True, text=True)
qp = json.load(open(t / 'qp.json')) if r.returncode == 0 else []
check(r.returncode == 0 and [json.loads(x)['address'] for x in open(t / 'qt.jsonl')] == ['00401000']
      and 'error' in qp[1] and 'not_run' in qp[2]['functions'][0], 'queue: one target; no address / not in the tree reported (%s)' % r.stderr[-200:])
r = subprocess.run([sys.executable, str(TOOLS / 'recon_fuzz.py'), 'report', str(t / 'qp.json'), str(camp),
                    str(t / 'q.md'), str(t / 'q.json')], cwd=TOOLS, env=env, capture_output=True, text=True)
qs = json.load(open(t / 'q.json')) if r.returncode == 0 else {}
check([x['verdict'] for x in qs.get('records', [])] == ['fuzz-clean', 'not-accepted', 'not-accepted'],
      'queue report: a row is fuzz-clean when its exe functions are (%s)' % [x['verdict'] for x in qs.get('records', [])])

print('test_exe_oracle: %d failures' % len(FAIL))
sys.exit(1 if FAIL else 0)
