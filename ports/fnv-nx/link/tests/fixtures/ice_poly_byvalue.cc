// invented fixture: a polymorphic struct passed and returned by value (ICE of the guest compiler with the plugins, expected to build)
struct UVf { virtual void f(); int m; };
UVf f_i(UVf x) { return x; }
