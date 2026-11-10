// Clang plugin (MSVC ABI targets only). Plugin arguments:
//   out=<file>      per dynamic class: Itanium type string, vfptr offset, MSVC
//                   vftable symbol, virtual base count (one line per table).
//   sympair=<file>  per external function/variable defined or referenced:
//                   itanium \t msvc \t func|ctor|dtor|data \t def|ref
//   members=<file>  per member function declared by a complete class:
//                   itanium type \t itanium symbol (C1/D1) \t declared|implicit
// Every name comes from a clang mangler call, never from text editing.
// Usage: -fplugin=fnvnx_vtnames.so -fplugin-arg-fnvnx_vtnames-sympair=<file>
#include "clang/AST/ASTConsumer.h"
#include "clang/AST/ASTContext.h"
#include "clang/AST/DeclCXX.h"
#include "clang/AST/DeclTemplate.h"
#include "clang/AST/ExprCXX.h"
#include "clang/AST/GlobalDecl.h"
#include "clang/AST/Mangle.h"
#include "clang/AST/RecursiveASTVisitor.h"
#include "clang/AST/VTableBuilder.h"
#include "clang/Frontend/CompilerInstance.h"
#include "clang/Frontend/FrontendPluginRegistry.h"
#include "llvm/Support/raw_ostream.h"
#include <map>
#include <memory>
#include <set>
#include <string>
#include <system_error>
#include <tuple>

using namespace clang;

namespace {
using Row = std::tuple<std::string, int64_t, std::string, unsigned>;
using Key3 = std::tuple<std::string, std::string, std::string>;

class Visitor : public RecursiveASTVisitor<Visitor> {
public:
  explicit Visitor(ASTContext &C)
      : Ctx(C), Diags(C.getDiagnostics()),
        Itanium(ItaniumMangleContext::create(C, C.getDiagnostics())),
        MS(cast<MicrosoftMangleContext>(C.createMangleContext())),
        VTC(cast<MicrosoftVTableContext>(C.getVTableContext())) {}
  bool shouldVisitTemplateInstantiations() const { return true; }
  bool shouldVisitImplicitCode() const { return true; }

  bool VisitCXXRecordDecl(CXXRecordDecl *RD) {
    if (!RD->isCompleteDefinition() || RD->isDependentContext() ||
        RD->isInvalidDecl())
      return true;
    std::string Ity = typeName(RD);
    if (Ity.empty())
      return fail(RD->getLocation(), RD, "empty Itanium name");
    emitMembers(RD, Ity);
    if (!RD->isDynamicClass())
      return true;
    for (const std::unique_ptr<VPtrInfo> &VP : VTC->getVFPtrOffsets(RD)) {
      std::string Sym;
      llvm::raw_string_ostream SO(Sym);
      MS->mangleCXXVFTable(RD, VP->MangledPath, SO);
      SO.flush();
      if (Sym.compare(0, 4, "??_7") != 0)
        return fail(RD->getLocation(), RD, "unexpected vftable symbol " + Sym);
      Rows.insert(Row(Ity, VP->FullOffsetInMDC.getQuantity(), Sym,
                      RD->getNumVBases()));
    }
    return true;
  }

  // Template patterns are skipped; only their instantiations are walked.
  bool TraverseFunctionTemplateDecl(FunctionTemplateDecl *D) {
    for (FunctionDecl *S : D->specializations())
      if (!TraverseDecl(S))
        return false;
    return true;
  }
  bool TraverseClassTemplateDecl(ClassTemplateDecl *D) {
    for (ClassTemplateSpecializationDecl *S : D->specializations())
      if (!TraverseDecl(S))
        return false;
    return true;
  }
  bool TraverseVarTemplateDecl(VarTemplateDecl *D) {
    for (VarTemplateSpecializationDecl *S : D->specializations())
      if (!TraverseDecl(S))
        return false;
    return true;
  }
  bool TraverseClassTemplatePartialSpecializationDecl(
      ClassTemplatePartialSpecializationDecl *) {
    return true;
  }
  bool TraverseVarTemplatePartialSpecializationDecl(
      VarTemplatePartialSpecializationDecl *) {
    return true;
  }

