// Clang plugin: for every dynamic class, print the Itanium type string, the
// vfptr offset, the MSVC vftable symbol and the virtual base count, one line
// per vtable address point (MSVC ABI targets only).
// Usage: -fplugin=fnvnx_vtnames.so -fplugin-arg-fnvnx_vtnames-out=<file>
#include "clang/AST/ASTConsumer.h"
#include "clang/AST/ASTContext.h"
#include "clang/AST/Mangle.h"
#include "clang/AST/RecursiveASTVisitor.h"
#include "clang/AST/VTableBuilder.h"
#include "clang/Frontend/CompilerInstance.h"
#include "clang/Frontend/FrontendPluginRegistry.h"
#include "llvm/Support/raw_ostream.h"
#include <memory>
#include <set>
#include <string>
#include <system_error>
#include <tuple>

using namespace clang;

namespace {
using Row = std::tuple<std::string, int64_t, std::string, unsigned>;

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
        RD->isInvalidDecl() || !RD->isDynamicClass())
      return true;
    std::string Ity;
    llvm::raw_string_ostream IO(Ity);
    Itanium->mangleCanonicalTypeName(Ctx.getRecordType(RD), IO);
    IO.flush();
    if (Ity.compare(0, 4, "_ZTS") == 0)
      Ity.erase(0, 4);
    if (Ity.empty())
      return fail(RD, "empty Itanium name");
    for (const std::unique_ptr<VPtrInfo> &VP : VTC->getVFPtrOffsets(RD)) {
      std::string Sym;
      llvm::raw_string_ostream SO(Sym);
      MS->mangleCXXVFTable(RD, VP->MangledPath, SO);
      SO.flush();
      if (Sym.compare(0, 4, "??_7") != 0)
        return fail(RD, "unexpected vftable symbol " + Sym);
      Rows.insert(Row(Ity, VP->FullOffsetInMDC.getQuantity(), Sym,
                      RD->getNumVBases()));
    }
    return true;
  }
  std::set<Row> Rows;
  bool Failed = false;

private:
  bool fail(const CXXRecordDecl *RD, const std::string &Msg) {
    Failed = true;
    unsigned ID = Diags.getCustomDiagID(DiagnosticsEngine::Error,
                                        "fnvnx_vtnames: %0: %1");
    Diags.Report(RD->getLocation(), ID) << RD->getQualifiedNameAsString() << Msg;
    return true;
  }
  ASTContext &Ctx;
  DiagnosticsEngine &Diags;
  std::unique_ptr<ItaniumMangleContext> Itanium;
  std::unique_ptr<MicrosoftMangleContext> MS;
  MicrosoftVTableContext *VTC;
};

class Consumer : public ASTConsumer {
public:
  explicit Consumer(std::string Out) : Out(std::move(Out)) {}
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
    if (V.Failed)
      return;
    std::string Buf;
    llvm::raw_string_ostream OS(Buf);
    for (const Row &R : V.Rows)
      OS << std::get<0>(R) << '\t' << std::get<1>(R) << '\t' << std::get<2>(R)
         << '\t' << std::get<3>(R) << '\n';
    OS.flush();
    if (Out.empty()) {
      llvm::outs() << Buf;
      return;
    }
    std::error_code EC;
    llvm::raw_fd_ostream F(Out, EC);
    if (EC) {
      D.Report(D.getCustomDiagID(DiagnosticsEngine::Error,
                                 "fnvnx_vtnames: cannot write %0: %1"))
          << Out << EC.message();
      return;
    }
    F << Buf;
  }

private:
  std::string Out;
};

class Action : public PluginASTAction {
public:
  std::unique_ptr<ASTConsumer> CreateASTConsumer(CompilerInstance &,
                                                 llvm::StringRef) override {
    return std::make_unique<Consumer>(Out);
  }
  bool ParseArgs(const CompilerInstance &CI,
                 const std::vector<std::string> &Args) override {
    DiagnosticsEngine &D = CI.getDiagnostics();
    for (const std::string &A : Args) {
      if (A.compare(0, 4, "out=") == 0) {
        Out = A.substr(4);
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
  std::string Out;
};
} // namespace

static FrontendPluginRegistry::Add<Action>
    X("fnvnx_vtnames", "print MSVC vftable names per Itanium type string");
