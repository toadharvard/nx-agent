// fnvcheck.cpp - clang AST checker of the fnv-nx static gate (tools/static/run.sh, docs/STATIC_CHECKS.md).
//
//   fnvcheck --root=<prepared tree> [--layout] [--seen=<keys file>] [--no-ast] <unit> -- <clang args>
//
// Walks one translation unit of the prepared tree and prints one TSV row per finding located in the tree (files
// outside --root - system headers, shim headers - are ignored):
//
//   <CHECK> \t <relpath> \t <line> \t <col> \t <detail...>
//
// A finding counts only when both its expansion and its spelling location are in the tree and in no --exclude
// directory (always excluded: paths containing /shim/include/ or /sse2neon/): an intrinsic macro or inline code of
// the shim, of sse2neon or of the clang headers that stand in for it, expanded in a unit, is not the unit's code.
//
// AST checks (default): source constructs whose meaning differs between MSVC 2008 x86-32 (what the decomp was
// matched against) and GCC AArch64 ILP32 (what we build): float->integer conversions, variable 64-bit shifts,
// argument evaluation order, varargs and format strings, volatile / polling / Interlocked fields, TLS, pointer
// sign, enum signedness, bit-fields, member pointers, empty bases, long double, wide-char library calls.
// --layout: one row per complete record defined in the tree (size, alignment, field and base offsets), for
// layout_diff.py to compare the msvc and gnu configurations; keys listed in --seen are skipped (dedup across units).
//
// Our own tool; prints file:line references and short expression excerpts only.
#include "clang/AST/ASTConsumer.h"
#include "clang/AST/ASTContext.h"
#include "clang/AST/ParentMapContext.h"
#include "clang/AST/RecordLayout.h"
#include "clang/AST/RecursiveASTVisitor.h"
#include "clang/Basic/SourceManager.h"
#include "clang/Frontend/CompilerInstance.h"
#include "clang/Frontend/FrontendActions.h"
#include "clang/Lex/Lexer.h"
#include "clang/Tooling/CommonOptionsParser.h"
#include "clang/Tooling/Tooling.h"
#include "llvm/Support/CommandLine.h"
#include <fstream>
#include <map>
#include <set>
#include <vector>
#include <string>

using namespace clang;

static llvm::cl::OptionCategory Cat("fnvcheck");
static llvm::cl::opt<std::string> Root("root", llvm::cl::desc("prepared tree; only findings inside it are printed"),
                                       llvm::cl::cat(Cat));
static llvm::cl::opt<bool> DoLayout("layout", llvm::cl::desc("print record layouts"), llvm::cl::cat(Cat));
static llvm::cl::opt<bool> NoAst("no-ast", llvm::cl::desc("skip the AST checks"), llvm::cl::cat(Cat));
static llvm::cl::opt<std::string> Seen("seen", llvm::cl::desc("file of layout keys already printed"),
                                       llvm::cl::cat(Cat));

static llvm::cl::opt<std::string> AtomicKeys("atomic-keys",
                                             llvm::cl::desc("file of ATOMIC-USE keys: report plain stores to them"),
                                             llvm::cl::cat(Cat));
static llvm::cl::opt<bool> OnlyAtomic("only-atomic", llvm::cl::desc("only the ATOMIC-PLAIN-STORE check"),
                                      llvm::cl::cat(Cat));
static llvm::cl::list<std::string> Exclude("exclude",
                                           llvm::cl::desc("directory whose code is not the tree's even below --root "
                                                          "(shim, sse2neon, generated headers; repeatable)"),
                                           llvm::cl::cat(Cat));

static std::set<std::string> SeenKeys, Atomic;

// name of a plain identifier declaration, empty for operators / constructors / conversions
static llvm::StringRef idName(const clang::NamedDecl *D) {
  return D && D->getDeclName().isIdentifier() ? D->getName() : llvm::StringRef();
}

namespace {

std::string clean(std::string s, size_t max = 80) {
  std::string o;
  bool sp = false;
  for (char c : s) {
    if (c == '\t' || c == '\n' || c == '\r' || c == ' ') {
      if (!sp && !o.empty()) o += ' ';
      sp = true;
    } else {
      o += c;
      sp = false;
    }
    if (o.size() >= max) { o += "..."; break; }
  }
  return o;
}

struct Visitor : RecursiveASTVisitor<Visitor> {
  ASTContext &C;
  SourceManager &SM;
  std::string RootDir;
  struct FileInfo { bool in, excluded; std::string rel; };
  std::map<unsigned, FileInfo> FileCache;  // FileID hash -> in tree (and not excluded), excluded, relpath
  std::vector<std::string> ExDirs;
  const FunctionDecl *CurFn = nullptr;
  std::set<const VarDecl *> Uninit;  // locals of the current function declared without an initializer, not used yet

  explicit Visitor(ASTContext &C) : C(C), SM(C.getSourceManager()) {
    RootDir = Root;
    if (!RootDir.empty() && RootDir.back() != '/') RootDir += '/';
    for (const std::string &d : Exclude) {
      llvm::SmallString<256> abs(d);
      llvm::sys::fs::make_absolute(abs);
      llvm::sys::path::remove_dots(abs, true);
      std::string e = abs.str().str();
      if (!e.empty() && e.back() != '/') e += '/';
      ExDirs.push_back(e);
    }
  }
  const FileInfo &info(FileID F) {
    auto it = FileCache.find(F.getHashValue());
    if (it != FileCache.end()) return it->second;
    FileInfo fi{false, false, std::string()};
    if (auto FE = SM.getFileEntryRefForID(F)) {
      std::string p = FE->getName().str();
      llvm::SmallString<256> abs(p);
      llvm::sys::fs::make_absolute(abs);
      llvm::sys::path::remove_dots(abs, true);
      p = abs.str().str();
      fi.excluded = p.find("/shim/include/") != std::string::npos || p.find("/sse2neon/") != std::string::npos;
      for (const std::string &e : ExDirs)
        if (p.compare(0, e.size(), e) == 0) fi.excluded = true;
      if (!fi.excluded && !RootDir.empty() && p.compare(0, RootDir.size(), RootDir) == 0) {
        fi.in = true;
        fi.rel = p.substr(RootDir.size());
      }
    }
    return FileCache.emplace(F.getHashValue(), fi).first->second;
  }
  bool shouldVisitTemplateInstantiations() const { return false; }
  bool shouldVisitImplicitCode() const { return false; }

  // location in the tree: (relpath, line, col), or false
  bool where(SourceLocation L, std::string &path, unsigned &line, unsigned &col) {
    if (L.isInvalid()) return false;
    SourceLocation E = SM.getExpansionLoc(L);
    const FileInfo &fi = info(SM.getFileID(E));
    if (!fi.in) return false;
    if (L.isMacroID() && info(SM.getFileID(SM.getSpellingLoc(L))).excluded) return false;  // a shim/sse2neon macro
    path = fi.rel;
    line = SM.getExpansionLineNumber(E);
    col = SM.getExpansionColumnNumber(E);
    return true;
  }

  std::string text(SourceRange R, size_t max = 80) {
    CharSourceRange CR = CharSourceRange::getTokenRange(SM.getExpansionRange(R).getAsRange());
    bool bad = false;
    StringRef s = Lexer::getSourceText(CR, SM, C.getLangOpts(), &bad);
    if (bad) return "?";
    return clean(s.str(), max);
  }

  std::string fn() { return CurFn ? CurFn->getQualifiedNameAsString() : std::string("-"); }

