// fnv-nx native renderer, N1: self-test of nx::Renderer / nx::StateTracker on any backend (host GL, Unicorn guest,
// console). Returns the number of failed checks; *checks = checks run; log gets one line per failure.
#ifndef FNVNX_N1_SELFTEST_H
#define FNVNX_N1_SELFTEST_H
#include "renderer.h"
int n1_selftest(nx::Backend* be, char* log, uint32_t loglen, int* checks);
// The engine-path world frame (selftest_world.cpp); appends to log at *at, adds to *checks, returns failures. Also
// writes one info line with the frame's DrawStats.
int n1_selftest_world(nx::Backend* be, nx::Renderer& r, uint32_t back, char* log, uint32_t cap, uint32_t* at, int* checks);
#endif
