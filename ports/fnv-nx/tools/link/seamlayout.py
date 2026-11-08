#!/usr/bin/env python3
"""seamlayout.py - static LAYOUT seam check: the native size of every class against what the exe says about it.

    python3 tools/link/seamlayout.py <link root> <exe> [-o seams_layout.tsv] [--tree T] [--topdown topdown.tsv]
                                     [--jobs N] [--shard i/n] [--no-probe]
    from seamlayout import check_layout;  rows = check_layout(link_root, exe_path)     # seamcheck.py imports this

The link (<link>/out/addrmap.tsv, <link>/fixed/*.o) says which exe address is which native symbol and class; the
exe's x86 code says how big the class really is. A class whose native layout is smaller than the exe's lets the exe
code (which walks the object by its own offsets) run past the native object: StandardLibrarySingletonObject is 1 byte
natively and 0x98 in the exe. Nothing here runs the game; the exe is only read.

Exe evidence (x86 disassembly, capstone; function bounds are the addrmap's addresses):
  new     `push N; call <operator new>` followed (data flow of the returned pointer through registers and ebp
          slots, at most 40 instructions, no other allocation) by a call of a constructor of class C: exact sizeof.
          Allocators: 0x00401000, 0x00aa13e0 (cdecl, size on the stack) and 0x00aa3e40 (MemoryManager::Allocate,
          thiscall, size on the stack), see DEFAULT_ALLOCATORS; --alloc adds more.
  static  `mov ecx, <data address>; call <constructor of C>`: a global object of C. The extent is the distance to
          the next known data symbol (refs.tsv data rows and the other constructed globals); that is an upper bound
          of the size (padding and unreferenced neighbours are inside it), so it is a mismatch only when the exe
          code also reads the object's tail (a reference within 4 bytes of the extent's end, the "tight" case) and
          a lower bound otherwise: `suspect` (native smaller than the extent, tail not referenced) is reported
          but not counted as a mismatch.
  maxoff  the highest member offset (+ access width) that member functions of C touch through `this` (ecx at entry
          and its copies in registers and ebp slots): a lower bound of the size.

Native size, first source that knows the class: DWARF DW_AT_byte_size of the objects in <link>/fixed (none in
the normal link, which is compiled without -g), <link>/out/layouts.tsv (aggregates passed by value), a layout probe
(layout.py's probe_unit: sizeof in the defining unit; needs --tree, the prepared source tree). Results of probes are
cached next to the output (seams_layout_probe*.tsv). A class nothing knows gets verdict `unknown`.

Rows (COLUMNS): class, native size (decimal), native source, exe size (decimal), evidence, sites, example (exe
address), verdict, boot (1: constructed by a function the topdown list names, else 0), note.
Verdicts: mismatch (native < exe for new/static-tight, native < max offset + width for maxoff), larger (new/static:
native > exe, the native constructor writes past the exe's allocation), suspect (static, see above), ok, unknown.
"""
import argparse
import bisect
import collections
import concurrent.futures
import os
import re
import struct
import subprocess
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

COLUMNS = ['class', 'native_size', 'native_src', 'exe_size', 'evidence', 'sites', 'example', 'verdict', 'boot',
           'note']
# operator new (FormHeap_Allocate), the sized allocation helper, MemoryManager::Allocate (docs/DIFFFUZZ.md)
DEFAULT_ALLOCATORS = (0x00401000, 0x00aa13e0, 0x00aa3e40)
NEW_WINDOW = 40
CXXFILT = os.environ.get('CXXFILT', 'c++filt')
READELF = os.environ.get('READELF', 'aarch64-linux-gnu-readelf')
SYNTH = re.compile(r'FUN_[0-9a-f]{8}|(?:^|::)Holder[0-9a-f]{8}|_shell$|_This\d*$|_Class$|_cls$|^S[0-9a-f]{8}_')


# ------------------------------------------------------------------------------------------------ the exe image

class Image:
    """The sections of a PE32 file: read(va, n), executable and writable ranges."""

    def __init__(self, data):
        self.data = data
        pe = struct.unpack_from('<I', data, 0x3c)[0]
        if data[pe:pe + 4] != b'PE\0\0':
            raise ValueError('not a PE file')
        nsec, = struct.unpack_from('<H', data, pe + 6)
        osz, = struct.unpack_from('<H', data, pe + 20)
        self.base, = struct.unpack_from('<I', data, pe + 24 + 28)
        self.sections = []   # (name, va, vsize, raw offset, raw size, characteristics)
        so = pe + 24 + osz
        for i in range(nsec):
            name, vs, rva, rs, ro, _a, _b, _c, _d, ch = struct.unpack_from('<8sIIIIIIHHI', data, so + 40 * i)
            self.sections.append((name.rstrip(b'\0').decode('latin-1'), self.base + rva, vs or rs, ro, rs, ch))

    def read(self, va, n):
        for _, sva, vs, ro, rs, _ in self.sections:
            if sva <= va < sva + vs:
                off = va - sva
                if off >= rs:
                    return b'\0' * n
                return self.data[ro + off:ro + min(rs, off + n)]
        return b''

    def code(self):
        return [(s[1], s[1] + min(s[2], s[4])) for s in self.sections if s[5] & 0x20000000]

    def writable(self):
        return [(s[1], s[1] + s[2]) for s in self.sections if s[5] & 0x80000000 and not s[5] & 0x20000000]


