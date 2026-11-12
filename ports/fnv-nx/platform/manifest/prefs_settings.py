#!/usr/bin/env python3
"""The settings of FalloutPrefs.ini that the exe registers, with their built-in defaults (L5 declared machine).

  prefs_settings.py <FalloutNV.exe> [out.tsv]

Rule (owner, launcher gate): FalloutPrefs.ini holds exactly the keys that the exe's own code requires before its main path;
every other key is absent and the exe uses its built-in defaults. This pass proves the second half statically: every setting
the prefs collection can read has a default in the exe's settings table.

Derivation, no name list and no hand-written address:
  1. S = the C string "FalloutPrefs.ini"; C = every 4-byte cell of the image that holds the relocated address of S.
  2. The collection getters G: in a function that loads a cell of C, every `call rel32; mov ecx,eax; call rel32` after the
     load (WinMain: G().Load(path)). More than one collection loads the file.
  3. The setting constructors K: every function that calls G and then calls through slot 1 of a vtable (`mov r,[r2+4]; call r`
     with `this` pushed): the collection's Add. A call of G followed by slot 2 (`+8`) is the destructor and is not a constructor.
  4. Every `call rel32` to a K, found by scanning the code for E8 and checked against a linear decode of the calling function,
     is a registration. Its arguments are the immediate pushes before it: the name (`name:Section`, a relocated string
     pointer) and the default (an immediate; a relocated string pointer; or, for a float constructor, the float constant
     loaded by `fld dword [abs]`).
A registration whose default cannot be recovered statically is reported with kind `none`; the constructors take the default
as a mandatory argument and store it in the object, so the count of `none` rows measures this pass, not the exe.
Assumption ASM-501 (function boundaries and forward flow: function starts are `55 8B EC` after `CC` padding; linear decode); the registry is verify/ASSUMPTIONS.json.
"""
import bisect, re, struct, sys

try:
    import capstone
    import pefile
except ImportError as e:
    sys.stderr.write("prefs_settings: %s\n" % e)
    sys.exit(2)


