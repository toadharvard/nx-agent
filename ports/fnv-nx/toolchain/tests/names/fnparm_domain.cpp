// fnparm_domain.cpp - function types whose parameter forms have no recorded cl 15.00 name (names.expect has f8 for plain, array, const-pointer and a
// function-typed parameter of a declaration): the names plugin gives them a typed `nodecor:fnparmform:<form>` fact, not a name. Compiled by test_names.py.
void d1(void (*)(void g(int))) {}                 // a function-typed parameter inside a function type
void d2(void (*)(int *volatile, char)) {}               // a volatile pointer parameter inside a function type
void d3(void (*)(int *const, short)) {}               // a const pointer: f8's form, named
void d4(void (*)(int *, int *)) {}                // plain, named
