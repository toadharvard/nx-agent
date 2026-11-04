# S6n host unit tests:  make -C tests/s6n        (OUT=<dir> for the binaries, default /tmp/fnvnx-s6n-tests)
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
NATIVE := $(ROOT)/render/native
OUT ?= /tmp/fnvnx-s6n-tests
CC ?= gcc
CFLAGS ?= -std=c99 -O2 -Wall -Wextra -Werror -pedantic
CPPFLAGS += -I$(NATIVE)/include -I$(NATIVE)/front

all: $(OUT)/test_d3d9_state $(OUT)/test_d3d9_state_cxxcheck $(OUT)/test_front $(OUT)/test_harness
	$(OUT)/test_d3d9_state
	$(OUT)/test_front
	$(OUT)/test_harness

$(OUT)/test_harness: test_harness.c $(NATIVE)/front/harness.c $(NATIVE)/front/harness.h $(NATIVE)/gl/nxgl.c $(NATIVE)/front/d3d9_state.c $(NATIVE)/include/nx/gpu_abi.h
	@mkdir -p $(OUT)
	$(CC) -std=c11 -O1 -g -Wall -Wextra -Werror $(CPPFLAGS) -o $@ test_harness.c $(NATIVE)/front/harness.c \
		$(NATIVE)/gl/nxgl.c $(NATIVE)/front/gpu_dispatch.c $(NATIVE)/front/d3d9_state.c -lEGL -lm