# ------------------------------------------------------------------------------------------------ the link

def demangle(names):
    """{mangled: demangled} through c++filt."""
    names = sorted(set(names))
    out = {}
    for i in range(0, len(names), 20000):
        chunk = names[i:i + 20000]
        r = subprocess.run([CXXFILT], input='\n'.join(chunk) + '\n', capture_output=True, text=True, check=True)
        lines = r.stdout.split('\n')
        for n, d in zip(chunk, lines):
            out[n] = d
    return out


_OPERATOR = re.compile(r'operator\s*(?:\(\)|<<=?|>>=?|<=>|<=?|>=?|->\*?|\[\]|[^\s(<>\[]+)')


def split_member(dem):
    """Demangled function -> (class, function name) or None: the qualified name before the parameter list, split at
    the last top-level `::`. Class names have no spaces."""
    s = _OPERATOR.sub(lambda m: 'operator_' + str(abs(hash(m.group(0))) % 997), dem)
    s = re.sub(r'^(?:virtual |static |inline )+', '', s)
    s = re.sub(r'\{[^{}]*\}', 'L', s)
    depth, cut = 0, None
    for i, c in enumerate(s):
        if c == '<':
            depth += 1
        elif c == '>':
            depth -= 1
        elif c == '(' and depth == 0:
            cut = i
            break
    if cut is None:
        return None
    name = s[:cut].strip()
    if ' ' in name.replace(', ', ',').replace(' *', '*').replace(' &', '&'):
        name = name.replace(', ', ',')
        name = name.split(' ')[-1]
    parts, depth, cur = [], 0, ''
    i = 0
    while i < len(name):
        c = name[i]
        if c == '<':
            depth += 1
        elif c == '>':
            depth -= 1
        if depth == 0 and name.startswith('::', i):
            parts.append(cur)
            cur = ''
            i += 2
            continue
        cur += c
        i += 1
    parts.append(cur)
    if len(parts) < 2:
        return None
    return '::'.join(parts[:-1]).replace(' ', ''), parts[-1].replace(' ', '')


def is_ctor(cls, fn):
    last = cls.rsplit('::', 1)[-1]
    return fn == last or fn == re.sub(r'<.*$', '', last)


class Link:
    """addrmap.tsv: function addresses with their native symbol, object, class and constructor-ness."""

    def __init__(self, root):
        self.root = root
        self.rows = []   # (addr, kind, native, object)
        with open(os.path.join(root, 'out', 'addrmap.tsv')) as f:
            for line in f:
                if line.startswith('#') or line.startswith('kind'):
                    continue
                p = line.rstrip('\n').split('\t')
                if len(p) < 5:
                    continue
                try:
                    self.rows.append((int(p[0], 16), p[1], p[3], p[4]))
                except ValueError:
                    pass
        self.starts = sorted({r[0] for r in self.rows})
        self.unit_of = {}   # addr -> a unit (src/...cpp) that references a symbol resolved to it
        self.sym_unit = {}  # (addr, native symbol) -> a unit that references it
        self.class_objs = collections.defaultdict(list)   # class -> object files / units with code or uses of it
        pairs = collections.OrderedDict()   # (addr, native symbol) -> object (refs.tsv has no object: '')
        for addr, kind, native, obj in self.rows:
            if kind in ('F', 'C', 'X', 'G') and native.startswith('_Z'):
                pairs[(addr, native)] = obj
        # refs.tsv: the native symbols the objects reference, with the exe address they were resolved to; it names
        # the constructors that no object defines (std::StandardLibrarySingletonObject::...C2Ev at 0x00af30a0)
        try:
            with open(os.path.join(root, 'out', 'refs.tsv')) as f:
                for line in f:
                    p = line.rstrip('\n').split('\t')
                    if len(p) > 4 and p[2] == 'code' and p[1].startswith('_Z') and len(p[4]) == 8:
                        try:
                            pairs.setdefault((int(p[4], 16), p[1]), '')
                            self.unit_of.setdefault(int(p[4], 16), p[0].split('#')[0])
                            self.sym_unit.setdefault((int(p[4], 16), p[1]), p[0].split('#')[0])
                        except ValueError:
                            pass
        except OSError:
            pass
        dem = demangle([n for _, n in pairs])
        cands = collections.defaultdict(set)   # addr -> {(class, function)}
        self.obj_of = {}
        for (addr, native), obj in pairs.items():
            m = split_member(dem.get(native, ''))
            if not m:
                continue
            cands[addr].add(m)
            if obj:
                self.obj_of.setdefault(addr, obj)
                if obj not in self.class_objs[m[0]]:
                    self.class_objs[m[0]].append(obj)
            u = self.sym_unit.get((addr, native))
            if u:
                f = u.replace('/', '_') + '.o'
                if f not in self.class_objs[m[0]]:
                    self.class_objs[m[0]].append(f)
        self.ctors = {}
        classes = set()
        for addr, cs in cands.items():
            for cls, fn in cs:
                if is_ctor(cls, fn) or fn.startswith('~'):
                    classes.add(cls)
        self.func_class = {}
        for addr, cs in cands.items():
            cs = sorted(cs, key=lambda c: (c[0].startswith('fnv_'), len(c[0]), c[0]))
            ct = [c for c in cs if is_ctor(*c)]
            if ct:
                self.ctors[addr] = ct[0][0]
            for cls, fn in cs:
                if cls in classes:
                    self.func_class.setdefault(addr, cls)
        self.classes = classes


