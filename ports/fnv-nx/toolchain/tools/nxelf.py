"""Minimal ELF reader for AArch64 images, ELF32 (ILP32) and ELF64 (LP64) alike.

Only what the Switch packers need: header, program headers, section headers, notes, dynamic entries and
relocations. Little-endian only (Horizon is little-endian).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

EM_AARCH64 = 183
ET_EXEC, ET_DYN = 2, 3
PT_LOAD, PT_DYNAMIC, PT_TLS = 1, 2, 7
SHT_NOTE = 7
NT_GNU_BUILD_ID = 3

DT_NULL, DT_RELA, DT_RELASZ, DT_RELAENT, DT_REL, DT_RELSZ = 0, 7, 8, 9, 17, 18
DT_RELRSZ, DT_RELR, DT_RELRENT = 35, 36, 37

R_AARCH64_NONE = 0
R_AARCH64_P32_RELATIVE = 183   # ILP32: *(u32 *)(B + off) = B + addend
R_AARCH64_RELATIVE = 1027      # LP64:  *(u64 *)(B + off) = B + addend


@dataclass
class Phdr:
    type: int
    offset: int
    vaddr: int
    filesz: int
    memsz: int
    flags: int
    align: int


@dataclass
class Shdr:
    name: str
    type: int
    flags: int
    addr: int
    offset: int
    size: int
    entsize: int


class ElfError(ValueError):
    pass


class Elf:
    def __init__(self, data: bytes):
        if data[:4] != b"\x7fELF":
            raise ElfError("not an ELF file")
        ei_class, ei_data = data[4], data[5]
        if ei_data != 1:
            raise ElfError("big-endian ELF is not supported")
        if ei_class not in (1, 2):
            raise ElfError("bad ELF class")
        self.data = data
        self.is64 = ei_class == 2
        self.ptr = 8 if self.is64 else 4
        if self.is64:
            (self.type, self.machine, _, self.entry, phoff, shoff, _, _, phentsize, phnum, shentsize, shnum,
             shstrndx) = struct.unpack_from("<HHIQQQIHHHHHH", data, 16)
        else:
            (self.type, self.machine, _, self.entry, phoff, shoff, _, _, phentsize, phnum, shentsize, shnum,
             shstrndx) = struct.unpack_from("<HHIIIIIHHHHHH", data, 16)
        if self.machine != EM_AARCH64:
            raise ElfError(f"expected AArch64 (e_machine {EM_AARCH64}), got {self.machine}")

        self.phdrs: list[Phdr] = []
        for i in range(phnum):
            o = phoff + i * phentsize
            if self.is64:
                t, fl, off, va, _pa, fsz, msz, al = struct.unpack_from("<IIQQQQQQ", data, o)
            else:
                t, off, va, _pa, fsz, msz, fl, al = struct.unpack_from("<IIIIIIII", data, o)
            self.phdrs.append(Phdr(t, off, va, fsz, msz, fl, al))

        raw = []
        for i in range(shnum):
            o = shoff + i * shentsize
            if self.is64:
                nm, t, fl, ad, off, sz, _lk, _inf, _al, es = struct.unpack_from("<IIQQQQIIQQ", data, o)
            else:
                nm, t, fl, ad, off, sz, _lk, _inf, _al, es = struct.unpack_from("<IIIIIIIIII", data, o)
            raw.append((nm, t, fl, ad, off, sz, es))
        strtab = b""
        if raw and shstrndx < len(raw):
            s = raw[shstrndx]
            strtab = data[s[4]:s[4] + s[5]]
        self.shdrs = []
        for nm, t, fl, ad, off, sz, es in raw:
            end = strtab.find(b"\0", nm)
            self.shdrs.append(Shdr(strtab[nm:end].decode(errors="replace"), t, fl, ad, off, sz, es))

    # -- queries ------------------------------------------------------------------------------------------
    def loads(self) -> list[Phdr]:
        return [p for p in self.phdrs if p.type == PT_LOAD]

    def segment_bytes(self, p: Phdr) -> bytes:
        if p.offset + p.filesz > len(self.data):
            raise ElfError("segment outside file")
        return self.data[p.offset:p.offset + p.filesz]

    def section(self, name: str) -> Shdr | None:
        for s in self.shdrs:
            if s.name == name:
                return s
        return None

    def build_id(self) -> bytes:
        for s in self.shdrs:
            if s.type != SHT_NOTE:
                continue
            o, end = s.offset, s.offset + s.size
            while o + 12 <= end:
                namesz, descsz, ntype = struct.unpack_from("<III", self.data, o)
                name = self.data[o + 12:o + 12 + namesz]
                desc_off = o + 12 + ((namesz + 3) & ~3)
                if ntype == NT_GNU_BUILD_ID and name == b"GNU\0":
                    return self.data[desc_off:desc_off + descsz]
                o = desc_off + ((descsz + 3) & ~3)
        return b""

    def vaddr_to_offset(self, va: int) -> int:
        for p in self.loads():
            if p.vaddr <= va < p.vaddr + p.filesz:
                return p.offset + (va - p.vaddr)
        raise ElfError(f"vaddr {va:#x} not in a file-backed segment")

    def dynamic(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for p in self.phdrs:
            if p.type != PT_DYNAMIC:
                continue
            fmt, step = ("<qQ", 16) if self.is64 else ("<iI", 8)
            for o in range(p.offset, p.offset + p.filesz, step):
                tag, val = struct.unpack_from(fmt, self.data, o)
                if tag == DT_NULL:
                    break
                out[tag] = val
        return out

    def relocations(self) -> list[tuple[int, int, int, int]]:
        """(offset, type, sym, addend) for every RELA entry, plus RELR entries as RELATIVE."""
        d = self.dynamic()
        out = []
        if DT_RELA in d:
            off, size = self.vaddr_to_offset(d[DT_RELA]), d.get(DT_RELASZ, 0)
            ent = 24 if self.is64 else 12
            for o in range(off, off + size, ent):
                if self.is64:
                    r_off, info, add = struct.unpack_from("<QQq", self.data, o)
                    rtype, sym = info & 0xFFFFFFFF, info >> 32
                else:
                    r_off, info, add = struct.unpack_from("<IIi", self.data, o)
                    rtype, sym = info & 0xFF, info >> 8
                out.append((r_off, rtype, sym, add))
        if DT_RELR in d:
            rel_type = R_AARCH64_RELATIVE if self.is64 else R_AARCH64_P32_RELATIVE
            off, size, w = self.vaddr_to_offset(d[DT_RELR]), d.get(DT_RELRSZ, 0), self.ptr
            fmt = "<Q" if self.is64 else "<I"
            where = 0
            for o in range(off, off + size, w):
                (e,) = struct.unpack_from(fmt, self.data, o)
                if not e & 1:
                    out.append((e, rel_type, 0, None))
                    where = e + w
                else:
                    bits, i = e >> 1, 0
                    while bits:
                        if bits & 1:
                            out.append((where + i * w, rel_type, 0, None))
                        bits >>= 1
                        i += 1
                    where += (8 * w - 1) * w
        return out


def read(path: str) -> Elf:
    with open(path, "rb") as f:
        return Elf(f.read())
