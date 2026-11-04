# render-veg family tests (grass; trees, particles, decals to follow):
#   make -C tests/s6n -f veg.mk [UAM=<uam>]                 unit tests of the CPU side + shader package build
#   make -C tests/s6n -f veg.mk parity FNVNX_T019=<dir>      scene check against Translated (sdp2dksh -o <dir> --glsl
#                                                            shaderpackage019.sdp; skipped without it)
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
NATIVE := $(ROOT)/render/native
OUT ?= /tmp/fnvnx-s6n-tests
SHOUT := $(OUT)/veg_shaders
CXX ?= g++

all: $(OUT)/test_veg $(SHOUT)/nx_shaders.c
	$(OUT)/test_veg

$(OUT)/test_veg: test_veg.cpp $(NATIVE)/n1/veg_grass.cpp $(NATIVE)/n1/veg_tree.cpp $(NATIVE)/n1/veg_families.h
	@mkdir -p $(OUT)
	$(CXX) -std=c++17 -O1 -g -Wall -Wextra -Werror -fno-exceptions -fno-rtti -I$(NATIVE)/n1 -o $@ test_veg.cpp $(NATIVE)/n1/veg_grass.cpp $(NATIVE)/n1/veg_tree.cpp

$(OUT)/libnxveg.so: $(NATIVE)/n1/veg_grass.cpp $(NATIVE)/n1/veg_tree.cpp $(NATIVE)/n1/veg_families.h
	@mkdir -p $(OUT)
	$(CXX) -std=c++17 -O1 -fPIC -shared -Wall -Wextra -Werror -fno-exceptions -fno-rtti -I$(NATIVE)/n1 -o $@ $(NATIVE)/n1/veg_grass.cpp $(NATIVE)/n1/veg_tree.cpp

$(SHOUT)/nx_shaders.c: $(wildcard $(NATIVE)/shaders/*.glsl) $(NATIVE)/shaders/build.py
	python3 $(NATIVE)/shaders/build.py $(SHOUT) $(if $(UAM),--uam $(UAM))

parity: $(OUT)/libnxveg.so $(SHOUT)/nx_shaders.c
	FNVNX_VEG_LIB=$(OUT)/libnxveg.so python3 -I veg_parity.py $(SHOUT)

.PHONY: all parity
