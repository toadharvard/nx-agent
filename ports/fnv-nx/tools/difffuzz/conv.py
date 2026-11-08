"""conv.py - one function's logical arguments and result, and where each machine keeps them.

The MSVC decorated name (units.json) is the single source of truth for both sides: S2's models (tools/link
sigs.py / abi.py / abi2.py) give the logical values (this, parameters, result) with their classes and sizes, the
MSVC x86 locations (ecx, edx, stack offsets, hidden result pointer) and the AAPCS64 ILP32 chunks (x/v registers,
stack, memory behind a pointer, x8). The same logical bytes are written into both machines.
"""
import os
import sys

import config

sys.path.insert(0, str(config.LINKTOOLS))
import abi    # noqa: E402
import abi2   # noqa: E402
import layout  # noqa: E402
import sigs   # noqa: E402
from fnvlink import DemangleCache, UNDNAME  # noqa: E402

SIGNED = ('char', 'signed char', 'short', 'int', 'long', '__int64', 'long long', 'wchar_t')


class Unsupported(Exception):
    pass


_dem = None
_layouts = None


def demangler():
    global _dem
    if _dem is None:
        p = config.WORK / 'cache'
        p.mkdir(parents=True, exist_ok=True)
        src = config.LINK / 'out/cache/msvc.tsv'
        dst = p / 'msvc.tsv'
        if not dst.exists() and src.exists():
            dst.write_bytes(src.read_bytes())
        _dem = DemangleCache(str(dst), UNDNAME)
    return _dem


def layouts():
    global _layouts
    if _layouts is None:
        path = os.environ.get('FNVNX_LAYOUTS', str(config.LINK / 'out/layouts.tsv'))
        _layouts = {k: v for k, v in layout.load(path).items() if v is not None}
    return _layouts


def parse(name):
    d = demangler()
    if name.startswith('?'):
        d.need([name])
    return sigs.parse_msvc(name, d[name])


class Value:
    __slots__ = ('label', 'cls', 'size', 'kind', 'raw', 'signed', 'is_ptr', 'is_float', 'is_bool', 'is_ref')

    def __init__(self, v, raw):
        self.label, self.cls, self.size, self.kind = v.label, v.cls, v.size, v.kind
        self.raw = raw or ''
        t = ' '.join(self.raw.replace('const', '').replace('volatile', '').split())
        self.signed = t in SIGNED or t.startswith('enum')
        self.is_ptr = v.cls == 'P'
        self.is_float = v.cls in ('F32', 'F64')
        self.is_bool = t == 'bool'      # only 0/1 are valid bool representations (both ABIs assume it)
        self.is_ref = t.endswith('&')   # a reference: never null

    def __repr__(self):
        return '%s:%s/%d' % (self.label, self.cls, self.size)


