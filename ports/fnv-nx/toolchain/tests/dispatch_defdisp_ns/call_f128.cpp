// an indirect call site with a _Float128 argument: still an error (the site needs the descriptor)
int call_f(int (*f)(_Float128), _Float128 a) { return f(a); }
