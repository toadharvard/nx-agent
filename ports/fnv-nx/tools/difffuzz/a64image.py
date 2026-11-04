"""a64image.py - one unit's rewritten AArch64 ILP32 object linked into a small image for the emulator.

Inputs: the object as tools/link `apply` rewrote it (undefined symbols renamed to `__fnv_<addr>` / bridges, address
symbols added for its definitions, image variables rebound to their original addresses), the bridges object, and the
absolute data symbols of `aliases.ld`. Everything still undefined gets a stub address:

  * `__fnv_<X>` for code X (no variant suffix): CODE_STUB + X, so a branch there names its original target;
  * anything else (ctor/dtor variants, runtime names, imports, C++ names left unresolved): a slot in the named-stub
    region, `stubs[addr] = name`.

Stub pages hold `brk #0`; the emulator turns a fetch there into an intercepted call. The image itself is linked at
A64_BASE (outside the image window 0x00400000..0x02100000 and the shared world regions).
"""
import json
import os
import re
import struct
import contextlib
import fcntl
import subprocess
import sys

import config

sys.path.insert(0, str(config.LINKTOOLS))
import elfsyms  # noqa: E402

LD = os.environ.get('LD', 'aarch64-linux-gnu-ld')
A64_BASE = 0x0A000000
CODE_STUB = 0x08000000        # + original code address (0x00400000..0x00fdf000)
NAMED_STUB = 0x09000000       # + 16 * k
NAMED_STUB_MAX = 0x00800000 // 16
STACK_GUARD = 0x0b800000      # page holding the stack protector canary (mapped by the AArch64 engine)
LINK_VERSION = 5              # bump when the stub/alias rules change (invalidates cached unit images)
ADDR_SYM = re.compile(r'^__fnv_([0-9a-f]{8})(?:_(C[0-9]|D[0-9]))?$')
LAYOUT = """
SECTIONS {
  . = 0x%08x;
  .text : { *(.text.unlikely .text.unlikely.*) *(.text.startup .text.startup.*) *(.text .text.* .gnu.linkonce.t.*) }
  . = ALIGN(0x1000);
  .rodata : { *(.rodata .rodata.* .gnu.linkonce.r.*) }
  . = ALIGN(0x10);
  .data.rel.ro : { *(.data.rel.ro.local .data.rel.ro.local.*) *(.data.rel.ro .data.rel.ro.*) }
  .data : { *(.data .data.* .gnu.linkonce.d.*) }
  .init_array : { *(.init_array .init_array.*) }
  .got : { *(.got) *(.got.plt) *(.igot.plt) }
  .tdata : { *(.tdata .tdata.*) }
  .tbss : { *(.tbss .tbss.*) }
  .bss : { *(.bss .bss.* COMMON) }
  /DISCARD/ : { *(.eh_frame) *(.eh_frame_hdr) *(.gcc_except_table .gcc_except_table.*) *(.note*) *(.comment) }
}
"""


def load_aliases(path):
    """aliases.ld -> (absolute {name: value}, aliases {name: target name})."""
    absolute, alias = {}, {}
    rx = re.compile(r'^\s*([\w.$]+)\s*=\s*([\w.$]+)\s*;')
    if os.path.exists(path):
        for line in open(path, encoding='utf-8', errors='surrogateescape'):
            m = rx.match(line)
            if not m:
                continue
            if m.group(2).startswith('0x'):
                absolute[m.group(1)] = int(m.group(2), 16)
            else:
                alias[m.group(1)] = m.group(2)
    return absolute, alias


class Image:
    """segments [(addr, bytes)], syms {name: addr}, stubs {addr: name}, entries {native addr: orig addr} (the
    `__fnv_` address symbols defined by the unit itself)."""

    def __init__(self, elf, stubs, info):
        self.elf, self.stubs = elf, stubs
        self.info = info
        self.segments, self.syms = [], {}
        self._read()
        self.entries = {}
        for n, a in self.syms.items():
            m = ADDR_SYM.match(n)
            if m and not m.group(2) and a >= A64_BASE:
                self.entries.setdefault(a, int(m.group(1), 16))
        self.end = max(a + len(b) for a, b in self.segments) if self.segments else A64_BASE
        # everything after the code (.rodata onwards) is restored between runs; .text never changes
        text = [a + len(b) for a, b in self.segments if a == A64_BASE]
        self.data_start = text[0] if text else A64_BASE
        # writable data (.data.rel.ro onwards): the first segment that is not .text or .rodata
        # the unit's own mutable state: .data and .bss only (not the GOT, not vtables/typeinfo in .data.rel.ro)
        self.private = [(a, a + n) for nm, a, n in self.sections if nm in ('.data', '.bss')]

    def _read(self):
        d = open(self.elf, 'rb').read()
        shoff, = struct.unpack_from('<I', d, 0x20)
        shentsize, shnum, shstrndx = struct.unpack_from('<HHH', d, 0x2e)
        secs = [struct.unpack_from('<IIIIIIIIII', d, shoff + i * shentsize) for i in range(shnum)]
        shstr = secs[shstrndx][4]
        self.sections = []
        for name, typ, flags, addr, off, size, link, info, align, ent in secs:
            if flags & 2 and size and addr >= A64_BASE:      # SHF_ALLOC
                data = bytes(size) if typ == 8 else d[off:off + size]   # SHT_NOBITS
                self.segments.append((addr, data))
                sname = d[shstr + name:d.index(b'\0', shstr + name)].decode('latin-1')
                self.sections.append((sname, addr, size))
        for s in elfsyms.symbols(self.elf):
            if s.name and s.shndx != 0 and s.type not in ('section', 'file'):
                self.syms.setdefault(s.name, s.value)


