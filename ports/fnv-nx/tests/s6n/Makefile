# S6n host unit tests:  make -C tests/s6n        (OUT=<dir> for the binaries, default /tmp/fnvnx-s6n-tests)
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
NATIVE := $(ROOT)/render/native
OUT ?= /tmp/fnvnx-s6n-tests
CC ?= gcc
CFLAGS ?= -std=c99 -O2 -Wall -Wextra -Werror -pedantic
CPPFLAGS += -I$(NATIVE)/include -I$(NATIVE)/front

all: $(OUT)/test_d3d9_state $(OUT)/test_d3d9_state_cxxcheck
	$(OUT)/test_d3d9_state

$(OUT)/test_d3d9_state: test_d3d9_state.c $(NATIVE)/front/d3d9_state.c $(NATIVE)/front/d3d9_state.h $(NATIVE)/include/nx/state.h
	@mkdir -p $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -o $@ test_d3d9_state.c $(NATIVE)/front/d3d9_state.c -lm

# The header must also compile as C++ (extern "C").
$(OUT)/test_d3d9_state_cxxcheck: $(NATIVE)/front/d3d9_state.h $(NATIVE)/include/nx/state.h
	@mkdir -p $(OUT)
	echo '#include "d3d9_state.h"' | $(CXX) $(CPPFLAGS) -x c++ -std=c++11 -Wall -Werror -fsyntax-only -
	@touch $@

.PHONY: all
