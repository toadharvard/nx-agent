// HOST1: host-ABI calls (through a slot of type fnvnx_host) are not rewritten; every other indirect call is
typedef unsigned long long __attribute__((fnvnx_host)) hslot;
typedef unsigned long long (*hfn1)(unsigned long long);
typedef unsigned long long (*hfn2)(unsigned long long, unsigned long long);
struct Api {
  unsigned size;
  hslot open;
  hslot close;
  hslot tab[2];
};
extern const Api *host_api;
#define HCALL1(slot, a) ((hfn1)(unsigned long)(host_api->slot))(a)
#define HCALL2(slot, a, b) ((hfn2)(unsigned long)(host_api->slot))(a, b)
unsigned long long h1(unsigned long long a) { return HCALL1(open, a); }
unsigned long long h2(unsigned long long a, unsigned long long b) { return HCALL2(close, a, b) + HCALL1(open, b); }
unsigned long long h3(int i, unsigned long long a) { return ((hfn1)(unsigned long)(host_api->tab[i]))(a); }
unsigned long long h4(unsigned long long a) {   // a loop: the slot is read again or hoisted, never merged
  unsigned long long r = 0;
  for (int i = 0; i < 3; i++) r += HCALL1(open, a + i);
  return r;
}
unsigned long long ord(hfn1 f, unsigned long long a) { return f(a) + HCALL1(open, a); }   // the pointer call is rewritten, the host call is not
unsigned plain(const Api *p) { return p->size; }                                           // the size slot is a plain uint32