  void emit(const char *check, SourceLocation L, const std::string &detail) {
    std::string p;
    unsigned l, c;
    if (!where(L, p, l, c)) return;
    llvm::outs() << check << '\t' << p << '\t' << l << '\t' << c << '\t' << detail << '\n';
  }

  std::string ty(QualType T) {
    PrintingPolicy PP(C.getLangOpts());
    PP.SuppressTagKeyword = true;
    return T.getAsString(PP);
  }

  // ------------------------------------------------------------------ helpers
  struct Effects {
    int n = 0;                       // effectful sub-expressions (calls to non-const functions, ++/--, assignment, new)
    std::set<const Decl *> callees;  // non-const callees
    std::set<std::string> names;     // their simple names
    std::string first;               // first effectful callee's name
  };
  // a function whose visible body is `return <expression without calls or side effects>;` (the decomp's getters
  // are rarely const-qualified)
  bool trivialGetter(const FunctionDecl *FD) {
    const FunctionDecl *Def = nullptr;
    if (!FD->hasBody(Def) || !Def) return false;
    auto *CS = dyn_cast_or_null<CompoundStmt>(Def->getBody());
    if (!CS || CS->size() != 1) return false;
    auto *RS = dyn_cast<ReturnStmt>(CS->body_front());
    if (!RS || !RS->getRetValue() || RS->getRetValue()->isValueDependent()) return false;
    return !RS->getRetValue()->HasSideEffects(C, true);
  }
  // callees whose order of calls is observable: stream reads, random numbers, iterators
  static bool streamName(StringRef n) {
    std::string l = n.lower();
    for (const char *k : {"read", "pop", "next", "rand", "parse", "fetch", "advance", "consume", "getchar",
                          "getbyte", "gettoken", "getline", "seek", "skip"})
      if (l.find(k) != std::string::npos) return true;
    return false;
  }
  static bool pureName(StringRef n) {
    static const char *names[] = {"strlen", "strcmp", "stricmp", "_stricmp", "strncmp", "_strnicmp", "sqrt", "sqrtf",
                                  "fabs", "fabsf", "sin", "sinf", "cos", "cosf", "tan", "atan", "atan2", "atan2f",
                                  "asin", "acos", "acosf", "floor", "floorf", "ceil", "ceilf", "abs", "labs", "pow",
                                  "powf", "exp", "log", "fmod", "fmodf", "memcmp", "tolower", "toupper", "fnv_FloatToInt",
                                  "fnvnx_lvalue", "operator new", "operator delete"};
    for (auto *s : names)
      if (n == s) return true;
    return false;
  }
  void effects(const Stmt *S, Effects &E) {
    if (!S) return;
    if (isa<LambdaExpr>(S)) return;
    if (auto *CE = dyn_cast<CallExpr>(S)) {
      const FunctionDecl *FD = CE->getDirectCallee();
      bool pure = false;
      if (FD) {
        if (FD->getBuiltinID() || FD->hasAttr<ConstAttr>() || FD->hasAttr<PureAttr>() || pureName(idName(FD)))
          pure = true;
        if (auto *MD = dyn_cast<CXXMethodDecl>(FD))
          if (MD->isConst()) pure = true;
        if (isa<CXXConversionDecl>(FD)) pure = true;
        if (!pure && trivialGetter(FD)) pure = true;
      }
      if (!pure) {
        E.n++;
        E.callees.insert(FD ? FD->getCanonicalDecl() : nullptr);
        if (FD) E.names.insert(idName(FD).str());
        if (E.first.empty()) E.first = FD ? FD->getQualifiedNameAsString() : std::string("<indirect>");
      }
    } else if (auto *UO = dyn_cast<UnaryOperator>(S)) {
      if (UO->isIncrementDecrementOp()) E.n++;
    } else if (auto *BO = dyn_cast<BinaryOperator>(S)) {
      if (BO->isAssignmentOp()) E.n++;
    } else if (isa<CXXNewExpr>(S)) {
      E.n++;
    }
    for (const Stmt *Ch : S->children()) effects(Ch, E);
  }
  const Expr *objectOf(const Expr *E) {
    // the object a non-const member call acts on (innermost member call's base), as text
    E = E->IgnoreParenImpCasts();
    if (auto *MC = dyn_cast<CXXMemberCallExpr>(E))
      if (auto *MD = MC->getMethodDecl())
        if (!MD->isConst()) return MC->getImplicitObjectArgument();
    return nullptr;
  }

  // ------------------------------------------------------------------ function context
  bool TraverseFunctionDecl(FunctionDecl *D) {
    auto *Save = CurFn;
    auto SaveU = Uninit;
    CurFn = D;
    Uninit.clear();
    bool r = RecursiveASTVisitor::TraverseFunctionDecl(D);
    CurFn = Save;
    Uninit = SaveU;
    return r;
  }
  bool TraverseCXXMethodDecl(CXXMethodDecl *D) {
    auto *Save = CurFn;
    auto SaveU = Uninit;
    CurFn = D;
    Uninit.clear();
    bool r = RecursiveASTVisitor::TraverseCXXMethodDecl(D);
    CurFn = Save;
    Uninit = SaveU;
    return r;
  }
  bool TraverseCXXConstructorDecl(CXXConstructorDecl *D) {
    auto *Save = CurFn;
    auto SaveU = Uninit;
    CurFn = D;
    Uninit.clear();
    bool r = RecursiveASTVisitor::TraverseCXXConstructorDecl(D);
    CurFn = Save;
    Uninit = SaveU;
    return r;
  }
  bool TraverseCXXDestructorDecl(CXXDestructorDecl *D) {
    auto *Save = CurFn;
    auto SaveU = Uninit;
    CurFn = D;
    Uninit.clear();
    bool r = RecursiveASTVisitor::TraverseCXXDestructorDecl(D);
    CurFn = Save;
    Uninit = SaveU;
    return r;
  }

  // ------------------------------------------------------------------ reinterpreted memory
  // how the object reached through a pointer cast is used: walk up through parens, implicit casts, pointer
  // arithmetic on the cast pointer, `*`, `[]` and `->`/`.` to the lvalue, then look at its parent:
  // 'read' (lvalue-to-rvalue), 'write' (left side of a plain assignment), 'other' (address taken, bound, passed)
  std::string useOf(const Expr *E) {
    const Expr *Cur = E;
    bool deref = false;
    for (int depth = 0; depth < 8; depth++) {
      auto Ps = C.getParents(*Cur);
      if (Ps.empty()) break;
      const Expr *P = Ps[0].get<Expr>();
      if (!P) break;
      if (isa<ParenExpr>(P) || (isa<CastExpr>(P) && cast<CastExpr>(P)->getCastKind() == CK_NoOp)) {
        Cur = P;
        continue;
      }
      if (auto *IC = dyn_cast<ImplicitCastExpr>(P)) {
        if (IC->getCastKind() == CK_LValueToRValue) return deref ? "read" : "other";
        if (!deref) { Cur = P; continue; }  // decay etc. of the pointer itself
        return "other";
      }
      if (auto *BO = dyn_cast<BinaryOperator>(P)) {
        if (!deref && BO->isAdditiveOp()) { Cur = P; continue; }
        if (deref && BO->getOpcode() == BO_Assign && BO->getLHS()->IgnoreParens() == Cur) return "write";
        if (deref && BO->isCompoundAssignmentOp() && BO->getLHS()->IgnoreParens() == Cur) return "read";
        return deref ? "other" : "other";
      }
      if (auto *UO = dyn_cast<UnaryOperator>(P)) {
        if (UO->getOpcode() == UO_Deref) { deref = true; Cur = P; continue; }
        if (deref && UO->isIncrementDecrementOp()) return "read";
        return "other";
      }
      if (auto *AS = dyn_cast<ArraySubscriptExpr>(P)) {
        if (AS->getBase()->IgnoreParenImpCasts() == Cur->IgnoreParenImpCasts() || !deref) { deref = true; Cur = P; continue; }
        return "other";
      }
      if (auto *ME = dyn_cast<MemberExpr>(P)) {
        (void)ME;
        deref = true;
        Cur = P;
        continue;
      }
      break;
    }
    return "other";
  }
  static bool floatType(QualType T) {
    T = T.getCanonicalType();
    return T->isRealFloatingType();
  }
  // a union that has a floating member besides the integer member `F`
  static bool floatUnion(const FieldDecl *F) {
    const RecordDecl *RD = F->getParent();
    if (!RD || !RD->isUnion() || !F->getType()->isIntegerType()) return false;
    for (auto *O : RD->fields())
      if (O != F && floatType(O->getType())) return true;
    return false;
  }