def crt_starts(root_hint=None):
    path = os.path.join(os.path.dirname(HERE), '..', 'platform', 'crt', 'crt_symbols.tsv')
    out = set()
    try:
        for line in open(path):
            if line.startswith('0x'):
                out.add(int(line.split('\t')[0], 16))
    except (OSError, ValueError):
        pass
    return out


# ------------------------------------------------------------------------------------------------ exe scanning

_IMM = re.compile(r'^-?(?:0x[0-9a-f]+|\d+)$')
_MEM = re.compile(r'\[([^\]]*)\]')
_REGS = {'eax', 'ecx', 'edx', 'ebx', 'esi', 'edi', 'ebp', 'esp'}
_SIZES = {'byte': 1, 'word': 2, 'dword': 4, 'qword': 8, 'xmmword': 16, 'tbyte': 10, 'fword': 6, 'oword': 16}
_NOWRITE = ('cmp', 'test', 'push', 'jmp', 'call', 'ret', 'retn', 'nop', 'int3', 'bt', 'fld', 'fst', 'fadd', 'fsub',
            'fmul', 'fdiv', 'fcom', 'fucom', 'fild', 'fist', 'fiadd', 'fisub', 'fimul', 'fidiv', 'fcomp', 'fucomp',
            'cmpxchg', 'comis', 'ucomis', 'prefetch', 'rep', 'lock', 'movs', 'stos', 'scas', 'cmps', 'movq', 'movd',
            'movss', 'movsd', 'movaps', 'movups', 'movdq', 'cvt', 'add', 'sub', 'xor', 'and', 'or', 'imul')
_WRITE_ALWAYS = ('mov', 'lea', 'movzx', 'movsx', 'pop', 'xchg', 'xor', 'sub', 'add', 'and', 'or', 'imul', 'shl',
                 'shr', 'sar', 'inc', 'dec', 'neg', 'not', 'adc', 'sbb', 'cmov', 'set', 'bsf', 'bsr', 'rol', 'ror',
                 'cdq', 'cwde', 'mul', 'div', 'idiv', 'lods', 'setcc')


def _split_ops(o):
    out, depth, cur = [], 0, ''
    for c in o:
        if c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
        if c == ',' and depth == 0:
            out.append(cur.strip())
            cur = ''
        else:
            cur += c
    if cur.strip():
        out.append(cur.strip())
    return out


def _imm(s):
    if _IMM.match(s):
        return int(s, 16) if '0x' in s else int(s)
    return None


def _mem_parse(op):
    """'dword ptr [ebp - 0x4c]' -> (size, base, disp, indexed, key) or None. key: 'ebp-0x4c' for a slot."""
    m = _MEM.search(op)
    if not m:
        return None
    inner = m.group(1).replace(' ', '')
    pre = op[:m.start()]
    size = 4
    for w, n in _SIZES.items():
        if pre.startswith(w):
            size = n
            break
    if 'fs:' in pre or 'gs:' in pre or 'es:' in pre:
        return None
    terms = re.findall(r'([+-]?)([^+-]+)', inner)
    base, disp, indexed = None, 0, False
    for sign, t in terms:
        if '*' in t:
            indexed = True
        elif t in _REGS:
            if base is None:
                base = t
            else:
                indexed = True
        else:
            v = _imm(t)
            if v is None:
                return None
            disp += -v if sign == '-' else v
    return size, base, disp, indexed, inner


def back_size(ins, k):
    """The constant pushed for the call at ins[k]: the nearest push within 8 instructions; a pushed register is
    resolved by a `mov reg, imm` just before."""
    for j in range(k - 1, max(k - 9, -1), -1):
        m, o = ins[j][2], ins[j][3]
        if m == 'push':
            v = _imm(o)
            if v is not None:
                return v
            if o in _REGS:
                for i in range(j - 1, max(j - 5, -1), -1):
                    ops = _split_ops(ins[i][3]) if ins[i][2] == 'mov' else None
                    if ops and ops[0] == o:
                        return _imm(ops[1])
            return None
        if m in ('call', 'jmp', 'ret', 'retn') or m.startswith('j'):
            return None
    return None


