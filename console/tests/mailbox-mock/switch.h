// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>
#include <string.h>
static void randomGet(void *p,unsigned long n){static uint64_t seed=101;seed++;memcpy(p,&seed,n);}