class Conv:
    """vals: [Value] in passing order (this first); ret: Value or None.
    x86: [(label, space, offset, size)] incl. 'sret'; x86_ret: 'none' | 'eax' | 'edx:eax' | 'st0' | 'sret'.
    a64: {label: chunks}; a64_ret: chunks (abi2.a64_ret); pop: bytes the x86 callee pops."""

    def __init__(self, name, unit_source):
        self.name = name
        self.sig = sig = parse(name)
        if sig.kind != 'func' and not (sig.kind == 'cname' and sig.cc):
            raise Unsupported('not a function signature: %s' % sig.kind)
        if sig.kind == 'cname':
            raise Unsupported('undecorated C name (no parameter types)')
        lay = layouts()
        try:
            vals, ret = abi2.values(sig, lambda t: lay.get((unit_source, t)))
        except abi2.Unknown as e:
            raise Unsupported('value class unknown: %s' % e)
        raws = (['this'] if sig.member else []) + list(sig.raw_params or [])
        self.vals = [Value(v, r) for v, r in zip(vals, raws)]
        self.ret = Value(ret, sig.raw_ret) if ret is not None else None
        self._ret_val = ret
        self.hidden = abi2.hidden_result(sig, ret)
        self.x86 = abi2.x86(sig, vals, ret)
        cc = sig.cc or 'cdecl'
        stack = [sz for lab, sp, off, sz in self.x86 if sp == 'stk']
        self.pop = sum((s + 3) // 4 * 4 for s in stack) if cc in ('stdcall', 'thiscall', 'fastcall') else 0
        if ret is None:
            self.x86_ret = 'none'
        elif self.hidden:
            self.x86_ret = 'sret'
        elif ret.cls in ('F32', 'F64'):
            self.x86_ret = 'st0'
        elif ret.size > 4:
            self.x86_ret = 'edx:eax'
        else:
            self.x86_ret = 'eax'
        self._avals = vals
        try:
            self.a64 = abi2.a64(vals)
            self.a64_ret = abi2.a64_ret(ret)
        except abi2.Unknown as e:
            raise Unsupported('a64 class unknown: %s' % e)
        for lab, ch in self.a64.items():
            for _, cont, _, _ in ch:
                if cont[0] == 'mem' and cont[1][0] != 'x':
                    raise Unsupported('by-reference aggregate passed on the stack')

    def bridged_view(self, drop):
        """(difffuzz-20, r5-sretbridge) The convention the native side delivers to the stub behind an `sret-explicit`
        bridge (tools/link abi2: the reference passes the result buffer as an explicit pointer parameter `drop`, the
        definition takes it as the hidden result): the bridge consumes that parameter, so every later one sits one
        slot earlier. Returns a copy whose `vals` and `a64` omit `drop` (decode_a64 then finds the others where the
        bridge put them); None when `drop` is not a parameter."""
        import copy
        keep = [i for i, v in enumerate(self.vals) if v.label != drop]
        if len(keep) == len(self.vals):
            return None
        out = copy.copy(self)
        out.vals = [self.vals[i] for i in keep]
        out.a64 = abi2.a64([self._avals[i] for i in keep])
        return out

    def describe(self):
        d = '%s -> %s' % (', '.join(map(repr, self.vals)), repr(self.ret) if self.ret else 'void')
        return d + (' [x86 regs %s]' % ','.join(self.custom) if getattr(self, 'custom', None) else '')

    def register_eligible(self):
        """Labels of the declared values a custom register can carry (integers/pointers of at most 4 bytes on the
        stack in the declared convention), in declared order."""
        out = []
        for lab, space, o, size in getattr(self, 'x86_declared', self.x86):
            v = next((x for x in self.vals if x.label == lab), None)
            if space not in ('ecx', 'edx') and v is not None and v.kind == 'gpr' and v.size <= 4:
                out.append(lab)
        return out

    def with_registers(self, regs, assign=None):
        """MSVC whole-program custom convention: the oracle also takes arguments in `regs` (registers it reads on
        entry that the declared convention does not pass). Model: register-eligible values (integers/pointers of at
        most 4 bytes, `this` first) take those registers in declared order, everything else keeps the stack in
        declared order. AArch64 placement is unchanged (GCC compiled the declared signature). `assign`
        ({label: register}) replaces the declared-order pairing (engine.calibrate_custom, difffuzz-20)."""
        regs = list(regs)
        if not hasattr(self, 'x86_declared'):
            self.x86_declared = list(self.x86)
        self.custom_regs = list(regs)
        out, off, used = [], 0, []
        for lab, space, o, size in self.x86_declared:
            v = next((x for x in self.vals if x.label == lab), None)
            if space in ('ecx', 'edx'):
                out.append((lab, space, 0, size))
                continue
            if assign is not None:
                if lab in assign:
                    used.append(assign[lab])
                    out.append((lab, assign[lab], 0, size))
                    continue
            elif regs and v is not None and v.kind == 'gpr' and v.size <= 4:
                used.append(regs.pop(0))
                out.append((lab, used[-1], 0, size))
                continue
            out.append((lab, 'stk', off, size))
            off += (size + 3) // 4 * 4
        self.x86 = out
        self.custom = used
        cc = self.sig.cc or 'cdecl'
        stack = [sz for _, sp, _, sz in out if sp == 'stk']
        self.pop = sum((x + 3) // 4 * 4 for x in stack) if cc in ('stdcall', 'thiscall', 'fastcall') else 0
        return self


def extend(val, data, mode):
    """Bytes of a 4/8-byte container slot for value bytes `data` (size val.size): sign/zero extension, or
    garbage pad bytes from `mode` (bytes) when given."""
    n = (val.size + 3) // 4 * 4 if val.size < 8 else val.size
    if len(data) >= n:
        return data[:n]
    if isinstance(mode, (bytes, bytearray)):
        return data + bytes(mode[:n - len(data)]).ljust(n - len(data), b'\0')
    fill = b'\xff' if val.signed and data and data[-1] & 0x80 else b'\0'
    return data + fill * (n - len(data))
