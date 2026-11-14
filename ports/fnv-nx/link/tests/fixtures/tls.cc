extern __thread int e_ie __attribute__((tls_model("initial-exec")));
__thread int d_def;
__thread int d_le __attribute__((tls_model("local-exec")));
__thread int d_gd __attribute__((tls_model("global-dynamic")));
__thread int d_ld __attribute__((tls_model("local-dynamic")));
static thread_local int s_tl;
inline int in_f() { static thread_local int in_tl; return ++in_tl; }
int use_in() { return in_f(); }
int use_all() { return e_ie + d_def + d_le + d_gd + d_ld + ++s_tl + use_in(); }