class Scan:
    def __init__(self):
        self.new_sites = []      # (site, func, size, ctor)
        self.static_sites = []   # (site, func, data address, ctor)
        self.maxoff = {}         # class -> [max end, accesses, example function, example offset]
        self.refs = set()        # data addresses referenced by absolute operands
        self.ro_ranges = []      # read-only data (vftables live there)
        self.derived = 0         # constructor calls inside a derived class's inlined constructor (skipped)


def _track_ptr(ins, k, ctors, allocators):
    """Index of the constructor call that takes the pointer returned by the allocation call at ins[k]."""
    ptr = {'eax'}
    for j in range(k + 1, min(k + NEW_WINDOW, len(ins))):
        m, o = ins[j][2], ins[j][3]
        if m == 'call':
            t = _imm(o)
            if t is not None and t in ctors and 'ecx' in ptr:
                return j, t
            if t in allocators:
                return None
            ptr -= {'eax', 'ecx', 'edx'}
            if not ptr:
                return None
        elif m in ('ret', 'retn', 'jmp'):
            return None
        elif m == 'mov':
            ops = _split_ops(o)
            if len(ops) != 2:
                continue
            dst, src = ops
            sm = _mem_parse(src)
            dm = _mem_parse(dst)
            skey = src if src in _REGS else (sm[4] if sm and sm[1] == 'ebp' and not sm[3] else None)
            dkey = dst if dst in _REGS else (dm[4] if dm and dm[1] == 'ebp' and not dm[3] else None)
            if skey in ptr and dkey:
                ptr.add(dkey)
            elif dkey in ptr:
                ptr.discard(dkey)
        elif m not in _NOWRITE or m in ('add', 'sub', 'xor', 'and', 'or', 'imul'):
            ops = _split_ops(o)
            if ops and ops[0] in ptr and m not in ('cmp', 'test', 'push'):
                ptr.discard(ops[0])
    return None


def _vptr_store_after(ins, j, sc, obj_addr=None):
    """True when one of the next 8 instructions after the constructor call at ins[j] stores a vftable address
    (an immediate in read-only data) at offset 0 of the object: the call is the base-class constructor inside the
    inlined constructor of a derived class, and the allocation size is the derived class's."""
    for i in range(j + 1, min(j + 9, len(ins))):
        m, o = ins[i][2], ins[i][3]
        if m in ('ret', 'retn', 'jmp') or m == 'call':
            return False
        if m == 'mov':
            ops = _split_ops(o)
            if len(ops) == 2 and '[' in ops[0]:
                v = _imm(ops[1])
                mp = _mem_parse(ops[0])
                if v is not None and mp and any(lo <= v < hi for lo, hi in sc.ro_ranges):
                    if (mp[1] and mp[2] == 0 and not mp[3]) or (mp[1] is None and obj_addr is not None
                                                                 and mp[2] == obj_addr):
                        return True
    return False


def _scan_function(ins, fva, member_class, ctors, allocators, data_ranges, sc):
    n = len(ins)
    for k in range(n):
        m, o = ins[k][2], ins[k][3]
        if m == 'call':
            t = _imm(o)
            if t in allocators:
                size = back_size(ins, k)
                if size:
                    hit = _track_ptr(ins, k, ctors, allocators)
                    if hit and _vptr_store_after(ins, hit[0], sc):
                        sc.derived += 1
                    elif hit:
                        sc.new_sites.append((ins[k][0], fva, size, hit[1]))
            elif t in ctors:
                for j in range(k - 1, max(k - 5, -1), -1):
                    if ins[j][2] == 'mov':
                        ops = _split_ops(ins[j][3])
                        if len(ops) == 2 and ops[0] == 'ecx':
                            v = _imm(ops[1])
                            if v is not None and any(lo <= v < hi for lo, hi in data_ranges):
                                if _vptr_store_after(ins, k, sc, v):
                                    sc.derived += 1
                                else:
                                    sc.static_sites.append((ins[k][0], fva, v, t))
                            break
                    elif ins[j][2] in ('call', 'jmp', 'ret'):
                        break
        # absolute data references
        if '0x' in o:
            for tok in re.findall(r'0x[0-9a-f]{6,8}', o):
                v = int(tok, 16)
                for lo, hi in data_ranges:
                    if lo <= v < hi:
                        sc.refs.add(v)
                        break
    if member_class:
        _this_uses(ins, fva, member_class, sc)