  // Definitions.
  bool VisitFunctionDecl(FunctionDecl *FD) {
    if (FD->doesThisDeclarationHaveABody())
      sym(FD, true, FD->getLocation());
    return true;
  }
  bool VisitVarDecl(VarDecl *VD) {
    if (VD->isThisDeclarationADefinition() == VarDecl::Definition)
      sym(VD, true, VD->getLocation());
    return true;
  }
  // References (odr-uses). Implicit destructor calls (locals, members, bases)
  // are not found; destructors of temporaries, delete-expressions and explicit
  // destructor calls are.
  bool VisitDeclRefExpr(DeclRefExpr *E) {
    return use(E->getDecl(), E->getBeginLoc(), E);
  }
  bool VisitMemberExpr(MemberExpr *E) {
    return use(E->getMemberDecl(), E->getBeginLoc(), E);
  }
  bool VisitCXXConstructExpr(CXXConstructExpr *E) {
    return use(E->getConstructor(), E->getBeginLoc(), E);
  }
  bool VisitCXXNewExpr(CXXNewExpr *E) {
    use(E->getOperatorNew(), E->getBeginLoc(), E);
    return use(E->getOperatorDelete(), E->getBeginLoc(), E);
  }
  bool VisitCXXDeleteExpr(CXXDeleteExpr *E) {
    use(E->getOperatorDelete(), E->getBeginLoc(), E);
    QualType T = E->getDestroyedType();
    if (const CXXRecordDecl *RD = T.isNull() ? nullptr : T->getAsCXXRecordDecl())
      if (RD->hasDefinition() && !RD->isDependentContext())
        use(RD->getDestructor(), E->getBeginLoc(), E);
    return true;
  }
  bool VisitCXXBindTemporaryExpr(CXXBindTemporaryExpr *E) {
    return use(E->getTemporary()->getDestructor(), E->getBeginLoc(), E);
  }

