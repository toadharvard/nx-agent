int add(int a, int b) { return a + b; }
int callee(int);
extern int ext_global;
int def_global = 3;
int ext_fn_user() { return callee(1) + ext_global + add(1, 2); }

struct S {
  static int sf(int x);
  virtual int vf();
  S();
  ~S();
  int m;
};
int S::sf(int x) { return x; }
int S::vf() { return 1; }
S::S() : m(0) {}
S::~S() {}

struct Impl { int a; virtual void v(); ~Impl(); };
struct Imp2 { int a; };
struct Imp3 { int a; ~Imp3(); };
void user() { Imp2 t; (void)t; Imp3 *p = new Imp3; delete p; }

template <class T> T twice(T t) { return t + t; }
int use_tpl() { return twice<int>(2); }

namespace { int anon_fn() { return 1; } }
static int stat_fn() { return 2; }
int use_hidden() { return anon_fn() + stat_fn(); }

struct Holder { S s; };
void use_holder() { Holder h; (void)h; }