def _this_uses(ins, fva, cls, sc):
    this = {'ecx'}
    best = None
    count = 0
    for a, _sz, m, o in ins:
        ops = _split_ops(o) if o else []
        # accesses first (an instruction reads its operands before it writes the destination)
        for op in ops:
            mp = _mem_parse(op) if '[' in op else None
            if mp and mp[1] in this and 0 <= mp[2] < 0x100000:
                width = 1 if (m == 'lea' or mp[3]) else mp[0]
                end = mp[2] + width
                count += 1
                if best is None or end > best[0]:
                    best = (end, mp[2])
        if m == 'call':
            this -= {'eax', 'ecx', 'edx'}
            if not this:
                break
            continue
        if m == 'mov' and len(ops) == 2:
            dst, src = ops
            sm = _mem_parse(src) if '[' in src else None
            dm = _mem_parse(dst) if '[' in dst else None
            skey = src if src in _REGS else (sm[4] if sm and sm[1] == 'ebp' and not sm[3] else None)
            dkey = dst if dst in _REGS else (dm[4] if dm and dm[1] == 'ebp' and not dm[3] else None)
            if skey in this and dkey:
                this.add(dkey)
            elif dkey in this:
                this.discard(dkey)
            if not this:
                break
            continue
        if ops and ops[0] in this and (m in _WRITE_ALWAYS or m.startswith(('cmov', 'set', 'movz', 'movs'))) \
                and m not in ('cmp', 'test', 'push'):
            this.discard(ops[0])
            if not this:
                break
        elif m == 'xchg' and len(ops) == 2:
            this -= {ops[0], ops[1]}
        elif m in ('mul', 'div', 'idiv', 'cdq', 'imul') and len(ops) <= 1:
            this -= {'eax', 'edx'}
    if best:
        cur = sc.maxoff.get(cls)
        if cur is None or best[0] > cur[0]:
            sc.maxoff[cls] = [best[0], (cur[1] if cur else 0) + count, '%08x' % fva, best[1]]
        else:
            cur[1] += count


def scan_exe(image, link, extra_funcs=(), allocators=DEFAULT_ALLOCATORS):
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.skipdata = True
    starts = sorted(set(link.starts) | set(extra_funcs))
    data_ranges = image.writable()
    sc = Scan()
    sc.ro_ranges = [(s[1], s[1] + s[2]) for s in image.sections if not s[5] & 0xa0000000]
    allocators = set(allocators)
    for lo, hi in image.code():
        i0 = bisect.bisect_left(starts, lo)
        inside = [s for s in starts[i0:] if s < hi]
        if not inside or inside[0] > lo:
            inside.insert(0, lo)
        for i, s in enumerate(inside):
            e = inside[i + 1] if i + 1 < len(inside) else hi
            code = image.read(s, e - s)
            ins = list(md.disasm_lite(code, s))
            _scan_function(ins, s, link.func_class.get(s), link.ctors, allocators, data_ranges, sc)
    return sc


# ------------------------------------------------------------------------------------------------ native sizes

def _debug_info(path):
    """True when the ELF32/64 object has a .debug_info section."""
    with open(path, 'rb') as f:
        h = f.read(64)
        if h[:4] != b'\x7fELF':
            return False
        is64 = h[4] == 2
        if is64:
            shoff, = struct.unpack_from('<Q', h, 0x28)
            shentsize, shnum, shstrndx = struct.unpack_from('<HHH', h, 0x3a)
        else:
            shoff, = struct.unpack_from('<I', h, 0x20)
            shentsize, shnum, shstrndx = struct.unpack_from('<HHH', h, 0x2e)
        f.seek(shoff)
        tab = f.read(shentsize * shnum)
        sh = [struct.unpack_from('<IIQQQQ' if is64 else '<IIIIII', tab, i * shentsize) for i in range(shnum)]
        # (name, type, flags, addr, offset, size)
        if is64:
            stroff, strsize = sh[shstrndx][4], sh[shstrndx][5]
        else:
            stroff, strsize = sh[shstrndx][4], sh[shstrndx][5]
        f.seek(stroff)
        strs = f.read(strsize)
    for s in sh:
        end = strs.find(b'\0', s[0])
        if strs[s[0]:end] == b'.debug_info':
            return True
    return False


_DIE = re.compile(r'^ <(\d+)><[0-9a-f]+>: Abbrev Number: \d+ \((DW_TAG_\w+)\)')
_ATTR = re.compile(r'^\s+<[0-9a-f]+>\s+(DW_AT_\w+)\s*:\s*(.*)$')


def dwarf_sizes(obj):
    """{qualified class name: byte size} of the complete class/struct/union DIEs of one object file
    (`readelf --debug-dump=info`, which applies the relocations)."""
    if not _debug_info(obj):
        return {}
    r = subprocess.run([READELF, '--debug-dump=info', obj], capture_output=True, text=True, errors='replace')
    out = {}
    stack = []   # (depth, name or None, is scope)
    cur = None
    spec_names = {}

    def finish():
        if cur and cur['tag'] in ('DW_TAG_class_type', 'DW_TAG_structure_type', 'DW_TAG_union_type') \
                and cur.get('size') is not None and not cur.get('decl') and cur.get('name'):
            scopes = [n for _, n, sc in stack[:-1] if n and sc]
            q = '::'.join(scopes) + ('::' if scopes else '')
            out[(q + cur['name']).replace(' ', '')] = cur['size']

    for line in r.stdout.split('\n'):
        m = _DIE.match(line)
        if m:
            finish()
            depth = int(m.group(1))
            while stack and stack[-1][0] >= depth:
                stack.pop()
            cur = {'tag': m.group(2), 'depth': depth}
            stack.append((depth, None, m.group(2) in ('DW_TAG_namespace', 'DW_TAG_class_type',
                                                      'DW_TAG_structure_type', 'DW_TAG_union_type')))
            continue
        a = _ATTR.match(line)
        if a and cur is not None:
            k, v = a.group(1), a.group(2)
            if k == 'DW_AT_name':
                nm = v.rsplit(': ', 1)[-1].strip() if '):' in v else v.strip()
                cur['name'] = nm
                stack[-1] = (stack[-1][0], nm, stack[-1][2])
            elif k == 'DW_AT_byte_size':
                try:
                    cur['size'] = int(v.split()[0], 0)
                except ValueError:
                    pass
            elif k == 'DW_AT_declaration':
                cur['decl'] = True
    finish()
    return out