  bool VisitMemberExpr(MemberExpr *E) {
    if (NoAst) return true;
    // FLOAT-BITS: the integer member of a float/integer union read - sees the float's bits (NaN payload and sign)
    if (auto *FD = dyn_cast<FieldDecl>(E->getMemberDecl()))
      if (floatUnion(FD)) {
        auto Ps = C.getParents(*E);
        const ImplicitCastExpr *IC = Ps.empty() ? nullptr : Ps[0].get<ImplicitCastExpr>();
        if (IC && IC->getCastKind() == CK_LValueToRValue)
          emit("FLOAT-BITS", E->getMemberLoc(), "union\t" + FD->getParent()->getQualifiedNameAsString() + "::" +
                                                    FD->getNameAsString() + "\t" + fn() + "\t" + text(E->getSourceRange()));
      }
    return true;
  }

  // ------------------------------------------------------------------ casts
  bool VisitCastExpr(CastExpr *E) {
    if (NoAst) return true;
    CastKind K = E->getCastKind();
    if (K == CK_BitCast || K == CK_LValueBitCast) {
      QualType To = E->getType(), From = E->getSubExpr()->getType();
      QualType ToP, FromP;
      if (K == CK_BitCast && To->isPointerType() && From->isPointerType()) {
        ToP = To->getPointeeType();
        FromP = From->getPointeeType();
      } else if (K == CK_LValueBitCast) {
        ToP = To;
        FromP = From;
      }
      if (!ToP.isNull() && !FromP.isNull() && !ToP->isDependentType() && !FromP->isDependentType()) {
        QualType TC = ToP.getCanonicalType().getUnqualifiedType(), FC = FromP.getCanonicalType().getUnqualifiedType();
        // BOOL-PUN: memory of another type read as bool - MSVC tests the whole byte (test al, al) and keeps its value
        // in integer contexts; GCC assumes 0/1 (tests bit 0, `!b` is `b ^ 1`): a byte other than 0/1 differs
        if (TC->isBooleanType() && !FC->isBooleanType()) {
          std::string use = useOf(E);
          if (use != "write")
            emit("BOOL-PUN", E->getBeginLoc(), use + "\t" + ty(From) + "->" + ty(To) + "\t" + fn() + "\t" +
                                                   text(E->getSourceRange()));
        }
        // FLOAT-BITS: a float object read through an integer type - sees the bits, where x86 and AArch64 differ for
        // generated NaNs (x86 default NaN 0xFFC00000, AArch64 0x7FC00000)
        if (floatType(FC) && TC->isIntegerType()) {
          std::string use = useOf(E);
          if (use != "write")
            emit("FLOAT-BITS", E->getBeginLoc(), "pun " + use + "\t" + ty(From) + "->" + ty(To) + "\t" + fn() + "\t" +
                                                     text(E->getSourceRange()));
        }
      }
    }
    if (K == CK_FloatingToIntegral) {
      QualType T = E->getType().getCanonicalType();
      const Expr *Sub = E->getSubExpr();
      unsigned w = C.getTypeSize(T);
      bool sgn = T->isSignedIntegerOrEnumerationType();
      Expr::EvalResult R;
      std::string kind;
      if (!Sub->isValueDependent() && Sub->EvaluateAsRValue(R, C) && R.Val.isFloat()) {
        llvm::APFloat F = R.Val.getFloat();
        llvm::APSInt I(w, !sgn);
        bool exact;
        auto st = F.convertToInteger(I, llvm::APFloat::rmTowardZero, &exact);
        if (!(st & llvm::APFloat::opInvalidOp)) return true;  // constant in range: same everywhere
        kind = "const";
      }
      // what differs: unsigned targets - negative values (MSVC _ftol2 wraps, AArch64 fcvtzu gives 0) and values
      // >= 2^w; signed targets - only values outside the target's range or NaN (x86 0x80000000.., AArch64 saturates)
      std::string cls = sgn ? (w == 64 ? "F2S64" : "F2S") : (w == 64 ? "F2U64" : "F2U");
      emit(cls.c_str(), E->getBeginLoc(),
           std::string(isa<ExplicitCastExpr>(E) || (isa<ImplicitCastExpr>(E) && cast<ImplicitCastExpr>(E)->isPartOfExplicitCast()) ? "explicit" : "implicit") + "\t" + ty(Sub->getType()) + "->" +
               ty(E->getType()) + (kind.empty() ? "" : " " + kind) + "\t" + fn() + "\t" + text(E->getSourceRange()));
    } else if (K == CK_PointerToIntegral) {
      QualType T = E->getType().getCanonicalType();
      unsigned w = C.getTypeSize(T);
      if (w > 32) {
        // GCC sign-extends a 32-bit pointer to a 64-bit integer: differs from a zero extension for addresses >= 2 GiB
        emit("P2I64", E->getBeginLoc(), ty(E->getType()) + "\t" + fn() + "\t" + text(E->getSourceRange()));
      } else if (T->isSignedIntegerType() && w == 32) {
        // signed view of an address: only matters for addresses >= 2 GiB (compare, shift right, divide)
        const Expr *Cur = E;
        for (int depth = 0; depth < 4; depth++) {
          auto Ps = C.getParents(*Cur);
          if (Ps.empty()) break;
          if (auto *BO = Ps[0].get<BinaryOperator>()) {
            BinaryOperatorKind O = BO->getOpcode();
            if (BO->isRelationalOp() || O == BO_Shr || O == BO_Div || O == BO_Rem)
              emit("P2I-SIGNED", E->getBeginLoc(),
                   std::string(BinaryOperator::getOpcodeStr(O)) + "\t" + fn() + "\t" + text(BO->getSourceRange()));
            break;
          }
          if (auto *PE = Ps[0].get<Expr>()) {
            if (isa<ParenExpr>(PE) || isa<ImplicitCastExpr>(PE)) { Cur = PE; continue; }
          }
          break;
        }
      }
    }
    return true;
  }

