// middle: a decomp-style wrapper that believes the callee returns int; on x86 it passes st0 through untouched.
int __fastcall G(void *p);                 // bound by address to C::G (/ALTERNATENAME in the link)
int __fastcall F(void *p) { return G(p); }