def load_layouts(root):
    """{type name: {object file name of the unit: size}}. Names are local to a unit (a bare `Result` is a different
    struct in every unit that declares one), so a size counts only for a class that has code in that unit."""
    out = {}
    path = os.path.join(root, 'out', 'layouts.tsv')
    try:
        for line in open(path):
            if line.startswith('#'):
                continue
            p = line.rstrip('\n').split('\t')
            if len(p) > 2:
                try:
                    sz = int(p[2])
                except ValueError:
                    continue
                if sz > 0:
                    out.setdefault(p[1].replace(' ', ''), {}).setdefault(p[0].replace('/', '_') + '.o', sz)
    except OSError:
        pass
    return out


def _unit_of(obj, units):
    return units.get(obj)


def tree_units(tree):
    """{object file name: unit path relative to the tree} for the .cpp files of tree/src."""
    out = {}
    for dp, _dn, fn in os.walk(os.path.join(tree, 'src')):
        for f in fn:
            if f.endswith('.cpp'):
                rel = os.path.relpath(os.path.join(dp, f), tree)
                out[rel.replace(os.sep, '_') + '.o'] = rel
    return out


def probe_classes(tree, workdir, wanted, jobs, shard=None, on_result=None, solo=False):
    """wanted: {unit: [class, ...]} -> {class: size or -1}, by layout.probe_unit of the unit (the classes of this
    decomp are mostly defined in the .cpp that uses them, so the whole unit is compiled). on_result(class, size)
    is called as each unit finishes."""
    import layout
    os.makedirs(workdir, exist_ok=True)
    work = list(wanted)   # the caller's order: the units that matter most first
    if shard:
        i, n = shard
        work = [u for u in work if zlib.crc32(u.encode()) % n == i]
    res = {}

    def one(unit):
        r = {}
        try:
            src = unit
            if solo:   # one compile per class: a failing neighbour in a batch can take a class down with it
                for c in wanted[unit]:
                    r.update(layout.probe_unit(tree, workdir, src, [c]))
            else:
                r = layout.probe_unit(tree, workdir, src, wanted[unit])
        except Exception:   # a unit that does not compile: its classes stay unknown
            pass
        return unit, r

    with concurrent.futures.ThreadPoolExecutor(max(1, jobs)) as ex:
        for unit, r in ex.map(one, work):
            for c in wanted[unit]:
                lay = r.get(c)
                res[c] = lay.size if lay and lay.size else -1
                if on_result:
                    on_result(c, res[c])
    return res


def load_probe_cache(outdir):
    """{class: size} of the probes so far (-1: failed); file lines are class, size, mode (full / solo)."""
    cache = {}
    if os.path.isdir(outdir):
        for f in sorted(os.listdir(outdir)):
            if f.startswith('seams_layout_probe') and f.endswith('.tsv'):
                for line in open(os.path.join(outdir, f)):
                    p = line.rstrip('\n').split('\t')
                    if len(p) >= 2:
                        mode = p[2] if len(p) > 2 else 'full'
                        size = int(p[1])
                        if size > 0 or cache.get(p[0], (None, ''))[0] is None:
                            cache[p[0]] = (size, mode)
    return cache


# ------------------------------------------------------------------------------------------------ the check

def boot_functions(topdown):
    out = set()
    if topdown and os.path.isfile(topdown):
        for line in open(topdown):
            if line.startswith('#'):
                continue
            p = line.rstrip('\n').split('\t')
            for col in (p[-1:] if p else []):
                for tok in re.findall(r'__fnv_([0-9a-f]{8})', col):
                    out.add(int(tok, 16))
            for tok in re.findall(r'__fnv_([0-9a-f]{8})', p[3] if len(p) > 3 else ''):
                out.add(int(tok, 16))
    return out