$(OUT)/test_front: test_front.cpp $(NATIVE)/front/front.cpp $(NATIVE)/front/d3d9_state.c $(NATIVE)/gl/nxgl.c $(NATIVE)/include/nx/front.h
	@mkdir -p $(OUT)
	$(CC) -std=c11 -O1 -c $(CPPFLAGS) $(NATIVE)/front/d3d9_state.c -o $(OUT)/tf_d3d9_state.o
	$(CC) -std=c11 -O1 -c $(CPPFLAGS) $(NATIVE)/gl/nxgl.c -o $(OUT)/tf_nxgl.o
	$(CC) -std=c11 -O1 -c $(CPPFLAGS) $(NATIVE)/front/gpu_dispatch.c -o $(OUT)/tf_dispatch.o
	$(CXX) -std=c++17 -O1 -Wall -Wextra -Werror -fno-exceptions -fno-rtti $(CPPFLAGS) -o $@ test_front.cpp $(NATIVE)/front/front.cpp \
		$(OUT)/tf_d3d9_state.o $(OUT)/tf_nxgl.o $(OUT)/tf_dispatch.o -lEGL -pthread

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
SCENE_SRC := $(NATIVE)/n0/scenes.cpp $(NATIVE)/n0/run_gl.cpp $(NATIVE)/front/front.cpp $(NATIVE)/capture/capture.cpp
SCENE_CSRC := $(NATIVE)/front/d3d9_state.c $(NATIVE)/front/gpu_dispatch.c $(NATIVE)/gl/nxgl.c $(SHOUT)/nx_shaders.c
SCENE_DEPS := $(wildcard $(NATIVE)/n0/*.h $(NATIVE)/include/nx/*.h $(NATIVE)/front/*.h $(NATIVE)/gl/*.h)

$(SHOUT)/nx_shaders.c: $(wildcard $(NATIVE)/shaders/*.glsl) $(NATIVE)/shaders/build.py
	python3 $(NATIVE)/shaders/build.py $(SHOUT) $(if $(UAM),--uam $(UAM))

$(OUT)/run_gl: $(SCENE_SRC) $(SCENE_CSRC) $(SCENE_DEPS)
	$(CXX) -std=c++17 -O1 -g -Wall -Wextra -Werror -fno-exceptions -fno-rtti $(CPPFLAGS) -I$(SHOUT) -c $(NATIVE)/front/front.cpp -o $(OUT)/front.o
	$(CXX) -std=c++17 -O1 -g -Wall -Wextra -Werror -fno-exceptions -fno-rtti $(CPPFLAGS) -I$(SHOUT) -c $(NATIVE)/n0/scenes.cpp -o $(OUT)/scenes.o
	$(CXX) -std=c++17 -O1 -g -Wall -Wextra -Werror -fno-exceptions -fno-rtti $(CPPFLAGS) -I$(SHOUT) -c $(NATIVE)/n0/run_gl.cpp -o $(OUT)/run_gl.o
	$(CXX) -std=c++17 -O1 -g -Wall -Wextra -Werror -fno-exceptions -fno-rtti $(CPPFLAGS) -c $(NATIVE)/capture/capture.cpp -o $(OUT)/capture.o
	$(CC) -std=c11 -O1 -g -Wall -Wextra -Werror $(CPPFLAGS) -I$(SHOUT) -c $(NATIVE)/gl/nxgl.c -o $(OUT)/nxgl.o
	$(CC) -std=c11 -O1 -g -Wall -Wextra -Werror $(CPPFLAGS) -c $(NATIVE)/front/gpu_dispatch.c -o $(OUT)/gpu_dispatch.o
	$(CC) -std=c11 -O1 -g -Wall -Wextra $(CPPFLAGS) -c $(NATIVE)/front/d3d9_state.c -o $(OUT)/d3d9_state_s.o
	$(CC) -std=c11 -O1 -I$(SHOUT) -c $(SHOUT)/nx_shaders.c -o $(OUT)/nx_shaders.o
	$(CXX) -o $@ $(OUT)/front.o $(OUT)/scenes.o $(OUT)/run_gl.o $(OUT)/capture.o $(OUT)/nxgl.o $(OUT)/gpu_dispatch.o $(OUT)/d3d9_state_s.o $(OUT)/nx_shaders.o -lEGL -lm

scenes: $(OUT)/run_gl
	python3 scenes.py --bin $(OUT)/run_gl --out $(OUT)/scenes

.PHONY: scenes

# ---- the ILP32 Switch guest under Unicorn, nxgpu calls forwarded to the GL backend (tests/s6n/emu_n0.py) ----
$(OUT)/libnxgl.so: $(NATIVE)/gl/nxgl.c $(NATIVE)/front/gpu_dispatch.c $(SCENE_DEPS)
	@mkdir -p $(OUT)
	$(CC) -std=c11 -O1 -g -fPIC -shared $(CPPFLAGS) -o $@ $(NATIVE)/gl/nxgl.c $(NATIVE)/front/gpu_dispatch.c -lEGL

# needs: pip install unicorn; GUEST=<switch/build.sh out>/guest/guest.nso
emu: $(OUT)/libnxgl.so $(OUT)/replay_gl
	python3 emu_n0.py $(GUEST) $(OUT)/libnxgl.so $(OUT)/emu
	python3 scenes.py --compare $(OUT)/emu/fnvnx/n0/out
	$(OUT)/replay_gl $(OUT)/emu/fnvnx/n0/out/n0.nxcap

.PHONY: emu

# ---- capture + replay: run the scenes with CaptureBackend on GL, replay the file on a fresh GL context ----------
$(OUT)/replay_gl: $(NATIVE)/capture/replay.cpp $(NATIVE)/capture/replay_gl.cpp $(NATIVE)/capture/replay.h $(OUT)/run_gl
	$(CXX) -std=c++17 -O1 -g -Wall -Wextra -Werror $(CPPFLAGS) -o $@ $(NATIVE)/capture/replay.cpp $(NATIVE)/capture/replay_gl.cpp \
		$(OUT)/nxgl.o $(OUT)/gpu_dispatch.o -lEGL

# ---- nx_render: captures (.nxcap, .nxd9) and scenes -> PNG on the GL backend (S16-compare entry point) ----------
$(OUT)/nx_render: $(NATIVE)/capture/nx_render.cpp $(NATIVE)/capture/d3d9_exec.cpp $(NATIVE)/capture/d3d9_exec.h \
		$(NATIVE)/capture/d3d9cap.h $(NATIVE)/capture/png.h $(NATIVE)/capture/replay.cpp $(OUT)/run_gl
	$(CXX) -std=c++17 -O1 -g -Wall -Wextra -Werror $(CPPFLAGS) -I$(SHOUT) -o $@ $(NATIVE)/capture/nx_render.cpp \
		$(NATIVE)/capture/d3d9_exec.cpp $(NATIVE)/capture/replay.cpp $(OUT)/front.o $(OUT)/scenes.o $(OUT)/nxgl.o \
		$(OUT)/gpu_dispatch.o $(OUT)/d3d9_state_s.o $(OUT)/nx_shaders.o -lEGL -lm

nx_render: $(OUT)/nx_render
	python3 -I test_nx_render.py --bin $(OUT)/nx_render --run-gl $(OUT)/run_gl --out $(OUT)/nx_render_test

.PHONY: nx_render

capture: $(OUT)/run_gl $(OUT)/replay_gl
	@mkdir -p $(OUT)/cap
	$(OUT)/run_gl $(OUT)/cap --capture $(OUT)/cap/n0.nxcap
	$(OUT)/replay_gl $(OUT)/cap/n0.nxcap $(OUT)/cap

.PHONY: capture

# ---- N1: nx::Renderer / StateTracker self-test on the GL backend --------------------------------------------------
N1 := $(NATIVE)/n1
N1SRC := $(N1)/renderer.cpp $(N1)/passes.cpp $(N1)/translated.cpp $(N1)/engine_stubs.cpp $(N1)/engine_device.cpp $(N1)/engine_textures.cpp
$(OUT)/test_n1_gl: $(N1SRC) $(wildcard $(N1)/*.h) $(N1)/selftest.cpp $(N1)/selftest_world.cpp $(N1)/test_gl.cpp $(OUT)/run_gl
	$(CXX) -std=c++17 -O1 -g -Wall -Wextra -Werror -fno-exceptions -fno-rtti $(CPPFLAGS) -I$(SHOUT) -I$(N1) -o $@ \
		$(N1SRC) $(N1)/selftest.cpp $(N1)/selftest_world.cpp $(N1)/test_gl.cpp $(OUT)/front.o $(OUT)/nxgl.o $(OUT)/gpu_dispatch.o \
		$(OUT)/d3d9_state_s.o $(OUT)/nx_shaders.o -lEGL -lm

n1: $(OUT)/test_n1_gl
	$(OUT)/test_n1_gl
	python3 -I test_d3d9_state_table.py --out $(OUT) $(if $(EXE),--exe $(EXE))

.PHONY: n1

# N1 self-test as the ILP32 guest under Unicorn: N1OUT=<render/native/n1/build.sh out dir>
n1emu: $(OUT)/libnxgl.so
	python3 emu_n0.py $(N1OUT)/n1_test.nso $(OUT)/libnxgl.so $(OUT)/emu_n1 fnvnx/n1/results.txt

.PHONY: n1emu

# ---- nx_render robustness: an ASan/UBSan build against malformed .nxd9/.nxcap (J-0147 V1 finding) -------------
$(OUT)/nx_render_asan: $(NATIVE)/capture/nx_render.cpp $(NATIVE)/capture/d3d9_exec.cpp $(NATIVE)/capture/d3d9_exec.h \
		$(NATIVE)/capture/d3d9cap.h $(NATIVE)/capture/png.h $(NATIVE)/capture/replay.cpp $(OUT)/run_gl
	$(CXX) -std=c++17 -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=undefined -fno-sanitize=vptr -Wall -Wextra -Werror \
		$(CPPFLAGS) -I$(SHOUT) -o $@ $(NATIVE)/capture/nx_render.cpp $(NATIVE)/capture/d3d9_exec.cpp \
		$(NATIVE)/capture/replay.cpp $(OUT)/front.o $(OUT)/scenes.o $(OUT)/nxgl.o $(OUT)/gpu_dispatch.o \
		$(OUT)/d3d9_state_s.o $(OUT)/nx_shaders.o -lEGL -lm

nx_render_robust: $(OUT)/nx_render_asan nx_render
	python3 -I test_nx_render_bounds.py --bin $(OUT)/nx_render_asan --work $(OUT)/nxr_bounds
	python3 -I ../v1/test_nx_render_robust.py --bin $(OUT)/nx_render_asan --work $(OUT)/nxr_v1 --iters $(ITERS) \
		--seed $(OUT)/nx_render_test/t.nxd9 --seed $(OUT)/nx_render_test/n0.nxcap

ITERS ?= 300
.PHONY: nx_render_robust
