#!/usr/bin/env python3
"""env_const_export - the constants table of the "objstub" gate, produced from the BUILT objects.

    env_const_export.py --tsv env_const.tsv --archive libfnvnx-platform.a --cmds compile_cmds.txt --root <fnv-nx dir>
                        --tc <toolchain prefix> --out env_const.objstub.tsv [--work DIR]

Output rows `symbol<TAB>kind<TAB>value<TAB>contract_ref`, sorted, no header (the format of verify/objstub.py read_rows):
symbol = the ELF symbol of the function in the archive; kind u32 | u64 (by the size of the row's return type) or addr
(a pointer constant that is a link-time address; value = the symbol it names); value = the constant as a number (the
result register's low 32 / all 64 bits, unsigned); contract_ref = the row's source reference (`ENV:<id>` / `MSDN:<name>`).
Facts, no text evaluation: for every source file named in env_const.tsv whose object is in the archive, the file is compiled
once more with its own flags (compile_cmds.txt, written by build_guest.sh: `c|cxx<TAB>command`) and with its generated
bodies replaced by the probe form of gen_env_const.py --probe. The compiler then (1) evaluates each row's constant in
the includer's own scope (`const <ret> fnvnx_probe_<line> = (<value>)`: the value is read from the object's data,
a relocation there makes it an address), and (2) names each function: an asm marker in the body is a relocation from the
row's line number to the function's own section `.text.<symbol>` (-ffunction-sections), so no demangler or signature
text is matched. The named symbol must be a defined non-weak FUNC of the row's object in the archive (nm --format=sysv) and,
for C++ rows, must demangle (c++filt) to `[enclosing namespaces::]<row symbol>(...`. A row whose file is not in the archive (host-side files) is
reported on stderr and left out. Any other failure names the row and exits 1."""
import os
import re
import shlex
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
COLS = ["symbol", "linkage", "ret", "params", "value", "source", "quals", "file"]


class Elf:
    """The little-endian ELF32/ELF64 relocatable object `data`: sections, symbols, and the relocations of each section."""

    def __init__(self, data):
        if data[:4] != b"\x7fELF" or data[5] != 1:
            raise ValueError("not a little-endian ELF object")
        self.d = data
        self.w = 8 if data[4] == 2 else 4
        if self.w == 8:
            shoff, = struct.unpack_from("<Q", data, 0x28)
            shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 0x3A)
        else:
            shoff, = struct.unpack_from("<I", data, 0x20)
            shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 0x2E)
        self.sec = []                                   # (name_off, type, offset, size, link, info)
        for i in range(shnum):
            b = shoff + i * shentsize
            if self.w == 8:
                nm, ty, _, _, off, sz, link, info = struct.unpack_from("<IIQQQQII", data, b)
            else:
                nm, ty, _, _, off, sz, link, info = struct.unpack_from("<IIIIIIII", data, b)
            self.sec.append((nm, ty, off, sz, link, info))
        stroff = self.sec[shstrndx][2]
        self.names = [self.cstr(stroff + s[0]) for s in self.sec]
        self.syms = []                                  # (name, shndx, value, size, type)
        for i, s in enumerate(self.sec):
            if s[1] == 2:                               # SHT_SYMTAB
                strt = self.sec[s[4]][2]
                step = 24 if self.w == 8 else 16
                for o in range(s[2], s[2] + s[3], step):
                    if self.w == 8:
                        nm, info, _, shndx, val, sz = struct.unpack_from("<IBBHQQ", data, o)
                    else:
                        nm, val, sz, info, _, shndx = struct.unpack_from("<IIIBBH", data, o)
                    self.syms.append((self.cstr(strt + nm), shndx, val, sz, info & 15))
        self.rel = {}                                   # target section index -> {offset: (symbol index, addend)}
        for s in self.sec:
            if s[1] in (4, 9):                          # SHT_RELA, SHT_REL
                tab = self.rel.setdefault(s[5], {})
                step = (24 if self.w == 8 else 12) if s[1] == 4 else (16 if self.w == 8 else 8)
                for o in range(s[2], s[2] + s[3], step):
                    if self.w == 8:
                        off, info = struct.unpack_from("<QQ", data, o)
                        add = struct.unpack_from("<q", data, o + 16)[0] if s[1] == 4 else 0
                        tab[off] = (info >> 32, add)
                    else:
                        off, info = struct.unpack_from("<II", data, o)
                        add = struct.unpack_from("<i", data, o + 8)[0] if s[1] == 4 else 0
                        tab[off] = (info >> 8, add)

    def cstr(self, o):
        return self.d[o:self.d.index(b"\0", o)].decode("latin1")

    def section(self, name):
        return self.names.index(name) if name in self.names else None

    def bytes_of(self, idx, off, n):
        s = self.sec[idx]
        return b"\0" * n if s[1] == 8 else self.d[s[2] + off:s[2] + off + n]     # SHT_NOBITS reads as zero


def nm_members(nm, archive):
    """{member: {symbol: [(class, type)]}} from `nm --format=sysv` of the archive."""
    out = subprocess.run([nm, "--format=sysv", archive], stdout=subprocess.PIPE, check=True).stdout.decode("latin1")
    res, cur = {}, None
    for line in out.split("\n"):
        m = re.match(r"Symbols from .*\[(.*)\]:", line)
        if m:
            cur = res.setdefault(m.group(1), {})
            continue
        f = [x.strip() for x in line.split("|")]
        if cur is not None and len(f) >= 7 and f[0] and f[0] != "Name" and f[2]:
            cur.setdefault(f[0], []).append((f[2], f[3]))
    return res