def main(exe, out):
    pe = pefile.PE(exe)
    base = pe.OPTIONAL_HEADER.ImageBase
    img = pe.get_memory_mapped_image()
    text = [s for s in pe.sections if s.Characteristics & 0x20000000][0]
    tva, tsz = text.VirtualAddress, text.Misc_VirtualSize
    seg = img[tva:tva + tsz]
    lo, hi = base + tva, base + tva + tsz
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)

    def cstr(va):
        o = va - base
        if not (0 < o < len(img)):
            return None
        e = img.find(b"\0", o)
        return img[o:e].decode("latin1") if 0 < e - o < 200 else None

    starts = [lo + m.start() + 1 for m in re.finditer(b"\xcc\x55\x8b\xec", seg)]

    def fs(a):
        return starts[bisect.bisect_right(starts, a) - 1]

    def fend(f):
        i = bisect.bisect_right(starts, f)
        return starts[i] if i < len(starts) else hi

    def decode(f):
        return list(md.disasm(img[f - base:fend(f) - base], f))

    calls = {}
    for m in re.finditer(b"\xe8", seg):
        t = lo + m.start() + 5 + struct.unpack("<i", seg[m.start() + 1:m.start() + 5])[0]
        calls.setdefault(t, []).append(lo + m.start())

    # 1. string and cells
    s_va = base + img.find(b"FalloutPrefs.ini\0")
    cells = [base + o for o in range(0, len(img) - 3, 4) if struct.unpack("<I", img[o:o + 4])[0] == s_va]
    # 2. getter G
    gs = set()
    for c in cells:
        for m in re.finditer(re.escape(struct.pack("<I", c)), seg):
            site = lo + m.start()
            ins = decode(fs(site))
            idx = [n for n, i in enumerate(ins) if i.address <= site < i.address + i.size]
            if not idx:
                continue
            n0 = idx[0]
            # the buffer the cell is appended to: `lea r,[ebp-x]` among the next pushes up to the call (strcat dest)
            buf = None
            for i in ins[n0:n0 + 8]:
                if i.mnemonic == "lea" and "[ebp -" in i.op_str:
                    buf = i.op_str.split(", ", 1)[1]
                if i.mnemonic == "call":
                    break
            if buf is None:
                continue
            # then `lea r,[buf]; push r; call G; mov ecx,eax; call L`
            for k in range(n0, len(ins) - 4):
                if (ins[k].mnemonic == "lea" and ins[k].op_str.endswith(", " + buf) and ins[k + 1].mnemonic == "push"
                        and ins[k + 2].mnemonic == "call" and ins[k + 3].op_str == "ecx, eax" and ins[k + 4].mnemonic == "call"
                        and ins[k + 1].op_str == ins[k].op_str.split(",")[0]):
                    gs.add(int(ins[k + 2].op_str, 16))
    assert gs, "no collection loads FalloutPrefs.ini"
    # 3. constructors, per collection getter
    K = {}
    for G in sorted(gs):
        for site in calls.get(G, []):
            f = fs(site)
            ins = decode(f)
            after = [i for i in ins if i.address > site]
            slot = None
            for i in after:
                m = re.match(r"(e\w\w), dword ptr \[e\w\w \+ (4|8)\]$", i.op_str) if i.mnemonic == "mov" else None
                if m:
                    slot = m.group(2)
                    break
            if slot == "4":
                K[f] = G
    rows = []
    for k in sorted(K):
        for site in calls.get(k, []):
            f = fs(site)
            ins = decode(f)
            idx = [n for n, i in enumerate(ins) if i.address == site]
            if not idx or ins[idx[0]].mnemonic != "call":
                continue  # an E8 byte inside another instruction or data
            win = ins[max(0, idx[0] - 10):idx[0]]
            obj = next((int(i.op_str.split(",")[1], 16) for i in reversed(win) if i.mnemonic == "mov" and i.op_str.startswith("ecx, 0x")), None)
            pushes = [i for i in win if i.mnemonic == "push"]
            name = dflt = None
            kind = "none"
            for i in reversed(pushes):
                try:
                    v = int(i.op_str, 16)
                except ValueError:
                    continue
                s = cstr(v) if v > base else None
                if name is None and s and ":" in s:
                    name = s
                elif name is not None and dflt is None:
                    dflt, kind = ("0x%x" % v, "imm")
                    if v > base and cstr(v) is not None:
                        dflt, kind = cstr(v), "string"
            if name is None:
                continue
            if dflt is None:
                for n, i in enumerate(win):
                    if i.mnemonic in ("fld1", "fldz"):
                        dflt, kind = ("1.0" if i.mnemonic == "fld1" else "0.0"), "float"
                    elif i.mnemonic == "fld" and i.op_str == "dword ptr [eax]":
                        # the default is the current value of another setting: `mov ecx,obj; call getter; fld [eax]`
                        o2 = next((int(j.op_str.split(",")[1], 16) for j in reversed(win[:n]) if j.mnemonic == "mov" and j.op_str.startswith("ecx, 0x")), None)
                        if o2 is not None:
                            dflt, kind = "0x%x" % o2, "setting"
            if dflt is None:
                for i in win:
                    m = re.match(r"dword ptr \[(0x[0-9a-f]+)\]$", i.op_str) if i.mnemonic == "fld" else None
                    if m:
                        dflt, kind = repr(struct.unpack("<f", img[int(m.group(1), 16) - base:int(m.group(1), 16) - base + 4])[0]), "float"
            key, sec = name.rsplit(":", 1)
            rows.append((key, sec, "0x%x" % K[k], "0x%x" % k, kind, dflt if dflt is not None else "", "0x%x" % (obj or 0), "0x%x" % site))
    byobj = {r[6]: r[0] for r in rows}
    rows = [(r[0], r[1], r[2], r[3], r[4], "setting " + byobj[r[5]] if r[4] == "setting" and r[5] in byobj else r[5], r[6], r[7]) if False else r for r in rows]
    fixed = []
    for r in rows:
        if r[4] == "setting":
            r = (r[0], r[1], r[2], r[3], r[4], byobj.get(r[5], r[5]), r[6], r[7])
        fixed.append(r)
    rows = fixed
    rows.sort()
    # Required before the main path: the settings the launcher gate reads. The gate = the callee of the function that calls
    # ShellExecuteA (the launcher start) whose result is tested (`call G; ...; test; je`) right before that call; its settings =
    # the registered prefs objects it loads (`mov ecx, obj`), directly and in its direct callees.
    objs = {r[6]: r for r in rows}
    se = None
    for e in pe.DIRECTORY_ENTRY_IMPORT:
        for im in e.imports:
            if im.name == b"ShellExecuteA":
                se = im.address
    assert se, "no ShellExecuteA import"
    gates = set()
    for m in re.finditer(b"\xff\x15" + struct.pack("<I", se), seg):
        f = fs(lo + m.start())
        ins = decode(f)
        n = [k for k, i in enumerate(ins) if i.address == lo + m.start()][0]
        for k in range(n - 1, 0, -1):
            if ins[k].mnemonic == "call" and ins[k].op_str.startswith("0x") and any(j.mnemonic == "test" for j in ins[k + 1:k + 4]) \
                    and any(j.mnemonic == "je" for j in ins[k + 1:k + 5]):
                gates.add(int(ins[k].op_str, 16))
                break
    assert len(gates) == 1, "launcher gate not unique: %s" % [hex(g) for g in gates]
    req = []
    for g in gates:
        for h in [g] + [int(i.op_str, 16) for i in decode(g) if i.mnemonic == "call" and i.op_str.startswith("0x")]:
            if not (lo <= h < hi):
                continue
            for i in decode(h):
                if i.mnemonic == "mov" and i.op_str.startswith("ecx, 0x"):
                    o = "0x%x" % int(i.op_str.split(",")[1], 16)
                    if o in objs:
                        req.append((objs[o][0], objs[o][1], o, "0x%x" % g))
    req = sorted(set(req))
    with open(out + ".required", "w") as fh:
        fh.write("# key\tsection\tobject\tgate_function\n")
        for r in req:
            fh.write("\t".join(r) + "\n")
    sys.stderr.write("required before the main path (gate settings): %s\n" % [r[0] + ":" + r[1] for r in req])
    with open(out, "w") as fh:
        fh.write("# key\tsection\tcollection_getter\tctor\tdefault_kind\tdefault\tobject\tsite\n")
        for r in rows:
            fh.write("\t".join(r) + "\n")
    n = len(rows)
    nn = sum(1 for r in rows if r[4] == "none")
    sys.stderr.write("getters %s; constructors %s; settings %d, with default %d, without %d\n" % ([hex(g) for g in sorted(gs)], [hex(k) for k in sorted(K)], n, n - nn, nn))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.stderr.write(__doc__)
        sys.exit(2)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "/dev/stdout")
