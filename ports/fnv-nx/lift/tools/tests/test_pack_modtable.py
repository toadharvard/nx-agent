"""Rule MODTABLE: the module set and every base of the lifted pack come from the loader's module table (contract/modules.py output, modules.tsv), nowhere else.
  liftgen.check_images: the image set is the exe plus EVERY row of the table; each image was lifted from the file its row pins, at the row's assigned base, and its universe lies in the row's range.
  packentries.stamp: entries.tsv ends with `# module_table sha256 <hex of modules.tsv>`; the entries stage is red on a missing, repeated, misplaced or different stamp.
Negative cases are the defects this rule closes: a base of another table (galaxy.dll once static: libvorbis at 0x2130000), a missing row, a duplicate image, a foreign file, an entry outside the range, a stale stamp.   test_pack_modtable.py"""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import hashlib, json, os, sys, tempfile
LIFT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, LIFT); sys.path.insert(0, os.path.join(LIFT, 'tools'))
import fnvnx.lift.tools.liftgen as liftgen; import fnvnx.lift.tools.packentries as packentries

fails = 0
def check(name, ok):
    global fails
    print(('ok   ' if ok else 'FAIL ') + name); fails += not ok
def dies(f, *a):
    try: f(*a)
    except SystemExit as e: return str(e.code)
    return None

W = tempfile.mkdtemp(prefix='modtable-')
def sha(b): return hashlib.sha256(b).hexdigest()
ROWS = [('atimgpud.dll', sha(b'a'), 0x1410000, 0x29000), ('libvorbis.dll', sha(b'v'), 0x1ce0000, 0x133000)]
EXE = sha(b'exe')
tsv = '# name\tsha256\tsize\tpreferred_base\tassigned_base\tsize_of_image\thas_tls\tentry_rva\tstatic\tload_index\n' + ''.join(
    '%s\t%s\t1\t10000000\t%08x\t%08x\t0\t0\t0\t\n' % (n, h, b, z) for n, h, b, z in ROWS)
MOD = os.path.join(W, 'modules.tsv'); open(MOD, 'w').write(tsv)
msha, table = liftgen.module_table(MOD)
check('module_table: sha256 of the file and the rows', msha == sha(tsv.encode()) and sorted(table) == ['atimgpud.dll', 'libvorbis.dll'] and table['libvorbis.dll'].base == 0x1ce0000)

def img(name, exe_sha, base, ents):
    d = os.path.join(W, name); os.makedirs(d, exist_ok=True)
    json.dump(dict(exe_sha256=exe_sha, base=base), open(d + '/inputs_0.json', 'w'))
    return d, set(ents)
def good():
    return [img('exe', EXE, None, [0x401000]), img('a', ROWS[0][1], '01410000', [0x1411005]), img('v', ROWS[1][1], '01ce0000', [0x1ce1000])]
check('every row has an image at its base: accepted', dies(liftgen.check_images, good(), table, EXE) is None)
g = good()[:2]; m = dies(liftgen.check_images, g, table, EXE)
check('a row without an image is refused (named)', m is not None and 'libvorbis.dll: a row of the module table with no image' in m)
g = good(); g[2] = img('v', ROWS[1][1], '02130000', [0x2131000]); m = dies(liftgen.check_images, g, table, EXE)
check('an image lifted at another base (the stale-pack defect) is refused', m is not None and 'lifted at base 02130000, the table assigns 01ce0000' in m and 'outside' in m)
g = good(); g[2] = img('v', ROWS[1][1], '01ce0000', [0x1ce1000, 0x1e20000]); m = dies(liftgen.check_images, g, table, EXE)
check('an entry outside the row range is refused', m is not None and '1 universe entries outside' in m)
g = good() + [img('v2', ROWS[1][1], '01ce0000', [0x1ce2000])]; m = dies(liftgen.check_images, g, table, EXE)
check('the same row twice is refused', m is not None and 'given twice' in m)
g = good() + [img('x', sha(b'galaxy'), '01af0000', [0x1af1000])]; m = dies(liftgen.check_images, g, table, EXE)
check('an image whose file is not a row (galaxy.dll, declared now) is refused', m is not None and 'is not a row of the module table' in m)
g = good(); g[0] = img('exe', EXE, '00400000', [0x401000]); m = dies(liftgen.check_images, g, table, EXE)
check('the exe image carries no base', m is not None and 'exe image carries a base' in m)

def pack(stamps, tail=''):
    p = tempfile.mkdtemp(prefix='pk-', dir=W)
    open(p + '/entries.tsv', 'w').write('00401000\tL_00401000\tcode\n# table_id ' + 'ab' * 32 + '\n' + ''.join('# module_table sha256 %s\n' % s for s in stamps) + tail)
    return p
check('packentries: the stamp of the loader table passes', dies(packentries.stamp, pack([msha]) + '/entries.tsv', MOD) is None)
m = dies(packentries.stamp, pack([sha(b'old')]) + '/entries.tsv', MOD); check('packentries: a stale stamp is red (both hashes named)', m is not None and 'stale pack' in m and msha in m)
m = dies(packentries.stamp, pack([]) + '/entries.tsv', MOD); check('packentries: a missing stamp is red', m is not None and '0 module_table stamps' in m)
m = dies(packentries.stamp, pack([msha, msha]) + '/entries.tsv', MOD); check('packentries: two stamps are red', m is not None and '2 module_table stamps' in m)
m = dies(packentries.stamp, pack([msha], '00401040\tL_00401040\tcode\n') + '/entries.tsv', MOD); check('packentries: a stamp that is not the last line is red', m is not None and 'not the last line' in m)
print('test_pack_modtable: %d failed' % fails); sys.exit(1 if fails else 0)