def main(argv):
    opt = {}
    for i in range(0, len(argv) - 1, 2):
        opt[argv[i]] = argv[i + 1]
    tc, root = opt["--tc"], os.path.realpath(opt["--root"])
    nm, cxxfilt, ar = (os.path.join(tc, "bin", "aarch64-none-elf-" + t) for t in ("nm", "c++filt", "ar"))
    cmds = {}
    for line in open(opt["--cmds"]):
        k, _, c = line.rstrip("\n").partition("\t")
        cmds[k] = shlex.split(c)
    members = set(subprocess.run([ar, "t", opt["--archive"]], stdout=subprocess.PIPE, check=True).stdout.decode().split())
    syms = nm_members(nm, opt["--archive"])
    lines = open(opt["--tsv"], encoding="utf-8").read().split("\n")
    if lines[0].split("\t") != COLS:
        raise SystemExit("env_const_export: bad header in " + opt["--tsv"])
    rows = [(n, dict(zip(COLS, l.split("\t")))) for n, l in enumerate(lines[1:], 2) if l]
    byfile = {}
    for n, r in rows:
        byfile.setdefault(r["file"], []).append((n, r))
    out, errors, skipped = {}, [], []
    work = opt.get("--work") or tempfile.mkdtemp(prefix="env_const_export.")
    for f, frs in sorted(byfile.items()):
        member = "platform_" + f.replace("/", "_") + ".o"
        if member not in members:
            skipped.append(f)
            continue
        src = os.path.join(root, "platform", f)
        text = open(src, encoding="utf-8").read()
        inc = re.findall(r'#include\s+"([^"]*env_const[^"]*\.gen\.inc)"', text)
        if len(inc) != 1:
            errors.append("%s: expected exactly one env_const .gen.inc include, found %d" % (f, len(inc)))
            continue
        td = os.path.join(work, f.replace("/", "_"))
        os.makedirs(td, exist_ok=True)
        tsrc = os.path.join(td, os.path.basename(src))
        with open(tsrc, "w", encoding="utf-8") as fh:
            fh.write(text)
        r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "gen_env_const.py"), opt["--tsv"],
                            os.path.join(td, os.path.basename(inc[0])), "--only", f, "--probe"], stderr=subprocess.PIPE)
        if r.returncode:
            errors.append("%s: gen_env_const --probe: %s" % (f, r.stderr.decode()[:200]))
            continue
        kind = "c" if f.endswith(".c") else "cxx"
        obj = os.path.join(td, "probe.o")
        r = subprocess.run(cmds[kind] + ["-iquote", os.path.dirname(src), "-c", tsrc, "-o", obj], cwd=root,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode:
            errors.append("%s: probe compile failed: %s" % (f, r.stdout.decode()[:300]))
            continue
        e = Elf(open(obj, "rb").read())
        fnsym, mrow = {}, e.section(".fnvnx_row")
        if mrow is not None:
            for off, (si, _) in e.rel.get(mrow, {}).items():
                nmk = struct.unpack_from("<I", e.bytes_of(mrow, off - 4, 4))[0]
                sname, shndx = e.syms[si][0], e.syms[si][1]
                fnsym[nmk] = e.names[shndx][len(".text."):] if e.syms[si][4] == 3 and e.names[shndx].startswith(".text.") else sname
        for n, row in frs:
            what = "%s (%s:%d)" % (row["symbol"], f, n)
            cs = [s for s in e.syms if s[0] == "fnvnx_probe_%d" % n and s[1] < 0xff00]
            if n not in fnsym or len(cs) != 1:
                errors.append("%s: no probe result (function marker %s, constant %d)" % (what, n in fnsym, len(cs)))
                continue
            fn = fnsym[n]
            ents = syms.get(member, {}).get(fn, [])
            if not any(c not in ("U", "W", "V", "w", "v") and t == "FUNC" for c, t in ents):
                errors.append("%s: symbol %s is not a defined non-weak function of %s" % (what, fn, member))
                continue
            if row["linkage"] == "C":
                if fn != row["symbol"]:
                    errors.append("%s: C row but the compiler's symbol is %s" % (what, fn))
                    continue
            else:
                dem = subprocess.run([cxxfilt, fn], stdout=subprocess.PIPE, check=True).stdout.decode().strip()
                if ("::" + row["symbol"] + "(") not in "::" + dem.split("(anonymous namespace)::")[-1] and not dem.startswith(row["symbol"] + "("):
                    errors.append("%s: symbol %s demangles to %s" % (what, fn, dem))
                    continue
            _, shndx, val, size, _ = cs[0]
            rel = e.rel.get(shndx, {}).get(val)
            if rel is not None:
                tgt = e.syms[rel[0]]
                if tgt[4] == 3 or not tgt[0] or rel[1]:
                    errors.append("%s: the constant is the address of an unnamed object (%s+%d), no symbol names it" % (what, e.names[tgt[1]], rel[1]))
                    continue
                kind, value = "addr", tgt[0]
            else:
                if size not in (1, 2, 4, 8):
                    errors.append("%s: the constant has size %d" % (what, size))
                    continue
                kind = "u64" if size == 8 else "u32"
                value = str(int.from_bytes(e.bytes_of(shndx, val, size), "little"))
            if fn in out:
                errors.append("%s: symbol %s given twice" % (what, fn))
                continue
            out[fn] = (kind, value, row["source"].split(" ")[0])
    if errors:
        for m in errors:
            sys.stderr.write("env_const_export: %s\n" % m)
        return 1
    for f in skipped:
        sys.stderr.write("env_const_export: %s is not in the archive (host-side file), its rows are not in the table\n" % f)
    with open(opt["--out"], "w", encoding="utf-8") as fh:
        for k in sorted(out):
            fh.write("%s\t%s\t%s\t%s\n" % ((k,) + out[k]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
