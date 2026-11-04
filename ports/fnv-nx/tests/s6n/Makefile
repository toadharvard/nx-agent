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

# ---- N0 scenes on the GL reference backend ------------------------------------------------------------------------
CXX ?= g++
SHOUT := $(OUT)/shaders
SCENE_SRC := $(NATIVE)/n0/scenes.cpp $(NATIVE)/n0/run_gl.cpp $(NATIVE)/front/front.cpp
SCENE_CSRC := $(NATIVE)/front/d3d9_state.c $(NATIVE)/front/gpu_dispatch.c $(NATIVE)/gl/nxgl.c $(SHOUT)/nx_shaders.c
SCENE_DEPS := $(wildcard $(NATIVE)/n0/*.h $(NATIVE)/include/nx/*.h $(NATIVE)/front/*.h $(NATIVE)/gl/*.h)

$(SHOUT)/nx_shaders.c: $(wildcard $(NATIVE)/shaders/*.glsl) $(NATIVE)/shaders/build.py
	python3 $(NATIVE)/shaders/build.py $(SHOUT) $(if $(UAM),--uam $(UAM))

$(OUT)/run_gl: $(SCENE_SRC) $(SCENE_CSRC) $(SCENE_DEPS)
	$(CXX) -std=c++17 -O1 -g -Wall -Wextra -Werror -fno-exceptions -fno-rtti $(CPPFLAGS) -I$(SHOUT) -c $(NATIVE)/front/front.cpp -o $(OUT)/front.o
	$(CXX) -std=c++17 -O1 -g -Wall -Wextra -Werror -fno-exceptions -fno-rtti $(CPPFLAGS) -I$(SHOUT) -c $(NATIVE)/n0/scenes.cpp -o $(OUT)/scenes.o
	$(CXX) -std=c++17 -O1 -g -Wall -Wextra -Werror -fno-exceptions -fno-rtti $(CPPFLAGS) -I$(SHOUT) -c $(NATIVE)/n0/run_gl.cpp -o $(OUT)/run_gl.o
	$(CC) -std=c11 -O1 -g -Wall -Wextra -Werror $(CPPFLAGS) -I$(SHOUT) -c $(NATIVE)/gl/nxgl.c -o $(OUT)/nxgl.o
	$(CC) -std=c11 -O1 -g -Wall -Wextra -Werror $(CPPFLAGS) -c $(NATIVE)/front/gpu_dispatch.c -o $(OUT)/gpu_dispatch.o
	$(CC) -std=c11 -O1 -g -Wall -Wextra $(CPPFLAGS) -c $(NATIVE)/front/d3d9_state.c -o $(OUT)/d3d9_state_s.o
	$(CC) -std=c11 -O1 -I$(SHOUT) -c $(SHOUT)/nx_shaders.c -o $(OUT)/nx_shaders.o
	$(CXX) -o $@ $(OUT)/front.o $(OUT)/scenes.o $(OUT)/run_gl.o $(OUT)/nxgl.o $(OUT)/gpu_dispatch.o $(OUT)/d3d9_state_s.o $(OUT)/nx_shaders.o -lEGL -lm

scenes: $(OUT)/run_gl
	python3 scenes.py --bin $(OUT)/run_gl --out $(OUT)/scenes

.PHONY: scenes

# ---- the ILP32 Switch guest under Unicorn, nxgpu calls forwarded to the GL backend (tests/s6n/emu_n0.py) ----
$(OUT)/libnxgl.so: $(NATIVE)/gl/nxgl.c $(NATIVE)/front/gpu_dispatch.c $(SCENE_DEPS)
	@mkdir -p $(OUT)
	$(CC) -std=c11 -O1 -g -fPIC -shared $(CPPFLAGS) -o $@ $(NATIVE)/gl/nxgl.c $(NATIVE)/front/gpu_dispatch.c -lEGL

# needs: pip install unicorn; GUEST=<switch/build.sh out>/guest/guest.nso
emu: $(OUT)/libnxgl.so
	python3 emu_n0.py $(GUEST) $(OUT)/libnxgl.so $(OUT)/emu
	python3 scenes.py --compare $(OUT)/emu/fnvnx/n0/out

.PHONY: emu
