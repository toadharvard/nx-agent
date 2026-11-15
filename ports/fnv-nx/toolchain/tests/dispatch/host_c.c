/* HOST1 in a C unit: the declared type of a host-slot table (.fnvnx.decltype is a C unit's section). The slot types are named by a typedef and by a second typedef. */
typedef unsigned long long __attribute__((fnvnx_host)) hslot;
typedef hslot hslot2;
struct Api {
  unsigned size;
  hslot open;
  hslot2 close;
  hslot tab[2];
  const hslot cs;
};
extern const struct Api *host_api;
/* host-ABI calls of two different callee types in one C unit: the compiler-internal marker of the pass pair is no declaration of the unit */
unsigned long long hc1(unsigned long long a) { return ((unsigned long long (*)(unsigned long long))(unsigned long)host_api->open)(a); }
unsigned long long hc2(unsigned long long a, unsigned long long b) { return ((unsigned long long (*)(unsigned long long, unsigned long long))(unsigned long)host_api->close)(a, b); }
