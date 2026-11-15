/* patterns that have no symbol (a variadic template, a templated conversion operator, a user-defined literal template), alone and instantiated */
template <class... A> int count(A... a) { return sizeof...(A); }
template <class... A> struct Pack { template <class... B> static int both(B... b) { return sizeof...(A) + sizeof...(B); } };
struct C { template <class T> operator T() const { return T(); } template <class... A> int n(A... a) const { return sizeof...(A); } };
unsigned long long operator""_u(unsigned long long v) { return v; }
template <char... Cs> int operator""_t() { return sizeof...(Cs); }
#ifdef INSTANTIATE
int use() { C c; return count(1, 2.0) + Pack<int, char>::both(1) + c.n(1, 2) + (int)c + (int)(3_u) + 12_t; }
#endif
