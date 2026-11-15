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