  // ------------------------------------------------------------------ operators
  bool VisitBinaryOperator(BinaryOperator *E) {
    if (E->isAssignmentOp()) plainStore(E->getLHS(), E->getOperatorLoc(), "assign");
    if (NoAst) return true;
    BinaryOperatorKind O = E->getOpcode();
    if (O == BO_Sub && E->getLHS()->getType()->isPointerType() && E->getRHS()->getType()->isPointerType()) {
      // pointer difference: both compilers divide by the element size with an exact-division multiply that assumes
      // the byte difference is a multiple of it (pointers into one array); for unrelated pointers the garbage differs
      QualType P = E->getLHS()->getType()->getPointeeType();
      if (!P->isDependentType() && !P->isIncompleteType() && !P->isVoidType() && !P->isFunctionType()) {
        uint64_t sz = C.getTypeSizeInChars(P).getQuantity();
        if (sz > 1 && (sz & (sz - 1)))
          emit("PTRDIFF-EXACT", E->getOperatorLoc(), std::to_string(sz) + "\t" + fn() + "\t" + text(E->getSourceRange()));
      }
    }
    if (O == BO_Shl || O == BO_Shr || O == BO_ShlAssign || O == BO_ShrAssign) {
      QualType LT = E->getLHS()->getType();
      if (LT->isDependentType() || !LT->isIntegerType()) return true;
      QualType PT = C.isPromotableIntegerType(LT) ? C.getPromotedIntegerType(LT) : LT;
      unsigned w = C.getTypeSize(PT);
      const Expr *R = E->getRHS();
      Expr::EvalResult ER;
      if (!R->isValueDependent() && R->EvaluateAsInt(ER, C)) {
        llvm::APSInt v = ER.Val.getInt();
        if (v.isNegative() || v.getLimitedValue() >= w)
          emit("SHIFT-CONST", E->getOperatorLoc(), std::to_string(w) + "\t" + fn() + "\t" + text(E->getSourceRange()));
      } else if (w == 64) {
        // MSVC x86 __allshl/__allshr/__aullshr: count >= 64 gives 0 (or the sign); AArch64 lsl/asr/lsr x: count & 63
        emit("SHIFT64-VAR", E->getOperatorLoc(), std::string(BinaryOperator::getOpcodeStr(O)) + "\t" + fn() + "\t" +
                                                     text(E->getSourceRange()));
      } else {
        // 32-bit variable shift: x86 shl/sar/shr and AArch64 lslv/asrv/lsrv both use count & 31 - same result
        emit("SHIFT32-VAR", E->getOperatorLoc(), std::string(BinaryOperator::getOpcodeStr(O)) + "\t" + fn());
      }
    } else if ((O == BO_Div || O == BO_Rem || O == BO_DivAssign || O == BO_RemAssign) &&
               E->getType()->isIntegerType()) {
      const Expr *R = E->getRHS();
      if (!R->isValueDependent() && !R->isEvaluatable(C))
        emit("DIV-VAR", E->getOperatorLoc(), std::string(BinaryOperator::getOpcodeStr(O)) + "\t" + fn());
    } else if (E->isAssignmentOp() || (E->isAdditiveOp() || E->isMultiplicativeOp() || E->isComparisonOp() ||
                                       E->isBitwiseOp())) {
      Effects L, R;
      effects(E->getLHS(), L);
      effects(E->getRHS(), R);
      if (L.n && R.n) {
        bool same = false;
        for (auto *d : L.callees)
          if (R.callees.count(d)) same = true;
        emit(E->isAssignmentOp() ? "ORDER-ASSIGN" : "ORDER-BINOP", E->getOperatorLoc(),
             std::string(same ? "same-callee" : "-") + "\t" + L.first + " | " + R.first + "\t" + fn() + "\t" +
                 text(E->getSourceRange()));
      }
    }
    if ((O == BO_EQ || O == BO_NE) && isa<CXXThisExpr>(E->getLHS()->IgnoreParenImpCasts()))
      emit("THIS-NULL", E->getOperatorLoc(), fn());
    return true;
  }

  // ------------------------------------------------------------------ calls
  void orderArgs(const char *check, SourceLocation L, ArrayRef<const Expr *> Args, const std::string &callee,
                 SourceRange R) {
    std::vector<Effects> E(Args.size());
    int n = 0;
    for (size_t i = 0; i < Args.size(); i++) {
      effects(Args[i], E[i]);
      if (E[i].n) n++;
    }
    if (n < 2) return;
    // strength: the same non-const callee in two arguments (Read() twice, rand() twice) or non-const member calls
    // on the same object; otherwise two independent effectful arguments
    std::string strength = "-";
    int streamArgs = 0;
    for (auto &e : E) {
      bool st = false;
      for (auto &nm : e.names) st |= streamName(nm);
      streamArgs += st;
    }
    if (streamArgs >= 2) strength = "stream";
    for (size_t i = 0; i < Args.size() && strength == "-"; i++)
      for (size_t j = i + 1; j < Args.size(); j++) {
        for (auto *d : E[i].callees)
          if (d && E[j].callees.count(d)) strength = "same-callee";
        const Expr *oi = objectOf(Args[i]), *oj = objectOf(Args[j]);
        if (strength == "-" && oi && oj && text(oi->getSourceRange()) == text(oj->getSourceRange()))
          strength = "same-object";
        if (strength != "-") break;
      }
    std::string firsts;
    for (auto &e : E)
      if (e.n) firsts += (firsts.empty() ? "" : ",") + e.first;
    emit(check, L, strength + "\t" + callee + "(" + clean(firsts, 120) + ")\t" + fn() + "\t" + text(R, 100));
  }

  bool printfLike(const FunctionDecl *FD, unsigned &fmtIdx) {
    if (!FD || !FD->isVariadic() || FD->getNumParams() == 0) return false;
    QualType P = FD->getParamDecl(FD->getNumParams() - 1)->getType();
    if (!P->isPointerType()) return false;
    QualType Pt = P->getPointeeType().getUnqualifiedType().getCanonicalType();
    if (!Pt->isCharType() && !Pt->isWideCharType()) return false;
    fmtIdx = FD->getNumParams() - 1;
    return true;
  }

  // classify one printf-style conversion against the argument passed for it (MSVC x86 semantics of the spec)
  void checkFormat(const CallExpr *CE, const FunctionDecl *FD, unsigned fmtIdx, const std::string &callee) {
    const Expr *F = CE->getArg(fmtIdx)->IgnoreParenImpCasts();
    auto *SL = dyn_cast<StringLiteral>(F);
    if (!SL || SL->getCharByteWidth() != 1) return;
    std::string s = SL->getString().str();
    unsigned arg = fmtIdx + 1;
    bool scanf = callee.find("scanf") != std::string::npos;
    for (size_t i = 0; i < s.size(); i++) {
      if (s[i] != '%') continue;
      size_t j = i + 1;
      if (j < s.size() && s[j] == '%') { i = j; continue; }
      while (j < s.size() && strchr("-+ #0", s[j])) j++;
      if (j < s.size() && s[j] == '*') { if (!scanf) arg++; j++; }
      while (j < s.size() && isdigit((unsigned char)s[j])) j++;
      if (j < s.size() && s[j] == '.') {
        j++;
        if (j < s.size() && s[j] == '*') { arg++; j++; }
        while (j < s.size() && isdigit((unsigned char)s[j])) j++;
      }
      std::string len;
      while (j < s.size() && strchr("hlLIwjzt0123456789", s[j])) {
        if (isdigit((unsigned char)s[j]) && len.empty()) break;
        len += s[j++];
      }
      if (j >= s.size()) break;
      char cv = s[j];
      i = j;
      bool msvcOnly = len.find('I') != std::string::npos || len == "w" || cv == 'S' || cv == 'C' || cv == 'Z';
      if (msvcOnly)
        emit("FMT-MSVC", CE->getBeginLoc(), "%" + len + cv + "\t" + callee + "\t" + fn());
      if ((cv == 's' || cv == 'c') && len.find('l') != std::string::npos)
        emit("FMT-WIDE", CE->getBeginLoc(), "%" + len + cv + "\t" + callee + "\t" + fn());
      if (scanf) { arg++; continue; }
      if (arg >= CE->getNumArgs()) {
        emit("FMT-MISSING", CE->getBeginLoc(), "%" + len + cv + "\t" + callee + "\t" + fn());
        return;
      }
      const Expr *A = CE->getArg(arg++);
      QualType AT = A->getType().getCanonicalType();
      unsigned asz = AT->isDependentType() || AT->isIncompleteType() ? 0 : C.getTypeSize(AT) / 8;
      unsigned want = 0;
      char wcls = 'i';
      if (strchr("diouxXcC", cv)) {
        want = (len == "ll" || len == "I64") ? 8 : 4;
      } else if (strchr("fFeEgGaA", cv)) {
        want = 8;
        wcls = 'f';
      } else if (strchr("sSpn", cv)) {
        want = 4;
        wcls = 'p';
      } else {
        continue;
      }
      if (AT->isRecordType()) {
        emit("FMT-STRUCT", A->getBeginLoc(), "%" + len + cv + "\t" + ty(A->getType()) + " " + std::to_string(asz) +
                                                 "B\t" + callee + "\t" + fn());
        continue;
      }
      char acls = AT->isRealFloatingType() ? 'f' : (AT->isPointerType() || AT->isArrayType()) ? 'p' : 'i';
      bool clsBad = (wcls == 'f') != (acls == 'f');
      if (clsBad || (asz && asz != want))
        emit(clsBad ? "FMT-CLASS" : "FMT-SIZE", A->getBeginLoc(),
             "%" + len + cv + " got " + ty(A->getType()) + " " + std::to_string(asz) + "B\t" + callee + "\t" + fn());
    }
  }

