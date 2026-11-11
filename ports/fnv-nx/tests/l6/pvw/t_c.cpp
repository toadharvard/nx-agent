// part c: state is unchanged by a call (render states, transforms, viewport, material, lights, stream bindings, declaration, FVF) and a destination that is also a source stream behaves as Wine's in-place loop
#include "t_common.h"
void t_c() { t_part('c'); int n = t_run_file("cases_c.txt"); printf("c: %d cases\n", n); CHECK(n > 0); }
