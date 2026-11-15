"""a64access test: needs LLVM 18 development files (llvm-18-dev). FAILS, never skips, when they are absent."""
import os, re, struct, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import scratch  # noqa: E402

scratch.install()
SRC = os.path.join(os.path.dirname(HERE), "a64access.cc")
BUILD = ("g++ -std=c++17 -O1 $(llvm-config-18 --cxxflags) a64access.cc -o a64access "
         "$(llvm-config-18 --ldflags --libs aarch64 mc mcdisassembler support)")

def sh(cmd, **kw):
    return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, **kw)

class A64Access(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        r = sh("llvm-config-18 --includedir")
        inc = r.stdout.strip()
        if r.returncode or not os.path.exists(os.path.join(inc, "llvm/MC/MCInstrInfo.h")):
            raise AssertionError("LLVM 18 dev headers missing: add llvm-18-dev 1:18.1.3-1ubuntu1 to env.json")
        r = sh(BUILD.replace("a64access.cc", SRC).replace("-o a64access", "-o %s/a64access" % cls.tmp))
        if r.returncode:
            raise AssertionError("build failed: " + r.stderr[-800:])

    def run_asm(self, text):
        s = os.path.join(self.tmp, "t.s"); o = os.path.join(self.tmp, "t.o"); b = os.path.join(self.tmp, "t.bin")
        self.last_bin = b
        open(s, "w").write(text + "\n")
        r = sh("aarch64-linux-gnu-as -march=armv8.5-a+lse+crypto+memtag+rcpc+fp16+sha2+aes %s -o %s && aarch64-linux-gnu-objcopy -O binary -j .text %s %s" % (s, o, o, b))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = sh("%s/a64access %s" % (self.tmp, b)).stdout.splitlines()
        res = []
        for line in out:
            p = line.split()
            d = {"op": p[1]}
            for x in p[2:]:
                k, v = x.split(":", 1); d[k] = set(v.split(",")) - {""} if k in "RW" else v
            res.append(d)
        return res

    def one(self, asm, R, W, kind=None):
        d = self.run_asm(asm)[0]
        self.assertEqual(d["R"], set(R), asm); self.assertEqual(d["W"], set(W), asm)
        if kind: self.assertEqual(d["KIND"], kind, asm)

    def test_known_wrong_in_capstone(self):
        self.one("cmp w9,w10", ["x9", "x10"], ["nzcv"])
        self.one("fmov s0,#1.0", [], ["v0"])
        self.one("ldaddal w1,w2,[x3]", ["x1", "x3"], ["x2"], "atomic")

    def test_rmw_and_writeback(self):
        self.one("movk x0,#1,lsl #16", ["x0"], ["x0"])
        self.one("ldr x0,[x1],#8", ["x1"], ["x0", "x1"])
        self.one("stp x29,x30,[sp,#-16]!", ["x29", "x30", "sp"], ["sp"])

    def test_flags_and_control(self):
        self.one("csel x0,x1,x2,eq", ["x1", "x2", "nzcv"], ["x0"])
        self.one("adds x0,x1,x2", ["x1", "x2"], ["x0", "nzcv"])
        self.one("ccmp x1,x2,#0,eq", ["x1", "x2", "nzcv"], ["nzcv"])
        self.one("bl .", ["sp"], ["x30"], "call")  # LLVM: BL has Uses=[SP], Defs=[LR]
        self.one("ret", ["x30"], [], "ret")
        self.one("svc #0", [], [], "svc")
        self.one("mrs x0, nzcv", [], ["x0", "nzcv"], "sys")  # LLVM models the system register as an immediate: not a read

    def test_simd(self):
        self.one("movi v0.4s,#0", [], ["v0"])
        self.one("fmadd d0,d1,d2,d3", ["v1", "v2", "v3", "fpcr"], ["v0"])  # LLVM models FP ops as reading FPCR
        self.one("ld1 {v0.16b},[x0]", ["x0"], ["v0"])
        self.one("casal x1,x2,[x3]", ["x1", "x2", "x3"], ["x1"], "atomic")

    def test_memory_operands(self):
        """MEM:<base>,<index>,<disp>,<size>,<writeback>; literal loads have the PC as base; '?' = not derived."""
        mem = lambda asm: self.run_asm(asm)[0]["MEM"]
        self.assertEqual(mem("ldr x0,[x1],#8"), "x1,-,0,8,8")
        self.assertEqual(mem("stp x29,x30,[sp,#-16]!"), "sp,-,-16,16,-16")
        self.assertEqual(mem("ldr q0,[sp,#32]"), "sp,-,32,16,-")
        self.assertEqual(mem("ldp x0,x1,[x2,#16]"), "x2,-,16,16,-")
        self.assertEqual(mem("ldrb w0,[x1,#3]"), "x1,-,3,1,-")
        self.assertEqual(mem("ldur w0,[x1,#-4]"), "x1,-,-4,4,-")
        self.assertEqual(mem("ldr x0,[x1,x2,lsl #3]"), "x1,x2,?,8,-")
        self.assertEqual(mem("ldr x0,."), "pc,-,?,?,-")
        self.assertEqual(mem("add x0,x1,x2"), "-")
        self.assertEqual(self.run_asm("ldxr x0,[x1]")[0]["KIND"], "excl")
        self.assertEqual(self.run_asm("brk #1")[0]["KIND"], "exc")

    def test_mem_flags_agree_with_encoding_class(self):
        """for every assembled word, the encoding-class MEM rule and LLVM's mayLoad/mayStore (MAY) agree: a class member
        (not prefetch) has MAY, and a word with MAY but no MEM is a system/hint/barrier/exception word (LLVM marks side effects
        that way). Disagreements are listed."""
        bad = []
        for d, asm in zip(self.run_asm("\n".join(INSNS)), INSNS):
            if (d["MEM"] != "-" and d["MAY"] == "" and not asm.startswith("prfm")) or (d["MEM"] == "-" and d["MAY"] != "" and d["KIND"] not in ("sys", "svc", "exc", "call")):
                bad.append((asm, d["MEM"], d["MAY"], d["KIND"]))
        self.assertEqual(bad, [])

    def test_objdump_crosscheck(self):
        """the set of words LLVM decodes equals the set objdump decodes (>= 300 instructions from templates, one per top-level
        class family) and every instruction is decoded by both."""
        self.assertGreaterEqual(len(set(INSNS)), 300)
        self.assertEqual(len(self.run_asm("\n".join(INSNS))), len(INSNS))
        dis = self.objdump_decoded(self.last_bin)
        ours = self.tool_decoded(self.last_bin)
        print("crosscheck: %d words, only-objdump %s, only-llvm %s" % (len(ours | dis), sorted(dis - ours)[:10], sorted(ours - dis)[:10]))
        self.assertEqual(ours, dis)

    def test_sweep_report(self):
        """reported, not asserted: disagreements over 4000 pseudo-random words (the reserved/unallocated space differs between
        decoders by construction). objdump asserts on some random words, so a failing chunk is bisected."""
        x, words = 12345, []
        for _ in range(4000):
            x = (x * 6364136223846793005 + 1442695040888963407) & (2**64 - 1)
            words.append(x >> 32)
        def objdump_words(ws):
            p = os.path.join(self.tmp, "sw.bin")
            with open(p, "wb") as f:
                f.write(b"".join(struct.pack("<I", w) for w in ws))
            r = sh("aarch64-linux-gnu-objdump -D -b binary -m aarch64 %s" % p)
            if r.returncode == 0:
                return {i for i in self.parse_objdump(r.stdout)}
            if len(ws) == 1:
                return set()  # objdump itself crashes on this word: it decodes nothing
            h = len(ws) // 2
            return objdump_words(ws[:h]) | {i + 4 * h for i in objdump_words(ws[h:])}
        dis = objdump_words(words)
        p = os.path.join(self.tmp, "sweep.bin")
        with open(p, "wb") as f:
            f.write(b"".join(struct.pack("<I", w) for w in words))
        ours = self.tool_decoded(p)
        print("sweep: %d words, llvm %d, objdump %d, only-llvm %d, only-objdump %d" % (len(words), len(ours), len(dis), len(ours - dis), len(dis - ours)))
        for o in sorted(dis - ours)[:6]:
            print("  only objdump: %08x" % words[o // 4])
        for o in sorted(ours - dis)[:6]:
            print("  only llvm: %08x" % words[o // 4])

    def parse_objdump(self, out):
        res = set()
        for ln in out.splitlines():
            m = re.match(r"\s*([0-9a-f]+):\t([0-9a-f]{8})\s+(\S+)", ln)
            if m and m.group(3) not in (".inst", "(bad)", ".word"):
                res.add(int(m.group(1), 16))
        return res

    def objdump_decoded(self, binp):
        return self.parse_objdump(sh("aarch64-linux-gnu-objdump -D -b binary -m aarch64 %s" % binp).stdout)

    def tool_decoded(self, binp):
        return {int(l.split()[0], 16) for l in sh("%s/a64access %s" % (self.tmp, binp)).stdout.splitlines() if "UNDECODABLE" not in l}

def _gen():
    """Systematic template expansion: one family per top-level A64 encoding class (data-processing immediate, branches/exceptions/
    system, loads/stores, data-processing register, scalar FP, AdvSIMD)."""
    X = ["x0", "x7", "x30", "xzr"]; W = ["w0", "w9", "w30", "wzr"]; V = ["v0", "v15", "v31"]; out = []
    for t in ("add", "adds", "sub", "subs"):
        out += ["%s x0,x1,#%d" % (t, i) for i in (0, 1, 4095)] + ["%s w0,w1,#1,lsl #12" % t, "%s x0,x1,x2" % t, "%s w0,w1,w2,lsl #3" % t, "%s x0,x1,w2,sxtw #2" % t]
    for t in ("and", "ands", "orr", "eor"):
        out += ["%s x0,x1,#0xff" % t, "%s w0,w1,#0xf0f0f0f0" % t, "%s x0,x1,x2,ror #5" % t]
    out += ["movz x0,#1,lsl #16", "movn w0,#2", "movk x3,#5,lsl #48", "adr x0,.", "adrp x1,.", "sbfm x0,x1,#3,#9", "ubfm w0,w1,#1,#2", "bfm x0,x1,#4,#5", "extr x0,x1,x2,#7"]
    out += ["b .", "bl .", "b.eq .", "b.ne .", "cbz x0,.", "cbnz w1,.", "tbz x0,#40,.", "tbnz w2,#3,.", "br x1", "blr x2", "ret", "ret x3", "eret",
            "svc #1", "hvc #2", "smc #3", "brk #4", "hlt #5", "dcps1", "nop", "yield", "wfe", "isb", "dsb sy", "dmb ish", "clrex", "msr nzcv,x0", "mrs x1,tpidr_el0", "msr daifset,#2", "sys #0,c7,c5,#0,x0",
            "braa x0,x1", "retaa", "blraaz x4", "paciasp", "autiasp", "xpaclri", "bti c", "hint #7"]
    for sz in ("b", "h", ""):
        out += ["ldr%s w0,[x1,#%d]" % (sz, i) for i in (0, 4)] + ["str%s w0,[x1],#4" % sz, "ldr%s w0,[x1,#-8]!" % sz, "ldur%s w0,[x1,#-3]" % sz, "ldr%s w0,[x1,x2]" % sz]
    out += ["ldrsb x0,[x1]", "ldrsh w0,[x1,#2]", "ldrsw x0,[x1,#4]", "ldtr x0,[x1,#8]", "sttr w0,[x1]", "ldr x0,.", "ldrsw x0,.", "prfm pldl1keep,[x0]", "prfm pldl1keep,.",
            "ldp x0,x1,[x2]", "stp w0,w1,[x2,#8]!", "ldp x0,x1,[x2],#16", "ldpsw x0,x1,[x2,#8]", "ldnp x0,x1,[x2]", "stnp q0,q1,[x2,#32]", "ldp d0,d1,[sp,#16]", "stp s0,s1,[x2],#8",
            "ldr b0,[x1]", "ldr h0,[x1,#2]", "ldr s0,[x1,#4]", "str d0,[x1,#8]", "ldr q0,[x1,#16]", "ldr q0,[x1,x2,lsl #4]", "ldr s0,.", "ldr q0,.", "str q0,[x1],#16", "ldur q0,[x1,#-16]",
            "ldxr w0,[x1]", "stxr w2,x0,[x1]", "ldaxr x0,[x1]", "stlxr w2,w0,[x1]", "ldxp x0,x1,[x2]", "stxp w3,x0,x1,[x2]", "ldar x0,[x1]", "stlr w0,[x1]", "ldlar x0,[x1]", "ldapr x0,[x1]", "ldapur x0,[x1,#3]", "stlur x0,[x1,#3]",
            "ldraa x0,[x1]", "ldrab x0,[x1,#8]!", "cas x0,x1,[x2]", "casal w0,w1,[x2]", "casb w0,w1,[x2]", "casp x0,x1,x2,x3,[x4]", "caspal x0,x1,x2,x3,[x4]",
            "ldadd x0,x1,[x2]", "ldclral w0,w1,[x2]", "ldeor x0,x1,[x2]", "ldset x0,x1,[x2]", "ldsmax x0,x1,[x2]", "ldumin w0,w1,[x2]", "swp x0,x1,[x2]", "swpal w0,w1,[x2]", "stadd x0,[x1]", "stclr w0,[x1]",
            "ld1 {v0.16b},[x0]", "ld1 {v0.4s,v1.4s},[x0]", "st1 {v0.2d},[x0],#16", "ld2 {v0.8b,v1.8b},[x0]", "ld3 {v0.4h-v2.4h},[x0]", "ld4 {v0.4s-v3.4s},[x0],x1", "ld1r {v0.4s},[x0]", "ld1 {v0.s}[1],[x0]", "st3 {v0.b,v1.b,v2.b}[3],[x0]",
            "stg x0,[x1]", "ldg x0,[x1]", "irg x0,x1", "dc zva,x0", "dc civac,x0", "ldr x0,[x1,w2,uxtw #3]", "ldr x0,[x1,x2,sxtx]"]
    for t in ("adc", "adcs", "sbc", "sbcs"):
        out += ["%s x0,x1,x2" % t, "%s w0,w1,w2" % t]
    out += ["cmp w9,w10", "cmn x1,#5", "tst x0,x1", "ccmp x1,x2,#0,eq", "ccmn w1,#3,#4,ne", "csel x0,x1,x2,eq", "csinc w0,w1,w2,ne", "csinv x0,x1,x2,lt", "csneg x0,x1,x2,ge", "cset w0,eq",
            "madd x0,x1,x2,x3", "msub w0,w1,w2,w3", "smaddl x0,w1,w2,x3", "umulh x0,x1,x2", "smulh x0,x1,x2", "sdiv x0,x1,x2", "udiv w0,w1,w2", "lslv x0,x1,x2", "asrv w0,w1,w2", "rorv x0,x1,x2",
            "rbit x0,x1", "rev w0,w1", "rev16 x0,x1", "clz x0,x1", "cls w0,w1", "crc32b w0,w1,w2", "crc32cx w0,w1,x2", "bic x0,x1,x2", "orn w0,w1,w2", "eon x0,x1,x2", "neg x0,x1", "mvn w0,w1", "mov x0,x1",
            "pacia x0,x1", "autia x0,x1", "setf8 w0", "rmif x0,#3,#4", "mul x0,x1,x2"]
    for t in ("fadd", "fsub", "fmul", "fdiv", "fmax", "fmin", "fnmul", "fmaxnm"):
        out += ["%s s0,s1,s2" % t, "%s d0,d1,d2" % t]
    out += ["fmov s0,#1.0", "fmov d0,#-2.5", "fmov s0,s1", "fmov w0,s1", "fmov x0,d1", "fmov d0,x1", "fmov v0.d[1],x1", "fmov x0,v1.d[1]", "fabs d0,d1", "fneg s0,s1", "fsqrt d0,d1", "frintn s0,s1", "frintz d0,d1",
            "fmadd d0,d1,d2,d3", "fnmsub s0,s1,s2,s3", "fcmp s0,s1", "fcmpe d0,#0.0", "fccmp s0,s1,#0,eq", "fcsel d0,d1,d2,eq", "fcvt d0,s1", "fcvt s0,d1", "fcvt h0,s1", "fcvtzs w0,s1", "fcvtzu x0,d1", "fcvtms w0,d1",
            "scvtf s0,w1", "ucvtf d0,x1", "scvtf d0,x1,#8", "fcvtzs x0,d1,#4", "fjcvtzs w0,d1"]
    for t in ("add", "sub", "mul", "cmeq", "cmgt", "umax", "smin", "mla", "uzp1", "zip2", "trn1", "addp"):
        out += ["%s v0.4s,v1.4s,v2.4s" % t, "%s v0.16b,v1.16b,v2.16b" % t]
    for t in ("and", "orr", "eor", "bic"):
        out += ["%s v0.16b,v1.16b,v2.16b" % t, "%s v0.8b,v1.8b,v2.8b" % t]
    for t in ("fadd", "fmul", "fmla", "fmax", "fcmeq", "faddp"):
        out += ["%s v0.4s,v1.4s,v2.4s" % t, "%s v0.2d,v1.2d,v2.2d" % t]
    out += ["movi v0.4s,#0", "movi v1.2d,#0xff00ff00ff00ff00", "mvni v0.4s,#1", "orr v0.4s,#1", "fmov v0.4s,#1.0", "dup v0.4s,w1", "dup v0.4s,v1.s[2]", "ins v0.s[1],w2", "ins v0.d[0],v1.d[1]", "umov w0,v1.s[1]", "smov x0,v1.b[3]",
            "shl v0.4s,v1.4s,#3", "ushr v0.2d,v1.2d,#7", "sshr v0.8h,v1.8h,#2", "sli v0.4s,v1.4s,#4", "shrn v0.4h,v1.4s,#3", "uxtl v0.4s,v1.4h", "xtn v0.4h,v1.4s", "sqxtn v0.8b,v1.8h", "tbl v0.16b,{v1.16b},v2.16b",
            "ext v0.16b,v1.16b,v2.16b,#3", "rev64 v0.4s,v1.4s", "cnt v0.16b,v1.16b", "addv s0,v1.4s", "uaddlv d0,v1.4s", "fmaxv s0,v1.4s", "frecpe v0.4s,v1.4s", "frsqrte s0,s1", "fcvtzs v0.4s,v1.4s", "scvtf v0.2d,v1.2d",
            "aese v0.16b,v1.16b", "aesmc v0.16b,v1.16b", "sha1c q0,s1,v2.4s", "sha256h q0,q1,v2.4s", "pmull v0.1q,v1.1d,v2.1d", "smull v0.2d,v1.2s,v2.2s", "umlal v0.4s,v1.4h,v2.4h", "sqdmulh v0.4s,v1.4s,v2.4s",
            "mul v0.4s,v1.4s,v2.s[1]", "fmla v0.4s,v1.4s,v2.s[3]", "fmul d0,d1,v2.d[1]", "add d0,d1,d2", "cmeq d0,d1,#0", "abs d0,d1", "sqadd b0,b1,b2", "fcvtn v0.4h,v1.4s", "bsl v0.16b,v1.16b,v2.16b", "bif v0.8b,v1.8b,v2.8b",
            "mrs x0,nzcv", "mrs x0,fpcr", "msr fpsr,x1", "ldaddal w1,w2,[x3]", "fmov s0,#1.0"]
    return list(dict.fromkeys(out))

INSNS = _gen()

if __name__ == "__main__":
    unittest.main()