  bool inSystem(const Decl *D) {
    std::string p;
    unsigned l, c;
    if (where(D->getLocation(), p, l, c)) return false;  // in the tree
    std::string file = SM.getFilename(SM.getExpansionLoc(D->getLocation())).str();
    return file.find("/shim/include/") == std::string::npos && file.find("/platform/") == std::string::npos;
  }

  static bool wideFn(StringRef n) {
    return n.starts_with("wcs") || n.starts_with("wmem") || n.starts_with("_wcs") || n.starts_with("swprintf") ||
           n.starts_with("vswprintf") || n.starts_with("wprintf") || n.starts_with("fwprintf") || n == "mbstowcs" ||
           n == "wcstombs" || n == "mbtowc" || n == "wctomb" || n == "mbrtowc" || n == "wcrtomb" ||
           n.starts_with("tow") || n.starts_with("isw") || n.starts_with("_wto") || n.starts_with("_itow") ||
           n.starts_with("_wfopen") || n == "btowc" || n == "wctob";
  }

  // "field Class::member" / "var name" of an lvalue, as ATOMIC-USE prints it
  std::string lvalueKey(const Expr *A) {
    A = A->IgnoreParenImpCasts();
    if (auto *ME = dyn_cast<MemberExpr>(A)) {
      if (auto *FD = dyn_cast<FieldDecl>(ME->getMemberDecl()))
        return "field " + FD->getParent()->getQualifiedNameAsString() + "::" + FD->getNameAsString();
    } else if (auto *DR = dyn_cast<DeclRefExpr>(A)) {
      if (auto *VD = dyn_cast<VarDecl>(DR->getDecl()))
        if (VD->hasGlobalStorage()) return "var " + VD->getQualifiedNameAsString();
    }
    return std::string();
  }
  void plainStore(const Expr *LHS, SourceLocation L, const char *how) {
    if (Atomic.empty()) return;
    std::string k = lvalueKey(LHS);
    if (!k.empty() && Atomic.count(k)) emit("ATOMIC-PLAIN-STORE", L, k + "\t" + how + "\t" + fn());
  }

  void atomicOperand(const Expr *A, const std::string &callee, SourceLocation L) {
    A = A->IgnoreParenImpCasts();
    if (auto *U = dyn_cast<UnaryOperator>(A))
      if (U->getOpcode() == UO_AddrOf) A = U->getSubExpr()->IgnoreParenImpCasts();
    std::string key;
    if (auto *ME = dyn_cast<MemberExpr>(A)) {
      if (auto *FD = dyn_cast<FieldDecl>(ME->getMemberDecl())) {
        key = "field " + FD->getParent()->getQualifiedNameAsString() + "::" + FD->getNameAsString();
        const RecordDecl *RD = FD->getParent();
        if (!RD->isInvalidDecl() && RD->isCompleteDefinition() && !RD->isDependentType()) {
          const ASTRecordLayout &RL = C.getASTRecordLayout(RD);
          uint64_t off = RL.getFieldOffset(FD->getFieldIndex()) / 8;
          if (off % 4 || RD->hasAttr<MaxFieldAlignmentAttr>() || RD->hasAttr<PackedAttr>())
            emit("ATOMIC-UNALIGNED", L, key + " +" + std::to_string(off) + "\t" + callee + "\t" + fn());
        }
      }
    } else if (auto *DR = dyn_cast<DeclRefExpr>(A)) {
      if (auto *VD = dyn_cast<VarDecl>(DR->getDecl()))
        if (VD->hasGlobalStorage()) key = "var " + VD->getQualifiedNameAsString();
    }
    if (!key.empty()) emit("ATOMIC-USE", L, key + "\t" + callee + "\t" + fn());
  }