@contextlib.contextmanager
def _locked(path):
    """Exclusive lock on <path>.lock: the fuzzer's worker processes build a unit image once, never concurrently
    (a reader saw a half-written ELF: struct.error / IndexError at setup)."""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path + '.lock', 'w') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def helpers_object(workdir):
    """a64helpers.cpp compiled once (ILP32, -O2): the platform iterators the native objects call by name."""
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'a64helpers.cpp')
    obj = os.path.join(workdir, 'zz_s13_helpers.o')
    with _locked(obj):
        if not os.path.exists(obj) or os.path.getmtime(obj) < os.path.getmtime(src):
            os.makedirs(workdir, exist_ok=True)
            subprocess.run([os.environ.get('CXX', 'aarch64-linux-gnu-g++'), '-mabi=ilp32', '-O2', '-fno-exceptions',
                            '-fno-stack-protector', '-c', src, '-o', obj + '.tmp'], check=True)
            os.replace(obj + '.tmp', obj)
    return obj


def build(source, linkdir, workdir):
    """Link the rewritten object of `source` -> Image (cached in workdir/<obj>.elf)."""
    return build_many([source], linkdir, workdir, config.obj_name(source))


def build_many(sources, linkdir, workdir, name):
    """Link the rewritten objects of several units into one image (lockstep runs of a call tree)."""
    os.makedirs(workdir, exist_ok=True)
    with _locked(os.path.join(workdir, name)):
        return _build_many(sources, linkdir, workdir, name)


def _build_many(sources, linkdir, workdir, name):
    fixed = os.path.join(linkdir, 'fixed')
    units = []
    for source in sources:
        obj = os.path.join(fixed, config.obj_name(source))
        if not os.path.exists(obj):
            raise FileNotFoundError(obj)
        units.append(obj)
    bridges = os.path.join(fixed, 'zz_bridges.o')
    os.makedirs(workdir, exist_ok=True)
    base = os.path.join(workdir, name)
    elf, meta = base + '.elf', base + '.stubs.json'
    srcs = units + [bridges, os.path.join(linkdir, 'out', 'aliases.ld')]
    if os.path.exists(elf) and os.path.exists(meta) and all(os.path.getmtime(elf) >= os.path.getmtime(s)
                                                            for s in srcs if os.path.exists(s)):
        m = json.load(open(meta))
        if m.get('version') == LINK_VERSION and m.get('sources') == sorted(sources):
            return Image(elf, {int(k): v for k, v in m['stubs'].items()}, m['info'])
    objs = units + ([bridges] if os.path.exists(bridges) else []) + [helpers_object(workdir)]
    defined, undef, weak = set(), set(), set()
    for o in objs:
        for s in elfsyms.symbols(o):
            if not s.name or s.type in ('section', 'file'):
                continue
            if s.shndx == 0:
                undef.add(s.name)
            elif s.bind != 'local':
                defined.add(s.name)
                if s.bind == 'weak':
                    weak.add(s.name)
    absolute, alias = load_aliases(os.path.join(linkdir, 'out', 'aliases.ld'))
    lines = []
    # natively defined image variables are weak address symbols; the game link's aliases.ld puts them at their
    # original address (the image copy), so do the same here
    for n in sorted(weak):
        if n in absolute:
            lines.append('%s = 0x%08x;' % (n, absolute[n]))
    for n in sorted(undef - defined):
        if n in absolute:
            lines.append('%s = 0x%08x;' % (n, absolute[n]))
            defined.add(n)
        elif n in alias and alias[n] in defined:
            lines.append('%s = %s;' % (n, alias[n]))
            defined.add(n)
    stubs, k = {}, 0
    info = dict(code_stubs=0, named_stubs=0, data_abs=0)
    for n in sorted(undef - defined):
        if n == '__stack_chk_guard':          # -fstack-protector (distribution GCC default): a real canary word
            lines.append('%s = 0x%08x;' % (n, STACK_GUARD))
            continue
        m = ADDR_SYM.match(n)
        if m and not m.group(2):
            a = int(m.group(1), 16)
            if a < 0x00fdf000:
                lines.append('%s = 0x%08x;' % (n, CODE_STUB + a)); info['code_stubs'] += 1
                continue
            lines.append('%s = 0x%08x;' % (n, a)); info['data_abs'] += 1
            continue
        if k >= NAMED_STUB_MAX:
            raise RuntimeError('too many named stubs')
        addr = NAMED_STUB + 16 * k
        k += 1
        stubs[addr] = n
        lines.append('%s = 0x%08x;' % (n, addr)); info['named_stubs'] += 1
    with open(base + '.stubs.ld', 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('\n'.join(lines) + '\n')
    with open(base + '.layout.ld', 'w') as f:
        f.write(LAYOUT % A64_BASE)
    if os.path.exists(elf + '.tmp'):
        os.remove(elf + '.tmp')
    r = subprocess.run([LD, '-m', 'aarch64linux32', '--no-demangle', '-static', '--noinhibit-exec',
                        '--defsym=__dso_handle=0', '-e', '0', '-T', base + '.layout.ld', '-o', elf + '.tmp',
                        base + '.stubs.ld', *objs],
                       capture_output=True, text=True, errors='surrogateescape')
    open(base + '.ld.log', 'w').write(r.stderr)
    if not os.path.exists(elf + '.tmp'):
        raise RuntimeError('link failed: %s' % r.stderr[:2000])
    os.replace(elf + '.tmp', elf)
    with open(meta + '.tmp', 'w') as f:
        json.dump(dict(stubs={str(a): n for a, n in stubs.items()}, info=info, version=LINK_VERSION,
                       sources=sorted(sources)), f)
    os.replace(meta + '.tmp', meta)
    return Image(elf, stubs, info)
