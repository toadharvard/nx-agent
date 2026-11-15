/* patterns that have no symbol (a variadic template, a templated conversion operator, a user-defined literal template), alone and instantiated */
template <class... A> int count(A... a) { return sizeof...(A); }
template <class... A> struct Pack { template <class... B> static int both(B... b) { return sizeof...(A) + sizeof...(B); } };
struct C { template <class T> operator T() const { return T(); } template <class... A> int n(A... a) const { return sizeof...(A); } };
unsigned long long operator""_u(unsigned long long v) { return v; }
template <char... Cs> int operator""_t() { return sizeof...(Cs); }
#ifdef INSTANTIATE
int use() { C c; return count(1, 2.0) + Pack<int, char>::both(1) + c.n(1, 2) + (int)c + (int)(3_u) + 12_t; }
#endif
/* names cl 15.00 has no type for, and operators it has no code for: the later MSVC ABI's names (clang MicrosoftMangle.cpp) */
typedef float v4 __attribute__((vector_size(16)));
typedef int v4i __attribute__((vector_size(16)));
typedef long long v1ll __attribute__((vector_size(8)));
void tv(v4 a, v4i b, v1ll c, char16_t d, char32_t e, decltype(nullptr) f, __int128 g, unsigned __int128 h) {}
