// top: callers that read the result as double / float (bound by address to F).
#include <stdio.h>
struct C { float G(); float v; };
struct D { double F(); };
struct E { float F(); };
int main() {
    C c; c.v = 7.0f;
    double d = ((D *)&c)->F();
    float f = ((E *)&c)->F();
    printf("double=%.17g float=%.9g\n", d, (double)f);
    return 0;
}
