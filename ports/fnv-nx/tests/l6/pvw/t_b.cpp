// part b: every return-code path and every fixme id (cases_b.txt, expectations from the python model), plus the argument rules P1 / P2 of pvw_api.cpp
#include "t_common.h"
void t_b() { t_part('b'); int n = t_run_file("cases_b.txt"); printf("b: %d cases\n", n); CHECK(n > 0); }