  bool VisitCallExpr(CallExpr *CE) {
    if (NoAst) return true;
    const FunctionDecl *FD = CE->getDirectCallee();
    std::string callee = FD ? FD->getQualifiedNameAsString() : "<indirect>";
    // argument order: MSVC x86 evaluates (pushes) arguments right to left, GCC AArch64 left to right
    std::vector<const Expr *> Args;
    unsigned first = 0;
    if (auto *OC = dyn_cast<CXXOperatorCallExpr>(CE)) {
      (void)OC;
      for (unsigned i = 0; i < CE->getNumArgs(); i++) Args.push_back(CE->getArg(i));
      if (Args.size() == 2) orderArgs("ORDER-OPERATOR", CE->getBeginLoc(), Args, callee, CE->getSourceRange());
      Args.clear();
    } else {
      for (unsigned i = first; i < CE->getNumArgs(); i++)
        if (!isa<CXXDefaultArgExpr>(CE->getArg(i))) Args.push_back(CE->getArg(i));
      orderArgs("ORDER-ARGS", CE->getBeginLoc(), Args, callee, CE->getSourceRange());
      if (auto *MC = dyn_cast<CXXMemberCallExpr>(CE)) {
        // C++17 (GCC): the object expression before the arguments; MSVC x86: arguments first, ecx last
        Effects O, A;
        effects(MC->getImplicitObjectArgument(), O);
        for (auto *a : Args) effects(a, A);
        if (O.n && A.n)
          emit("ORDER-OBJECT", CE->getBeginLoc(), O.first + " | " + A.first + "\t" + fn() + "\t" +
                                                      text(CE->getSourceRange(), 100));
      }
    }
    if (!FD) return true;
    StringRef name = idName(FD);
    // varargs: aggregates and long double through '...', printf-style formats
    if (FD->isVariadic()) {
      for (unsigned i = FD->getNumParams(); i < CE->getNumArgs(); i++) {
        QualType AT = CE->getArg(i)->getType().getCanonicalType();
        if (AT->isRecordType())
          emit("VA-STRUCT", CE->getArg(i)->getBeginLoc(),
               ty(CE->getArg(i)->getType()) + " " + (AT->isIncompleteType() ? "?" : std::to_string(C.getTypeSize(AT) / 8)) +
                   "B\t" + callee + "\t" + fn());
        else if (AT->isSpecificBuiltinType(BuiltinType::LongDouble))
          emit("VA-LDBL", CE->getArg(i)->getBeginLoc(), callee + "\t" + fn());
      }
      unsigned fi;
      if (printfLike(FD, fi)) checkFormat(CE, FD, fi, callee);
    } else if ((name.contains("printf") || name.contains("scanf")) && FD->getNumParams() >= 2) {
      for (unsigned i = 0; i < FD->getNumParams() && i < CE->getNumArgs(); i++) {
        QualType P = FD->getParamDecl(i)->getType();
        if (P->isPointerType() && P->getPointeeType()->isCharType() &&
            isa<StringLiteral>(CE->getArg(i)->IgnoreParenImpCasts())) {
          std::string s = cast<StringLiteral>(CE->getArg(i)->IgnoreParenImpCasts())->getString().str();
          if (s.find("%I") != std::string::npos || s.find("%S") != std::string::npos)
            emit("FMT-MSVC", CE->getBeginLoc(), "va_list\t" + callee + "\t" + fn());
        }
      }
    }
    if (name.starts_with("Interlocked") || name.starts_with("_Interlocked")) {
      if (CE->getNumArgs() >= 1) atomicOperand(CE->getArg(0), name.str(), CE->getBeginLoc());
    }
    if (name == "_ReadWriteBarrier" || name == "_ReadBarrier" || name == "_WriteBarrier" || name == "MemoryBarrier" ||
        name == "_mm_mfence" || name == "_mm_sfence" || name == "_mm_lfence")
      emit("BARRIER", CE->getBeginLoc(), name.str() + "\t" + fn());
    if (wideFn(name) && inSystem(FD)) emit("WCHAR-LIB", CE->getBeginLoc(), name.str() + "\t" + fn());
    // FLOAT-BITS: SSE sign masks and float->integer bit casts of arithmetic results (a compare result is a mask: same
    // on both machines)
    if ((name == "_mm_movemask_ps" || name == "_mm_movemask_pd" || name == "_mm_castps_si128" ||
         name == "_mm_castpd_si128") && CE->getNumArgs() == 1) {
      const Expr *A = CE->getArg(0)->IgnoreParenImpCasts();
      const CallExpr *AC = dyn_cast<CallExpr>(A);
      StringRef an = AC && AC->getDirectCallee() ? idName(AC->getDirectCallee()) : StringRef();
      if (!an.starts_with("_mm_cmp"))
        emit("FLOAT-BITS", CE->getBeginLoc(), "sse " + name.str() + "\t" + (an.empty() ? "-" : an.str()) + "\t" + fn() +
                                                 "\t" + text(CE->getSourceRange()));
    }
    if ((name.ends_with("l") && (name == "sqrtl" || name == "powl" || name == "fabsl" || name == "sinl" ||
                                  name == "cosl" || name == "expl" || name == "logl" || name == "floorl")))
      emit("LDBL", CE->getBeginLoc(), name.str() + "\t" + fn());
    return true;
  }

  bool VisitCXXConstructExpr(CXXConstructExpr *E) {
    if (NoAst || E->getNumArgs() < 2) return true;
    std::vector<const Expr *> Args;
    for (unsigned i = 0; i < E->getNumArgs(); i++)
      if (!isa<CXXDefaultArgExpr>(E->getArg(i))) Args.push_back(E->getArg(i));
    orderArgs("ORDER-ARGS", E->getBeginLoc(), Args, E->getConstructor()->getQualifiedNameAsString(),
              E->getSourceRange());
    return true;
  }

  // address of a named parameter in a variadic function: the x86 trick of walking the stack past it
  bool VisitUnaryOperator(UnaryOperator *U) {
    if (U->isIncrementDecrementOp()) plainStore(U->getSubExpr(), U->getBeginLoc(), "incdec");
    if (NoAst || U->getOpcode() != UO_AddrOf || !CurFn || !CurFn->isVariadic()) return true;
    if (auto *DR = dyn_cast<DeclRefExpr>(U->getSubExpr()->IgnoreParenImpCasts()))
      if (isa<ParmVarDecl>(DR->getDecl())) emit("VA-ADDR-PARAM", U->getBeginLoc(), fn() + "\t" + text(U->getSourceRange()));
    return true;
  }

  bool VisitFloatingLiteral(FloatingLiteral *F) {
    if (!NoAst && F->getType()->isSpecificBuiltinType(BuiltinType::LongDouble)) emit("LDBL", F->getBeginLoc(), "literal\t" + fn());
    return true;
  }

  // ------------------------------------------------------------------ volatile, polling
  bool isVolatileRead(const Stmt *S) {
    if (!S) return false;
    if (auto *IC = dyn_cast<ImplicitCastExpr>(S))
      if (IC->getCastKind() == CK_LValueToRValue && IC->getSubExpr()->getType().isVolatileQualified()) return true;
    for (const Stmt *Ch : S->children())
      if (isVolatileRead(Ch)) return true;
    return false;
  }
  // a read of shared memory (member through a pointer / this, or a global), not a local
  bool readsShared(const Stmt *S, std::string &what) {
    if (!S) return false;
    if (auto *IC = dyn_cast<ImplicitCastExpr>(S)) {
      if (IC->getCastKind() == CK_LValueToRValue) {
        const Expr *L = IC->getSubExpr()->IgnoreParens();
        if (auto *ME = dyn_cast<MemberExpr>(L)) {
          what = ME->getMemberDecl()->getNameAsString();
          return true;
        }
        if (auto *DR = dyn_cast<DeclRefExpr>(L))
          if (auto *VD = dyn_cast<VarDecl>(DR->getDecl()))
            if (VD->hasGlobalStorage() && !VD->getType().isConstQualified()) {
              what = VD->getNameAsString();
              return true;
            }
      }
    }
    if (isa<CallExpr>(S)) return false;  // the call's result, not a polled location
    for (const Stmt *Ch : S->children())
      if (readsShared(Ch, what)) return true;
    return false;
  }
  // loop body: only waiting (empty, Sleep/SwitchToThread/YieldProcessor/_mm_pause/Wait* calls)
  bool waitBody(const Stmt *S, bool &sawWait, bool &other) {
    if (!S) return true;
    if (auto *CE = dyn_cast<CallExpr>(S)) {
      if (auto *FD = CE->getDirectCallee()) {
        StringRef n = idName(FD);
        if (n == "Sleep" || n == "SleepEx" || n == "SwitchToThread" || n == "YieldProcessor" || n == "_mm_pause" ||
            n.starts_with("WaitFor") || n == "fnvnx_yield")
          sawWait = true;
        else
          other = true;
      } else {
        other = true;
      }
    }
    if (auto *BO = dyn_cast<BinaryOperator>(S))
      if (BO->isAssignmentOp()) other = true;
    if (auto *UO = dyn_cast<UnaryOperator>(S))
      if (UO->isIncrementDecrementOp()) other = true;
    for (const Stmt *Ch : S->children()) waitBody(Ch, sawWait, other);
    return true;
  }
  void loop(const Stmt *L, const Expr *Cond, const Stmt *Body) {
    if (NoAst || !Cond) return;
    {
      bool sw = false, oth = false;
      waitBody(Cond, sw, oth);    // ++ / assignment / calls in the condition: a scan, not a poll
      if (oth) return;
    }
    if (isVolatileRead(Cond)) {
      emit("POLL-VOLATILE", L->getBeginLoc(), fn() + "\t" + text(Cond->getSourceRange()));
      return;
    }
    std::string what;
    if (!readsShared(Cond, what)) return;
    bool sawWait = false, other = false;
    waitBody(Body, sawWait, other);
    if (!other) emit("POLL-PLAIN", L->getBeginLoc(), what + (sawWait ? " wait" : " spin") + "\t" + fn() + "\t" +
                                                         text(Cond->getSourceRange()));
  }
  bool VisitWhileStmt(WhileStmt *S) { loop(S, S->getCond(), S->getBody()); return true; }
  bool VisitDoStmt(DoStmt *S) { loop(S, S->getCond(), S->getBody()); return true; }
  bool VisitForStmt(ForStmt *S) {
    if (!S->getInc()) loop(S, S->getCond(), S->getBody());
    return true;
  }