def evidence(scan, link, image):
    """-> {(class, kind): dict(sizes Counter, sites, example, ...)}."""
    ev = {}
    for site, fva, size, ctor in scan.new_sites:
        e = ev.setdefault((link.ctors[ctor], 'new'), {'sizes': collections.Counter(), 'sites': [], 'funcs': set()})
        e['sizes'][size] += 1
        e['sites'].append(site)
        e['funcs'].add(fva)
    # static: extent to the next known data symbol
    syms = set(scan.refs)
    syms_sorted = None
    glob = {}
    for site, fva, addr, ctor in scan.static_sites:
        glob.setdefault((link.ctors[ctor], addr), []).append((site, fva))
    known = set(a for _, a in glob)
    if link.root:
        path = os.path.join(link.root, 'out', 'refs.tsv')
        try:
            for line in open(path):
                p = line.rstrip('\n').split('\t')
                if len(p) > 4 and p[2] == 'data' and re.match(r'^[0-9a-f]{8}$', p[4]):
                    known.add(int(p[4], 16))
        except OSError:
            pass
    syms_sorted = sorted(known)
    refs_sorted = sorted(syms)
    per = {}
    for (cls, addr), sites in glob.items():
        i = bisect.bisect_right(syms_sorted, addr)
        nxt = syms_sorted[i] if i < len(syms_sorted) else None
        # end of the section holding addr
        end = None
        for lo, hi in image.writable():
            if lo <= addr < hi:
                end = hi
        if nxt is None or (end and nxt > end):
            nxt = end
        if nxt is None or nxt - addr > 0x10000:
            continue
        gap = nxt - addr
        # references into the object that are not another global's start: members of this one
        j = bisect.bisect_left(refs_sorted, addr)
        lb = 0
        while j < len(refs_sorted) and refs_sorted[j] < nxt:
            if refs_sorted[j] not in known:
                lb = refs_sorted[j] - addr + 1
            j += 1
        e = per.setdefault(cls, {'sizes': collections.Counter(), 'sites': [], 'funcs': set(), 'gap': None,
                                 'lb': 0})
        e['sizes'][gap] += 1
        e['sites'].extend(s for s, _ in sites)
        e['funcs'].update(f for _, f in sites)
        if e['gap'] is None or gap < e['gap']:   # the tightest global bounds the size best
            e['gap'], e['lb'] = gap, lb
    for cls, e in per.items():
        ev[(cls, 'static')] = e
    for cls, (end, cnt, fn, off) in scan.maxoff.items():
        ev[(cls, 'maxoff')] = {'sizes': collections.Counter({end: cnt}), 'sites': [int(fn, 16)], 'funcs': set(),
                               'note': 'offset 0x%x in %s' % (off, fn)}
    return ev


