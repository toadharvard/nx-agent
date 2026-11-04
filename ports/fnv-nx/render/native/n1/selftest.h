// fnv-nx native renderer, N1: self-test of nx::Renderer / nx::StateTracker on any backend (host GL, Unicorn guest,
// console). Returns the number of failed checks; *checks = checks run; log gets one line per failure.
#ifndef FNVNX_N1_SELFTEST_H
#define FNVNX_N1_SELFTEST_H
#include "renderer.h"
int n1_selftest(nx::Backend* be, char* log, uint32_t loglen, int* checks);
#endif
