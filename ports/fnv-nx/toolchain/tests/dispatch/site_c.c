/* DISPATCH1 in a C unit with no declaration of fnvnx_dispatch: the plugin's own .fnvnx.decltype row of the dispatcher's prototype */
int site_call(int (*f)(int), int a) { return f(a); }