def check_layout(link_root, exe_path, tree=None, topdown=None, jobs=None, shard=None, probe=True,
                 allocators=DEFAULT_ALLOCATORS, progress=None):
    """-> rows (lists of strings, COLUMNS), mismatches first. See the module docstring."""
    log = progress or (lambda *a: None)
    link = Link(link_root)
    image = Image(open(exe_path, 'rb').read())
    log('addrmap: %d rows, %d classes, %d constructors' % (len(link.rows), len(link.classes), len(link.ctors)))
    sc = scan_exe(image, link, crt_starts(), allocators)
    log('exe: %d new sites, %d static sites, %d classes with member accesses; %d constructor calls skipped as the '
        'base part of an inlined derived constructor' % (len(sc.new_sites), len(sc.static_sites), len(sc.maxoff),
                                                          sc.derived))
    ev = evidence(sc, link, image)
    classes = sorted({c for c, _ in ev})
    # native sizes
    native = {}
    src = {}
    fixed = os.path.join(link_root, 'fixed')
    class_objs = link.class_objs
    by_obj = collections.defaultdict(list)
    for c in classes:
        for o in class_objs.get(c, [])[:3]:
            by_obj[o].append(c)
    for obj, cs in by_obj.items():
        p = os.path.join(fixed, obj)
        if os.path.isfile(p):
            d = dwarf_sizes(p)
            for c in cs:
                if c in d and c not in native:
                    native[c], src[c] = d[c], 'dwarf'
    lay = load_layouts(link_root)
    for c in classes:
        if c not in native and c in lay:
            sizes = {lay[c][o] for o in class_objs.get(c, []) if o in lay[c]}
            if len(sizes) == 1:
                native[c], src[c] = sizes.pop(), 'layouts.tsv'
    outdir = os.path.join(link_root, 'out')
    cache = load_probe_cache(outdir)
    for c in classes:
        if c not in native and cache.get(c, (-1,))[0] > 0:
            native[c], src[c] = cache[c][0], 'probe'
    if probe and tree:
        units = tree_units(tree)
        unit_for = {}
        for c in classes:
            for o in class_objs.get(c, []):
                u = units.get(o)
                if u:
                    unit_for[c] = u
                    break
        prio = {c for (c, k) in ev if k in ('new', 'static')}
        bf = boot_functions(topdown)
        boot_classes = {c for (c, k), e in ev.items() if e['funcs'] & bf}
        name = 'seams_layout_probe.tsv' if not shard else 'seams_layout_probe.%d_of_%d.tsv' % shard
        os.makedirs(outdir, exist_ok=True)
        lock = __import__('threading').Lock()
        out = open(os.path.join(outdir, name), 'a')

        def keep(mode):
            def f(c, s):
                with lock:
                    out.write('%s\t%d\t%s\n' % (c, s, mode))
                    out.flush()
                if s > 0:
                    native[c], src[c] = s, 'probe'
            return f

        # stage 1: every class, one compile per unit (all its classes in one probe source); stage 2: the classes of the
        # boot path that stage 1 lost, one compile each (a failing neighbour in a batch can take a class down with it)
        for mode, pick in (('full', lambda c: c not in cache),
                           ('solo', lambda c: c in boot_classes and cache.get(c, (0, ''))[1] != 'solo'
                            and (c not in cache or cache[c][0] <= 0))):
            wanted = collections.defaultdict(list)
            for c in sorted(classes, key=lambda c: (c not in prio, c not in boot_classes, c)):
                if c not in native and c in unit_for and pick(c):
                    wanted[unit_for[c]].append(c)
            log('probe %s: %d classes in %d units' % (mode, sum(map(len, wanted.values())), len(wanted)))
            if wanted:
                probe_classes(tree, os.path.join(outdir, 'seams_layout_work'), wanted, jobs or os.cpu_count(),
                              shard, on_result=keep(mode), solo=(mode == 'solo'))
        out.close()
    boots = boot_functions(topdown)
    rows = []
    for (cls, kind), e in ev.items():
        sizes = e['sizes']
        nsites = sum(sizes.values()) if kind != 'maxoff' else len(e['sites'])
        if kind == 'maxoff':
            exe = max(sizes)
            note = e.get('note', '') + '; %d accesses' % sizes[exe]
            nsites = sizes[exe]
        else:
            top = max(sizes.items(), key=lambda kv: (kv[1], kv[0]))
            exe = top[0] if kind == 'new' else e['gap']
            note = ''
            if len(sizes) > 1:
                note = 'sizes ' + ','.join('0x%x x%d' % kv for kv in sorted(sizes.items()))
            if kind == 'static':
                note = ('; '.join(x for x in [note, 'extent to next symbol; refs reach +0x%x' % e['lb']] if x))
        ns = native.get(cls)
        boot = int(bool(e['funcs'] & boots)) if kind != 'maxoff' else 0
        if SYNTH.search(cls):
            note = '; '.join(x for x in [note, 'synthetic class name'] if x)
        if ns is None:
            verdict = 'unknown'
        elif kind == 'new':
            verdict = 'mismatch' if ns < max(sizes) else ('larger' if ns > exe else 'ok')
        elif kind == 'maxoff':
            verdict = 'mismatch' if ns < exe else 'ok'
        else:   # static
            lb = e['lb']
            if ns < lb:
                verdict = 'mismatch'
            elif ns < exe:
                verdict = 'mismatch' if lb >= exe - 3 else 'suspect'
            elif ns > exe:
                verdict = 'larger'
            else:
                verdict = 'ok'
        example = '%08x' % (e['sites'][0] if e['sites'] else 0)
        rows.append([cls, str(ns) if ns is not None else '', src.get(cls, ''), str(exe), kind, str(nsites), example,
                     verdict, str(boot), note])
    order = {'mismatch': 0, 'suspect': 1, 'larger': 2, 'unknown': 3, 'ok': 4}
    kord = {'new': 0, 'static': 1, 'maxoff': 2}
    rows.sort(key=lambda r: (order[r[7]], -int(r[8]), kord[r[4]], -int(r[5]), r[0]))
    return rows


def write_rows(rows, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w') as f:
        f.write('#' + '\t'.join(COLUMNS) + '\n')
        for r in rows:
            f.write('\t'.join(r) + '\n')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('link')
    ap.add_argument('exe')
    ap.add_argument('-o', '--out')
    ap.add_argument('--tree', help='prepared source tree: lets classes without another size source be probed')
    ap.add_argument('--topdown', help="R2's topdown.tsv: its via column ranks the boot path")
    ap.add_argument('--jobs', type=int)
    ap.add_argument('--shard', help='i/n: probe only this share of the units (outputs concatenate in out/)')
    ap.add_argument('--no-probe', action='store_true')
    ap.add_argument('--alloc', action='append', default=[], help='more allocator addresses (hex)')
    a = ap.parse_args(argv)
    shard = tuple(int(x) for x in a.shard.split('/')) if a.shard else None
    out = a.out or os.path.join(a.link, 'out', 'seams_layout.tsv')
    allocs = DEFAULT_ALLOCATORS + tuple(int(x, 16) for x in a.alloc)
    rows = check_layout(a.link, a.exe, tree=a.tree, topdown=a.topdown, jobs=a.jobs, shard=shard,
                        probe=not a.no_probe, allocators=allocs,
                        progress=lambda *m: print(*m, file=sys.stderr, flush=True))
    write_rows(rows, out)
    c = collections.Counter((r[4], r[7]) for r in rows)
    print('wrote %s: %d rows; %s' % (out, len(rows), ', '.join('%s/%s %d' % (k[0], k[1], v) for k, v in sorted(c.items()))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