  // ------------------------------------------------------------------ declarations
  // a trivially-default-initialized local: no initializer, or a trivial default constructor
  bool uninitLocal(const VarDecl *D) {
    if (!D->isLocalVarDecl() || D->isStaticLocal() || D->getType()->isReferenceType() ||
        D->getType()->isDependentType() || D->isInvalidDecl())
      return false;
    const Expr *I = D->getInit();
    if (!I) return true;
    if (auto *CE = dyn_cast<CXXConstructExpr>(I->IgnoreImplicit()))
      return CE->getNumArgs() == 0 && CE->getConstructor()->isTrivial();
    return false;
  }

  // OUTARG-UNINIT: the first use of an uninitialized local hands its address to a call (`T x; f(&x); use(x);`, a
  // buffer passed by decay, a member call on it). Not a port hazard - the callee writes it - but the old fuzz harness
  // runs callees as stubs that do not write through pointer arguments (DIFFFUZZ.md, Limits): a divergence of such a
  // function that reads `x` is the harness's (S13 uninit-* causes).
  bool VisitDeclRefExpr(DeclRefExpr *DR) {
    if (NoAst || Uninit.empty()) return true;
    auto *VD = dyn_cast<VarDecl>(DR->getDecl());
    if (!VD || !Uninit.count(VD)) return true;
    Uninit.erase(VD);
    const Expr *Cur = DR;
    bool addr = false;
    for (int depth = 0; depth < 6; depth++) {
      auto Ps = C.getParents(*Cur);
      if (Ps.empty()) return true;
      const Expr *P = Ps[0].get<Expr>();
      if (!P) return true;
      if (isa<UnaryExprOrTypeTraitExpr>(P) || isa<CXXTypeidExpr>(P)) {
        Uninit.insert(VD);   // sizeof(x): not a use
        return true;
      }
      if (isa<ParenExpr>(P)) { Cur = P; continue; }
      if (auto *IC = dyn_cast<ImplicitCastExpr>(P)) {
        if (IC->getCastKind() == CK_ArrayToPointerDecay) addr = true;
        else if (IC->getCastKind() == CK_LValueToRValue) return true;   // a read: -Wuninitialized's business
        Cur = P;
        continue;
      }
      if (auto *UO = dyn_cast<UnaryOperator>(P)) {
        if (UO->getOpcode() != UO_AddrOf) return true;
        addr = true;
        Cur = P;
        continue;
      }
      if (isa<ExplicitCastExpr>(P) && addr) { Cur = P; continue; }
      if (auto *ME = dyn_cast<MemberExpr>(P)) {
        // x.f(...) on the uninitialized object: the member function fills it
        auto PPs = C.getParents(*ME);
        const CXXMemberCallExpr *MC = PPs.empty() ? nullptr : PPs[0].get<CXXMemberCallExpr>();
        if (MC && MC->getCallee()->IgnoreParens() == ME && MC->getMethodDecl() && !MC->getMethodDecl()->isConst())
          emit("OUTARG-UNINIT", DR->getLocation(), VD->getNameAsString() + "\tthis of " +
                                                     MC->getMethodDecl()->getNameAsString() + "\t" + fn());
        return true;
      }
      if (auto *CE = dyn_cast<CallExpr>(P)) {
        if (CE->getCallee()->IgnoreParenImpCasts() == Cur) return true;
        const FunctionDecl *FD = CE->getDirectCallee();
        if (!addr) {
          // bound to a non-const reference parameter: `int lo; GetRange(lo, hi);`
          if (!FD || isa<CXXOperatorCallExpr>(CE)) return true;
          unsigned i = 0;
          while (i < CE->getNumArgs() && CE->getArg(i) != Cur) i++;
          if (i >= CE->getNumArgs() || i >= FD->getNumParams()) return true;
          QualType PT = FD->getParamDecl(i)->getType();
          if (!PT->isLValueReferenceType() || PT->getPointeeType().isConstQualified()) return true;
          emit("OUTARG-UNINIT", DR->getLocation(), VD->getNameAsString() + "\tref of " + FD->getQualifiedNameAsString() +
                                                     "\t" + fn());
          return true;
        }
        emit("OUTARG-UNINIT", DR->getLocation(), VD->getNameAsString() + "\targ of " +
                                                   (FD ? FD->getQualifiedNameAsString() : std::string("<indirect>")) +
                                                   "\t" + fn());
        return true;
      }
      if (isa<CXXConstructExpr>(P) && addr) {
        emit("OUTARG-UNINIT", DR->getLocation(), VD->getNameAsString() + "\targ of constructor\t" + fn());
        return true;
      }
      return true;
    }
    return true;
  }

  bool VisitVarDecl(VarDecl *D) {
    if (!NoAst && CurFn && uninitLocal(D)) Uninit.insert(D);
    if (NoAst || isa<ParmVarDecl>(D)) return true;
    if (D->getTLSKind() != VarDecl::TLS_None)
      emit("TLS", D->getLocation(), D->getQualifiedNameAsString() + "\t" + ty(D->getType()) + "\t" +
                                        (D->isThisDeclarationADefinition() == VarDecl::DeclarationOnly ? "extern" : "def") +
                                        (D->hasInit() ? " init" : ""));
    if (D->hasGlobalStorage() && D->getType().isVolatileQualified())
      emit("VOLATILE-DECL", D->getLocation(), "var " + D->getQualifiedNameAsString() + "\t" + ty(D->getType()));
    if (D->getType()->isSpecificBuiltinType(BuiltinType::LongDouble)) emit("LDBL", D->getLocation(), "var\t" + fn());
    return true;
  }