  std::set<Row> Rows;
  std::map<Key3, bool> Syms; // (itanium, msvc, kind) -> defined in this unit
  std::set<Key3> Members;    // (class, member, declared|implicit)
  bool Failed = false;

private:
  bool fail(SourceLocation Loc, const NamedDecl *ND, const std::string &Msg) {
    Failed = true;
    unsigned ID = Diags.getCustomDiagID(DiagnosticsEngine::Error,
                                        "fnvnx_vtnames: %0: %1");
    Diags.Report(Loc, ID) << (ND ? ND->getQualifiedNameAsString() : "") << Msg;
    return true;
  }
  std::string typeName(const CXXRecordDecl *RD) {
    std::string S;
    llvm::raw_string_ostream O(S);
    Itanium->mangleCanonicalTypeName(Ctx.getRecordType(RD), O);
    O.flush();
    if (S.compare(0, 4, "_ZTS") == 0)
      S.erase(0, 4);
    return S;
  }
  std::string itaName(GlobalDecl GD) {
    std::string S;
    llvm::raw_string_ostream O(S);
    Itanium->mangleName(GD, O);
    return O.str();
  }
  std::string msName(GlobalDecl GD) {
    std::string S;
    llvm::raw_string_ostream O(S);
    MS->mangleName(GD, O);
    return O.str();
  }
  void add(const NamedDecl *ND, SourceLocation Loc, GlobalDecl GI,
           GlobalDecl GM, const char *Kind, bool Def) {
    std::string I = itaName(GI), M = msName(GM);
    if (I.empty() || M.empty()) {
      fail(Loc, ND, "cannot mangle symbol");
      return;
    }
    bool &D = Syms[Key3(I, M, Kind)];
    D = D || Def;
  }
  bool use(const Decl *D, SourceLocation Loc, const Expr *E) {
    if (!D || E->isInstantiationDependent())
      return true;
    if (auto *FD = dyn_cast<FunctionDecl>(D))
      sym(FD, FD->hasBody(), Loc);
    else if (auto *VD = dyn_cast<VarDecl>(D))
      sym(VD, VD->hasDefinition() == VarDecl::Definition, Loc);
    return true;
  }
  // Documented skips: invalid, dependent, deleted, builtin, templates
  // themselves, deduction guides, internal linkage / anonymous namespace,
  // function-local statics, decomposition declarations.
  void sym(const NamedDecl *ND, bool Def, SourceLocation Loc) {
    if (ND->isInvalidDecl() || ND->getDeclContext()->isDependentContext() ||
        !ND->isExternallyVisible())
      return;
    if (auto *FD = dyn_cast<FunctionDecl>(ND)) {
      if (FD->isDeleted() || FD->getBuiltinID() ||
          FD->getDescribedFunctionTemplate() || isa<CXXDeductionGuideDecl>(FD))
        return;
      if (auto *CD = dyn_cast<CXXConstructorDecl>(FD)) {
        // Itanium C1/C2 are paired with clang's MS mangler answer for the
        // same CXXCtorType (MSVC itself only has one ?0 constructor).
        for (CXXCtorType T : {Ctor_Complete, Ctor_Base})
          add(ND, Loc, GlobalDecl(CD, T), GlobalDecl(CD, T), "ctor", Def);
      } else if (auto *DD = dyn_cast<CXXDestructorDecl>(FD)) {
        // D1/D2/D0 are paired with the MS mangler's Dtor_Complete (?1),
        // Dtor_Base and Dtor_Deleting (?_G); MSVC has no base-object dtor.
        for (CXXDtorType T : {Dtor_Complete, Dtor_Base, Dtor_Deleting})
          add(ND, Loc, GlobalDecl(DD, T), GlobalDecl(DD, T), "dtor", Def);
      } else {
        add(ND, Loc, GlobalDecl(FD), GlobalDecl(FD), "func", Def);
      }
      return;
    }
    auto *VD = cast<VarDecl>(ND);
    if (!VD->hasGlobalStorage() || VD->isLocalVarDeclOrParm() ||
        VD->getDeclContext()->isFunctionOrMethod() ||
        isa<DecompositionDecl>(VD) || VD->getDescribedVarTemplate())
      return;
    add(ND, Loc, GlobalDecl(VD), GlobalDecl(VD), "data", Def);
  }
  void emitMembers(const CXXRecordDecl *RD, const std::string &Ity) {
    for (const CXXMethodDecl *M : RD->methods()) {
      if (M->isInvalidDecl())
        continue;
      std::string N;
      if (auto *C = dyn_cast<CXXConstructorDecl>(M))
        N = itaName(GlobalDecl(C, Ctor_Complete));
      else if (auto *D = dyn_cast<CXXDestructorDecl>(M))
        N = itaName(GlobalDecl(D, Dtor_Complete));
      else
        N = itaName(GlobalDecl(M));
      if (N.empty()) {
        fail(M->getLocation(), M, "cannot mangle member");
        continue;
      }
      Members.insert(Key3(Ity, N, M->isImplicit() ? "implicit" : "declared"));
    }
  }
  ASTContext &Ctx;
  DiagnosticsEngine &Diags;
  std::unique_ptr<ItaniumMangleContext> Itanium;
  std::unique_ptr<MicrosoftMangleContext> MS;
  MicrosoftVTableContext *VTC;
};

struct Opts {
  std::string Out, Sympair, Members;
};

class Consumer : public ASTConsumer {
public:
  explicit Consumer(Opts O) : O(std::move(O)) {}
  void HandleTranslationUnit(ASTContext &Ctx) override {
    DiagnosticsEngine &D = Ctx.getDiagnostics();
    if (!Ctx.getTargetInfo().getCXXABI().isMicrosoft()) {
      D.Report(D.getCustomDiagID(DiagnosticsEngine::Error,
                                 "fnvnx_vtnames: target is not the Microsoft "
                                 "C++ ABI; no output written"));
      return;
    }
    if (D.hasErrorOccurred())
      return;
    Visitor V(Ctx);
    V.TraverseDecl(Ctx.getTranslationUnitDecl());
    if (V.Failed || D.hasErrorOccurred())
      return;
    std::string Buf, SP, MB;
    llvm::raw_string_ostream OS(Buf), SS(SP), MS(MB);
    for (const Row &R : V.Rows)
      OS << std::get<0>(R) << '\t' << std::get<1>(R) << '\t' << std::get<2>(R)
         << '\t' << std::get<3>(R) << '\n';
    for (const auto &S : V.Syms)
      SS << std::get<0>(S.first) << '\t' << std::get<1>(S.first) << '\t'
         << std::get<2>(S.first) << '\t' << (S.second ? "def" : "ref") << '\n';
    for (const Key3 &M : V.Members)
      MS << std::get<0>(M) << '\t' << std::get<1>(M) << '\t' << std::get<2>(M)
         << '\n';
    OS.flush();
    SS.flush();
    MS.flush();
    if (O.Out.empty() && O.Sympair.empty() && O.Members.empty())
      llvm::outs() << Buf;
    if (!O.Out.empty())
      write(D, O.Out, Buf);
    if (!O.Sympair.empty())
      write(D, O.Sympair, SP);
    if (!O.Members.empty())
      write(D, O.Members, MB);
  }

private:
  static void write(DiagnosticsEngine &D, const std::string &Path,
                    const std::string &Data) {
    std::error_code EC;
    llvm::raw_fd_ostream F(Path, EC);
    if (EC) {
      D.Report(D.getCustomDiagID(DiagnosticsEngine::Error,
                                 "fnvnx_vtnames: cannot write %0: %1"))
          << Path << EC.message();
      return;
    }
    F << Data;
  }
  Opts O;
};

class Action : public PluginASTAction {
public:
  std::unique_ptr<ASTConsumer> CreateASTConsumer(CompilerInstance &,
                                                 llvm::StringRef) override {
    return std::make_unique<Consumer>(O);
  }
  bool ParseArgs(const CompilerInstance &CI,
                 const std::vector<std::string> &Args) override {
    DiagnosticsEngine &D = CI.getDiagnostics();
    for (const std::string &A : Args) {
      if (A.compare(0, 4, "out=") == 0) {
        O.Out = A.substr(4);
      } else if (A.compare(0, 8, "sympair=") == 0) {
        O.Sympair = A.substr(8);
      } else if (A.compare(0, 8, "members=") == 0) {
        O.Members = A.substr(8);
      } else {
        D.Report(D.getCustomDiagID(DiagnosticsEngine::Error,
                                   "fnvnx_vtnames: unknown argument '%0'"))
            << A;
        return false;
      }
    }
    return true;
  }
  ActionType getActionType() override { return AddAfterMainAction; }

private:
  Opts O;
};
} // namespace

static FrontendPluginRegistry::Add<Action>
    X("fnvnx_vtnames", "print MSVC vftable names per Itanium type string");
