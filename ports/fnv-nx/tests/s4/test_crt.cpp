/* test_crt.cpp - MSVC CRT semantics in platform/crt. */
#include "s4_test.h"

#include <errno.h>
#include <math.h>
#include <stdlib.h>

#include <stdexcept>
#include <typeinfo>

#include "fnvnx_msvcrt.h"

/* helpers called by name from the decomp */