  bool VisitFieldDecl(FieldDecl *D) {
    if (NoAst) return true;
    QualType T = D->getType();
    if (T.isVolatileQualified() || (T->isArrayType() && C.getBaseElementType(T).isVolatileQualified()))
      emit("VOLATILE-DECL", D->getLocation(),
           "field " + D->getParent()->getQualifiedNameAsString() + "::" + D->getNameAsString() + "\t" + ty(T));
    if (T->isMemberPointerType())
      emit("MEMPTR-FIELD", D->getLocation(), D->getParent()->getQualifiedNameAsString() + "::" + D->getNameAsString() +
                                                 "\t" + (T->isMemberFunctionPointerType() ? "function" : "data"));
    if (T->isSpecificBuiltinType(BuiltinType::LongDouble)) emit("LDBL", D->getLocation(), "field\t-");
    if (D->isBitField() && !D->isUnnamedBitfield()) {
      if (auto *ET = T->getAs<EnumType>()) {
        const EnumDecl *ED = ET->getDecl()->getDefinition();
        if (ED && !ED->isFixed() && !D->getBitWidth()->isValueDependent()) {
          unsigned w = D->getBitWidthValue(C);
          bool neg = false;
          llvm::APSInt mx(64, false);
          mx = 0;
          for (auto *E : ED->enumerators()) {
            llvm::APSInt v = E->getInitVal();
            if (v.isSigned() && v.isNegative()) neg = true;
            else if (v.getZExtValue() > mx.getZExtValue()) mx = llvm::APSInt(llvm::APInt(64, v.getZExtValue()), true);
          }
          // MSVC: the enum is int, so the bit-field is signed - a value with the top bit of the field set reads back
          // negative; GCC: unsigned underlying type, reads back the value
          if (!neg && w < 64 && mx.getZExtValue() >= (1ull << (w - 1)))
            emit("ENUM-BITFIELD", D->getLocation(), D->getParent()->getQualifiedNameAsString() + "::" +
                                                        D->getNameAsString() + "\t" + ED->getNameAsString() + " max " +
                                                        std::to_string(mx.getZExtValue()) + " in " + std::to_string(w) +
                                                        " bits");
        }
      }
    }
    return true;
  }

  bool VisitEnumDecl(EnumDecl *D) {
    if (NoAst || !D->isThisDeclarationADefinition() || D->isFixed() || D->isDependentType()) return true;
    bool big = false, huge = false;
    for (auto *E : D->enumerators()) {
      llvm::APSInt v = E->getInitVal();
      if (v.isSigned() && v.isNegative()) {
        if (v.getSExtValue() < INT32_MIN) huge = true;
      } else {
        uint64_t u = v.getZExtValue();
        if (u > 0xffffffffull) huge = true;
        else if (u > 0x7fffffffull) big = true;
      }
    }
    // MSVC: every unscoped enum is int (0x80000000 is INT_MIN); GCC: unsigned int, or 64-bit past 32 bits
    std::string name = D->getIdentifier() ? D->getQualifiedNameAsString()
                                          : std::string("(anonymous enum ") +
                                                (D->enumerator_begin() != D->enumerator_end()
                                                     ? D->enumerator_begin()->getNameAsString() : std::string()) + ")";
    if (huge) emit("ENUM-64", D->getLocation(), name);
    else if (big) emit("ENUM-UNSIGNED", D->getLocation(), name);
    return true;
  }

  bool VisitCXXRecordDecl(CXXRecordDecl *D) {
    if (NoAst || !D->isThisDeclarationADefinition() || D->isDependentType() || D->isInvalidDecl()) return true;
    // MSVC gives every empty base after the first its own byte; Itanium overlaps them all at offset 0
    int empty = 0, nonEmptyBefore = 0;
    for (auto &B : D->bases()) {
      auto *BD = B.getType()->getAsCXXRecordDecl();
      if (!BD || !BD->hasDefinition()) continue;
      if (BD->isEmpty()) {
        if (empty || nonEmptyBefore) emit("EMPTY-BASE", D->getLocation(), D->getQualifiedNameAsString() + "\t" + ty(B.getType()));
        empty++;
      } else {
        nonEmptyBefore++;
      }
    }
    if (D->getNumVBases()) emit("VBASE", D->getLocation(), D->getQualifiedNameAsString());
    return true;
  }

  // ------------------------------------------------------------------ layouts
  bool VisitRecordDecl(RecordDecl *D) {
    if (!DoLayout || !D->isCompleteDefinition() || D->isDependentType() || D->isInvalidDecl()) return true;
    if (auto *CD = dyn_cast<CXXRecordDecl>(D))
      if (CD->getDescribedClassTemplate()) return true;
    std::string p;
    unsigned l, c;
    if (!where(D->getLocation(), p, l, c)) return true;
    PrintingPolicy PP(C.getLangOpts());
    PP.AnonymousTagLocations = false;
    PP.SuppressTagKeyword = true;
    std::string key = p + ":" + std::to_string(l) + ":" + std::to_string(c) + "|" +
                      C.getRecordType(D).getAsString(PP);
    if (SeenKeys.count(key)) return true;
    SeenKeys.insert(key);
    const ASTRecordLayout &RL = C.getASTRecordLayout(D);
    std::string fields, bases, flags;
    bool anyBf = false;
    for (auto *F : D->fields()) {
      uint64_t off = RL.getFieldOffset(F->getFieldIndex());
      QualType FT = F->getType();
      uint64_t fsz = (FT->isIncompleteType() || FT->isDependentType()) ? 0 : C.getTypeSizeInChars(FT).getQuantity();
      fields += F->getNameAsString() + ":" + std::to_string(off) + ":" + std::to_string(fsz);
      if (F->isBitField()) {
        fields += ":" + std::to_string(F->getBitWidthValue(C)) + ":" + ty(F->getType());
        anyBf = true;
      }
      fields += ";";
    }
    if (auto *CD = dyn_cast<CXXRecordDecl>(D)) {
      for (auto &B : CD->bases()) {
        auto *BD = B.getType()->getAsCXXRecordDecl();
        if (!BD) continue;
        if (B.isVirtual()) {
          bases += "v:" + ty(B.getType()) + ";";
          flags += "vbase ";
        } else {
          bases += ty(B.getType()) + ":" + std::to_string(RL.getBaseClassOffset(BD).getQuantity()) + ":" +
                   std::to_string(BD->isInvalidDecl() ? 0 : C.getASTRecordLayout(BD).getSize().getQuantity()) +
                   (BD->isEmpty() ? ":empty" : "") + ";";
        }
      }
      if (CD->isDynamicClass()) flags += "dynamic ";
    }
    if (anyBf) flags += "bitfield ";
    if (D->hasAttr<MaxFieldAlignmentAttr>()) flags += "pack" + std::to_string(D->getAttr<MaxFieldAlignmentAttr>()->getAlignment() / 8) + " ";
    if (D->hasAttr<PackedAttr>()) flags += "packed ";
    llvm::outs() << "L\t" << key << '\t' << RL.getSize().getQuantity() << '\t' << RL.getAlignment().getQuantity() << '\t'
                 << RL.getDataSize().getQuantity() << '\t' << fields << '\t' << bases << '\t' << flags << '\n';
    return true;
  }
};

struct Consumer : ASTConsumer {
  void HandleTranslationUnit(ASTContext &C) override {
    Visitor V(C);
    V.TraverseDecl(C.getTranslationUnitDecl());
  }
};

struct Action : ASTFrontendAction {
  std::unique_ptr<ASTConsumer> CreateASTConsumer(CompilerInstance &, StringRef) override {
    return std::make_unique<Consumer>();
  }
};

}  // namespace

int main(int argc, const char **argv) {
  auto Opt = tooling::CommonOptionsParser::create(argc, argv, Cat);
  if (!Opt) {
    llvm::errs() << Opt.takeError();
    return 2;
  }
  if (OnlyAtomic) NoAst = true;
  if (!AtomicKeys.empty()) {
    std::ifstream in(AtomicKeys);
    std::string line;
    while (std::getline(in, line))
      if (!line.empty()) Atomic.insert(line);
  }
  if (!Seen.empty()) {
    std::ifstream in(Seen);
    std::string line;
    while (std::getline(in, line)) SeenKeys.insert(line);
  }
  tooling::ClangTool Tool(Opt->getCompilations(), Opt->getSourcePathList());
  int rc = Tool.run(tooling::newFrontendActionFactory<Action>().get());
  llvm::outs().flush();
  return rc == 0 ? 0 : 1;  // 1: the unit had errors (findings were still printed)
}
