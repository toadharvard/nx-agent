#!/usr/bin/env python3
"""Import manifest of a PE module set (R10a).

Rule: the manifest is the union of the import tables (static and delay) of every
module in the load closure. The Windows loader resolves every slot before the
entry point runs, so every slot is part of the load contract; nothing is dropped
because a trace did not call it. Reference counts are reported as information.

Also lists every call site of the dynamic-binding imports (LoadLibrary*,
GetModuleHandle*, GetProcAddress, CoCreateInstance) with the constant string or
GUID operand when the bytes before the call push one. A site without a constant
is reported as `dynamic`: the implementation must be total over all names anyway.

Usage: pemanifest.py <dir-with-modules> <root.exe> <outdir> [contract-only.dll,...]
(contract-only: a module present in the directory whose exports are implemented by
contract under ENV-TRUTH instead of being lifted; it is not loaded, so its own imports
are not part of the manifest.)
Output (exe-derived; keep out of the public repository):
  imports.tsv   module, dll, name|#ordinal, slot_va, code_refs, data_refs
  closure.tsv   module, role (root|static|absent), imports, exports
  dynsites.tsv  module, site_va, api, operand
"""
import bisect, collections, os, struct, sys
import pefile

DYN = {"LoadLibraryA", "LoadLibraryW", "LoadLibraryExA", "LoadLibraryExW",
       "GetModuleHandleA", "GetModuleHandleW", "GetProcAddress",
       "CoCreateInstance"}


def relocs(pe):
    out = []
    base = pe.OPTIONAL_HEADER.ImageBase
    for blk in getattr(pe, "DIRECTORY_ENTRY_BASERELOC", []):
        for e in blk.entries:
            if e.type == 3:
                out.append(base + e.rva)
    out.sort()
    return out


class Mod:
    def __init__(self, path):
        self.name = os.path.basename(path).lower()
        self.pe = pe = pefile.PE(path, fast_load=True)
        pe.parse_data_directories(directories=[0, 1, 5, 13])
        self.base = pe.OPTIONAL_HEADER.ImageBase
        self.img = pe.get_memory_mapped_image()
        self.relocs = relocs(pe)
        self.imports = []  # (dll, name, slot)
        for kind in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
            for e in getattr(pe, kind, []):
                for i in e.imports:
                    nm = i.name.decode() if i.name else "#%d" % i.ordinal
                    self.imports.append((e.dll.decode().lower(), nm, i.address))
        ex = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)
        self.exports = len(ex.symbols) if ex else 0
        self.code = [(self.base + s.VirtualAddress, s.Misc_VirtualSize)
                     for s in pe.sections if s.Characteristics & 0x20000000]

    def u32(self, va):
        o = va - self.base
        return struct.unpack_from("<I", self.img, o)[0] if 0 <= o <= len(self.img) - 4 else None

    def in_code(self, va):
        return any(a <= va < a + n for a, n in self.code)

    def cstr(self, va):
        o = va - self.base
        if not (0 <= o < len(self.img)):
            return None
        e = self.img.find(b"\0", o, o + 260)
        s = self.img[o:e] if e > o else b""
        if len(s) >= 2 and all(32 <= c < 127 for c in s):
            return s.decode()
        w = self.img[o:o + 520]
        e = next((i for i in range(0, len(w) - 1, 2) if w[i:i + 2] == b"\0\0"), 0)
        try:
            t = w[:e].decode("utf-16le")
        except UnicodeDecodeError:
            return None
        return "L:" + t if len(t) >= 2 and all(32 <= ord(c) < 127 for c in t) else None

    def refs(self):
        """slot -> [code sites], [data sites] from the relocation table."""
        slots = {s for _, _, s in self.imports}
        code, data = collections.defaultdict(list), collections.defaultdict(list)
        for r in self.relocs:
            t = self.u32(r)
            if t in slots:
                (code if self.in_code(r) else data)[t].append(r)
        return code, data

    def dynsites(self, code):
        rs = self.relocs
        for dll, nm, slot in self.imports:
            if nm not in DYN:
                continue
            for site in code.get(slot, []):
                ops = []
                i = bisect.bisect_left(rs, site - 48)
                while i < len(rs) and rs[i] < site:
                    r = rs[i]
                    i += 1
                    if self.img[r - 1 - self.base] != 0x68:  # push imm32
                        continue
                    t = self.u32(r)
                    if nm == "CoCreateInstance":
                        g = self.img[t - self.base:t - self.base + 16]
                        if len(g) == 16:
                            a, b, c = struct.unpack_from("<IHH", g)
                            ops.append("{%08X-%04X-%04X-%s-%s}" % (a, b, c, g[8:10].hex().upper(), g[10:].hex().upper()))
                    else:
                        s = self.cstr(t)
                        if s:
                            ops.append(s)
                yield site, nm, " ".join(ops) if ops else "dynamic"


def main(d, root, out, contract=""):
    contract = {c.lower() for c in contract.split(",") if c}
    files = {f.lower(): os.path.join(d, f) for f in os.listdir(d)
             if f.lower().endswith((".exe", ".dll"))}
    mods, absent, todo = {}, set(), [root.lower()]
    while todo:
        n = todo.pop()
        if n in mods or n in absent:
            continue
        if n not in files or n in contract:
            absent.add(n)
            continue
        m = mods[n] = Mod(files[n])
        todo += sorted({dll for dll, _, _ in m.imports})
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "imports.tsv"), "w") as fi, \
         open(os.path.join(out, "dynsites.tsv"), "w") as fd, \
         open(os.path.join(out, "closure.tsv"), "w") as fc:
        fi.write("module\tdll\tname\tslot\tcode_refs\tdata_refs\n")
        fd.write("module\tsite\tapi\toperand\n")
        fc.write("module\trole\timports\texports\n")
        for n in sorted(mods):
            m = mods[n]
            code, data = m.refs()
            for dll, nm, slot in m.imports:
                fi.write("%s\t%s\t%s\t0x%08x\t%d\t%d\n" % (n, dll, nm, slot, len(code[slot]), len(data[slot])))
            for site, nm, op in sorted(m.dynsites(code)):
                fd.write("%s\t0x%08x\t%s\t%s\n" % (n, site, nm, op))
            fc.write("%s\t%s\t%d\t%d\n" % (n, "root" if n == root.lower() else "static", len(m.imports), m.exports))
        for n in sorted(absent):
            fc.write("%s\t%s\t-\t-\n" % (n, "contract-only" if n in contract else "absent"))
        for n in sorted(set(files) - set(mods)):
            fc.write("%s\tnot-in-static-closure\t-\t-\n" % n)


if __name__ == "__main__":
    main(*sys.argv[1:5])
