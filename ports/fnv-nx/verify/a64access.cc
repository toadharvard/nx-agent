// a64access: per-word register read/write sets of AArch64 code, derived from LLVM MC (MCInstrDesc).
// usage: a64access <file or - for stdin> [<start> <end>]...   (byte offsets, little-endian 32-bit words; no ranges = whole input)
// output line: "<offset hex> <opcode> R:<ids> W:<ids> MEM:<base,index,disp,size,wb | - | pc,...> KIND:<kind> MAY:<L|S>"  or "<offset> UNDECODABLE"
// Rule: operands [0,NumDefs) are defs, remaining register operands are uses, plus ImplicitDefs/ImplicitUses;
// a read-modify-write def is listed again as a tied use operand by the opcode (TIED_TO), so it is read by the same rule.
// Register ids are normalised through MCRegisterInfo super-register chains to the architectural X/SP/V/NZCV/FPCR/FPSR.
#include "llvm/MC/MCAsmInfo.h"
#include "llvm/MC/MCContext.h"
#include "llvm/MC/MCDisassembler/MCDisassembler.h"
#include "llvm/MC/MCInst.h"
#include "llvm/MC/MCInstrInfo.h"
#include "llvm/MC/MCRegisterInfo.h"
#include "llvm/MC/MCSubtargetInfo.h"
#include "llvm/MC/TargetRegistry.h"
#include "llvm/Support/TargetSelect.h"
#include "llvm/TargetParser/Triple.h"
#include <cstdio>
#include <cstdlib>
#include <set>
#include <string>
#include <vector>
using namespace llvm;
static const MCRegisterInfo *RI;
static bool isreg(const std::string &n, char c) {  // exactly <c><decimal digits>
  if (n.size() < 2 || n[0] != c) return false;
  for (size_t i = 1; i < n.size(); i++) if (!isdigit((unsigned char)n[i])) return false;
  return true;
}
// Architectural register of r: the unique super-register (or r itself) whose MCRegisterInfo name is exactly X<n>, SP, Z<n> (the
// SVE view of V<n>), NZCV, FPCR or FPSR; aliases FP/LR are x29/x30. Register tuples (X0_X1, ...) and sub-registers never match.
static std::string canon(unsigned r) {
  std::string best;
  auto consider = [&](unsigned c) {
    std::string n = RI->getName(c);
    if (n == "NZCV" || n == "FPCR" || n == "FPSR" || n == "SP") best = n;
    else if (n == "FP") best = "X29";
    else if (n == "LR") best = "X30";
    else if (n == "XZR" || n == "WZR") best = "XZR";
    else if (isreg(n, 'X')) best = n;
    else if (isreg(n, 'Z')) best = "V" + n.substr(1);
    else if (isreg(n, 'Q') && best.empty()) best = "V" + n.substr(1);
  };
  consider(r);
  for (MCSuperRegIterator S(r, RI); S.isValid(); ++S) consider(*S);
  if (best.empty()) best = std::string("?") + RI->getName(r);
  for (auto &c : best) c = tolower(c);
  return best;
}
// Encoding-field rules (Arm ARM, A64 top-level encoding table). Bit positions are of the 32-bit word.
// Load/store class: op0 = bits [28:25] = x1x0, i.e. bit27 = 1 and bit25 = 0. Within it:
//   literal (no base register, PC-relative): bits [29:27] = 011, [25:24] = 00
//   pair: [29:27] = 101, [25:23] = 000 (non-temporal) | 001 post | 010 offset | 011 pre; imm7 = [21:15] scaled by the register size
//   single: [29:27] = 111, [25:24] = 01 unsigned offset (imm12 [21:10] scaled by 2^size);
//           [25:24] = 00, [21] = 0: imm9 = [20:12], [11:10] = 00 unscaled, 01 post, 10 unprivileged, 11 pre;
//           [25:24] = 00, [21] = 1, [11:10] = 10: register offset, index Rm = [20:16]; [11:10] = 00: LSE atomic; else PAC load.
//   all load/store classes carry the base register Rn at [9:5] (31 = sp), except literal.
// Anything not listed has displacement/size "?" (not derived); a consumer must treat it as unknown.
static std::string memfields(uint32_t w) {
  if (!((w >> 27 & 1) && !(w >> 25 & 1))) return "-";
  if ((w >> 27 & 7) == 3 && (w >> 24 & 3) == 0) return "pc,-,?,?,-";  // literal: base is the PC
  unsigned rn = w >> 5 & 31;
  std::string base = rn == 31 ? "sp" : "x" + std::to_string(rn), idx = "-", disp = "?", size = "?", wb = "-";
  auto sx = [](uint32_t v, int bits) { return (int)(v << (32 - bits)) >> (32 - bits); };
  unsigned V = w >> 26 & 1, sz = w >> 30;
  if ((w >> 27 & 7) == 7 && (w >> 24 & 3) <= 1) {
    unsigned lg = (V && sz == 0 && (w >> 23 & 1)) ? 4 : sz;
    size = std::to_string(1 << lg);
    if ((w >> 24 & 3) == 1) disp = std::to_string((w >> 10 & 0xFFF) << lg);
    else if (!(w >> 21 & 1)) {
      int imm = sx(w >> 12 & 0x1FF, 9); unsigned m = w >> 10 & 3;
      disp = std::to_string(m == 1 ? 0 : imm);
      if (m == 1 || m == 3) wb = std::to_string(imm);
    } else if ((w >> 10 & 3) == 2) idx = "x" + std::to_string(w >> 16 & 31);
    else size = "?";
  } else if ((w >> 27 & 7) == 5) {
    int b = V ? (sz == 0 ? 4 : sz == 1 ? 8 : 16) : (sz == 2 ? 8 : 4);
    int imm = sx(w >> 15 & 0x7F, 7) * b; unsigned m = w >> 23 & 7;
    if (m <= 3 || m == 2 || m == 3) {
      size = std::to_string(2 * b); disp = std::to_string(m == 1 ? 0 : imm);
      if (m == 1 || m == 3) wb = std::to_string(imm);
    }
  }
  return base + "," + idx + "," + disp + "," + size + "," + wb;
}
// Kind by encoding class; nullptr = decide from MCInstrDesc flags.
//   exception generation: [31:24] = 0xD4; SVC = opc[23:21] 000 with LL[1:0] 01; HVC LL 10; SMC LL 11; else "exc" (BRK, HLT, DCPS)
//   system: [31:22] = 0x354 (MSR/MRS/SYS/hint/barrier)
//   atomic (read-modify-write): LSE ops [29:27]=111,[26]=0,[25:24]=00,[21]=1,[11:10]=00 minus LDAPR/ST* variants with [15:12]=1100;
//     CAS/CASP [29:24]=001000,[21]=1,[14:10]=11111 with (bit23=1 or bit31=0)
//   excl: other [29:24] = 001000 words (load/store exclusive, acquire/release)
static const char *classify(uint32_t w) {
  if (w >> 24 == 0xD4) { unsigned opc = w >> 21 & 7, ll = w & 3; return opc == 0 && ll == 1 ? "svc" : opc == 0 && ll >= 2 ? "svc" : "exc"; }
  if (w >> 22 == 0x354) return "sys";
  if ((w >> 27 & 1) && !(w >> 25 & 1)) {
    if ((w >> 27 & 7) == 7 && !(w >> 26 & 1) && (w >> 24 & 3) == 0 && (w >> 21 & 1) && (w >> 10 & 3) == 0 && (w >> 12 & 15) != 12) return "atomic";
    if ((w >> 24 & 63) == 8) {
      if ((w >> 21 & 1) && (w >> 10 & 31) == 31 && ((w >> 23 & 1) || !(w >> 31))) return "atomic";
      return "excl";
    }
  }
  return nullptr;
}
int main(int argc, char **argv) {
  if (argc < 2) return 2;
  LLVMInitializeAArch64TargetInfo(); LLVMInitializeAArch64TargetMC(); LLVMInitializeAArch64Disassembler();
  std::string T = "aarch64-linux-gnu", err;
  const Target *Tg = TargetRegistry::lookupTarget(T, err);
  if (!Tg) { fprintf(stderr, "%s\n", err.c_str()); return 3; }
  std::unique_ptr<MCRegisterInfo> MRI(Tg->createMCRegInfo(T));
  MCTargetOptions O;
  std::unique_ptr<MCAsmInfo> AI(Tg->createMCAsmInfo(*MRI, T, O));
  std::unique_ptr<MCInstrInfo> II(Tg->createMCInstrInfo());
  std::unique_ptr<MCSubtargetInfo> ST(Tg->createMCSubtargetInfo(T, "generic", "+v8.5a,+fp-armv8,+neon,+lse,+crc,+crypto,+mte,+rcpc,+rcpc-immo,+fullfp16,+rdm,+sve,+pauth,+jsconv,+sb,+ssbs,+bti,+sha2,+aes,+sha3,+sm4,+dotprod,+fp16fml,+predres,+flagm,+altnzcv,+ccpp,+rand,+specrestrict,+lse128,+lor,+sve2,+sve2p1,+sme,+sme2,+sme2p1,+rcpc3,+the,+d128,+ls64,+mops,+hbc,+cssc,+wfxt,+xs,+tlb-rmi,+ecv,+sve2-bitperm,+i8mm,+bf16,+f32mm,+f64mm,+flagm,+pauth-lr,+fp8,+lut,+faminmax"));
  MCContext Ctx(Triple(T), AI.get(), MRI.get(), ST.get());
  std::unique_ptr<MCDisassembler> D(Tg->createMCDisassembler(*ST, Ctx));
  RI = MRI.get();
  FILE *f = std::string(argv[1]) == "-" ? stdin : fopen(argv[1], "rb"); if (!f) return 4;
  std::vector<uint8_t> buf; { uint8_t b[1 << 16]; size_t n; while ((n = fread(b, 1, sizeof b, f))) buf.insert(buf.end(), b, b + n); }
  if (f != stdin) fclose(f);
  std::vector<std::pair<size_t, size_t>> rg;
  for (int i = 2; i + 1 < argc; i += 2) rg.push_back({strtoull(argv[i], 0, 0), strtoull(argv[i + 1], 0, 0)});
  if (rg.empty()) rg.push_back({0, buf.size()});
  for (auto [s, e] : rg) for (size_t o = s; o + 4 <= e && o + 4 <= buf.size(); o += 4) {
    MCInst I; uint64_t sz;
    if (D->getInstruction(I, sz, ArrayRef<uint8_t>(&buf[o], 4), o, nulls()) != MCDisassembler::Success) { printf("%zx UNDECODABLE\n", o); continue; }
    const MCInstrDesc &d = II->get(I.getOpcode());
    std::set<std::string> R, W;
    unsigned nd = d.getNumDefs();
    for (unsigned i = 0; i < I.getNumOperands(); i++) {
      const MCOperand &op = I.getOperand(i);
      if (!op.isReg() || !op.getReg()) continue;
      std::string c = canon(op.getReg());
      if (c == "xzr") continue;
      (i < nd ? W : R).insert(c);
    }
    for (MCPhysReg r : d.implicit_defs()) W.insert(canon(r));
    for (MCPhysReg r : d.implicit_uses()) R.insert(canon(r));
    uint32_t w = buf[o] | buf[o + 1] << 8 | buf[o + 2] << 16 | (uint32_t)buf[o + 3] << 24;
    std::string mem = memfields(w), name = II->getName(I.getOpcode()).str();
    const char *k = classify(w);
    if (!k) k = d.isCall() ? "call" : d.isReturn() ? "ret" : d.isIndirectBranch() ? "indirect" : d.isBranch() ? "branch" : "other";
    printf("%zx %s R:", o, name.c_str());
    bool first = true; for (auto &r : R) { printf(first ? "%s" : ",%s", r.c_str()); first = false; }
    printf(" W:"); first = true; for (auto &r : W) { printf(first ? "%s" : ",%s", r.c_str()); first = false; }
    printf(" MEM:%s KIND:%s MAY:%s%s\n", mem.c_str(), k, d.mayLoad() ? "L" : "", d.mayStore() ? "S" : "");
  }
}
