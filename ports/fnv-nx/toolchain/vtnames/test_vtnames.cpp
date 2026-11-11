struct A { int a; virtual int f(); virtual ~A(); virtual int g(); };
struct S2 { int s; virtual ~S2(); virtual int k(); };
struct M : A, S2 { M(); ~M(); int k() override; virtual int last(); };

namespace n { struct B { virtual void h(); }; }
struct Outer { struct Inner { virtual void q(); }; };

template <class T> struct TT { T t; virtual ~TT() {} };
TT<int> x;
template <class T> struct NeverUsed { virtual void nu(); };

struct Plain { int p; void f(); };

struct VB { int v; };
struct WithVBase : virtual VB { virtual void w(); };
