/* fnvnx_crt.h - MSVC 2008 stand-in for shim/include/fnvnx_crt.h in S11 equivalence builds (tools/equiv): the MSVC
 * CRT headers that declare the MSVC-only CRT names the target header declares (DECL1 includes it where it drops the
 * decomp's own prototypes of them; _finite / _isnan are in <float.h>). */
#ifndef FNVNX_EQUIV_CRT_H
#define FNVNX_EQUIV_CRT_H
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <malloc.h>
#include <float.h>
#endif
