// part a: the generated sweep (cases_a.txt: matrices x viewports x source layouts x destination FVFs x lighting / fog / material sources x ranges), every destination byte bit-equal with the python model of Wine's arithmetic
#include "t_common.h"
void t_a() { t_part('a'); int n = t_run_file("cases_a.txt"); printf("a: %d cases\n", n); CHECK(n > 0); }
