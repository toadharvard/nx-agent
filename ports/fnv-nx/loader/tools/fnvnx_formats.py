"""File formats shared by S2 (link) and S3 (data image). Specified in docs/DATAIMAGE.md.

Address map, text form (written by the S2 link step, read by the offline tools):

    # fnvnx addrmap v1
    <orig VA hex>\t<kind>\t<flags>\t<native>

  kind   FUNC (function defined in the decomp) | TEXT (referenced .text address without definition) |
         MID (referenced address inside a defined function) |
         VTABLE (orig is an image vftable address point, native the Itanium vtable address point of that class)
  flags  '-' or a comma list of: init (??__E dynamic initializer), atexit (??__F), bridge (native is an ABI bridge),
         deldtor (orig is an MSVC deleting destructor, native the Itanium D0: S2 kind G)
  native the native symbol, '-' when there is no native implementation, or a literal '0x...' value (tests)

Address map, binary form (linked into the program by S2, read by loader/src/codeptr.c):
    "FNVAMAP1", u32 version=1, u32 count, u32 entry_size=12, u32 flags=0,
    count x {u32 orig, u32 native, u32 info}   sorted by strictly increasing orig
    info = kind (FUNC=1, TEXT=2, MID=3, VTABLE=4) | init 0x100 | atexit 0x200 | bridge 0x400 | deldtor 0x800

S2's own address map (tools/link/fnvlink.py, docs/LINK.md) is read with AddrMap.read_s2_tsv: kinds F/C/X -> FUNC,
G -> FUNC + deldtor, V -> VTABLE (native '<link_symbol>+<offset>'), E -> TEXT (no native); its runtime form
{orig, void *native} + fnvnx_addrmap_kinds is converted by fnvnx_codemap_from_s2 in the loader.

Fixup list (written by scan_codeptrs.py or fnvnx_fixups_serialize, read by fnvnx_fixups_parse):
    "FNVFIX01", u32 version=1, u32 count, u32 pe timestamp, u32 pe size_of_image, u32 entry_size=12, u32 0,
    count x {u32 loc, u32 target, u8 class, u8 confidence, u16 aux}   sorted by loc
"""
import struct

KINDS = {'FUNC': 1, 'TEXT': 2, 'MID': 3, 'VTABLE': 4}
KIND_NAMES = {v: k for k, v in KINDS.items()}
FLAGS = {'init': 0x100, 'atexit': 0x200, 'bridge': 0x400, 'deldtor': 0x800}

CLASSES = {
    1: 'vtable_col', 2: 'vtable_hint', 3: 'tls', 4: 'reloc', 5: 'run', 6: 'isolated', 7: 'vptr',
    0x81: 'rej_string', 0x82: 'rej_round', 0x83: 'unknown', 0x84: 'mid',
}
CLASS_IDS = {v: k for k, v in CLASSES.items()}
CONF_NONE, CONF_LOW, CONF_MED, CONF_HIGH = 0, 1, 2, 3


class AddrMap:
    """orig -> (info, native) where native is a symbol name, an int, or None."""

    def __init__(self, entries=None):
        self.entries = dict(entries or {})

    def kind(self, orig):
        e = self.entries.get(orig)
        return None if e is None else e[0] & 0xff

    def __len__(self):
        return len(self.entries)

    @classmethod
    def read_tsv(cls, path):
        m = cls()
        with open(path) as f:
            for line in f:
                if not line.strip() or line.startswith('#'):
                    continue
                orig, kind, flags, native = line.rstrip('\n').split('\t')[:4]
                info = KINDS[kind]
                if flags != '-':
                    for fl in flags.split(','):
                        info |= FLAGS[fl]
                if native == '-':
                    native = None
                elif native.startswith('0x'):
                    native = int(native, 16)
                m.entries[int(orig, 16)] = (info, native)
        return m

    @classmethod
    def read_s2_tsv(cls, path):
        """S2's addrmap.tsv (# fnvnx-addrmap 1: address, kind F/C/X/G/V/E, link_symbol, native_symbol, object, note,
        offset, d1_symbol, d1_native; columns after the sixth are optional). V rows (image vftables) become VTABLE
        entries whose native is the address point '<link_symbol>+<offset>' (offset 8 when the column is absent), E
        rows (referenced .text without definition) TEXT entries without native."""
        m = cls()
        with open(path) as f:
            for line in f:
                if not line.strip() or line.startswith('#'):
                    continue
                col = line.rstrip('\n').split('\t')
                sym = col[2] if len(col) > 2 and col[2] else None
                if col[1] == 'V':
                    off = int(col[6]) if len(col) > 6 and col[6] else 8
                    info, sym = KINDS['VTABLE'], (sym and '%s+%d' % (sym, off))
                elif col[1] == 'E':
                    info, sym = KINDS['TEXT'], None
                else:
                    info = KINDS['FUNC'] | (FLAGS['deldtor'] if col[1] == 'G' else 0)
                m.entries[int(col[0], 16)] = (info, sym)
        return m

    def merge(self, other):
        """add other's entries where self has none (S2 first, units.json/S4 for TEXT/MID)"""
        for k, v in other.entries.items():
            self.entries.setdefault(k, v)
        return self

    def write_tsv(self, path):
        with open(path, 'w') as f:
            f.write('# fnvnx addrmap v1\n')
            for orig in sorted(self.entries):
                info, native = self.entries[orig]
                fl = ','.join(k for k, v in FLAGS.items() if info & v) or '-'
                nat = '-' if native is None else ('0x%08x' % native if isinstance(native, int) else native)
                f.write('0x%08x\t%s\t%s\t%s\n' % (orig, KIND_NAMES[info & 0xff], fl, nat))

    def to_bin(self):
        """Binary form; symbolic natives become 0 (only S2's assembler output can carry real addresses)."""
        out = [b'FNVAMAP1', struct.pack('<IIII', 1, len(self.entries), 12, 0)]
        for orig in sorted(self.entries):
            info, native = self.entries[orig]
            out.append(struct.pack('<III', orig, native if isinstance(native, int) else 0, info))
        return b''.join(out)


def write_fixups(path, pe, fixups):
    """fixups: [(loc, target, klass, conf, aux)] sorted by loc."""
    with open(path, 'wb') as f:
        f.write(fixups_bytes(pe, fixups))


def fixups_bytes(pe, fixups):
    out = [b'FNVFIX01', struct.pack('<IIIIII', 1, len(fixups), pe.timestamp, pe.size_of_image, 12, 0)]
    out += [struct.pack('<IIBBH', *fx) for fx in fixups]
    return b''.join(out)


def read_fixups(path):
    b = open(path, 'rb').read()
    if b[:8] != b'FNVFIX01':
        raise ValueError('not a fixup list')
    ver, n, ts, soi, esize, _ = struct.unpack_from('<IIIIII', b, 8)
    return {'timestamp': ts, 'size_of_image': soi,
            'fixups': [struct.unpack_from('<IIBBH', b, 32 + 12 * i) for i in range(n)]}
