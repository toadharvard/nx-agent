# tests/s7 - host tests of platform/audio and platform/input, and their ILP32 syntax check.
#
#   make -C tests/s7          build and run (links S4's platform/win32 + crt + os_posix for events/modules/COM)
#   make -C tests/s7 ilp32    -fsyntax-only of platform/audio, platform/input with aarch64-linux-gnu -mabi=ilp32
#                             (Horizon files against libnx headers: LIBNX=<libnx checkout>)

HERE := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
ROOT := $(abspath $(HERE)/../..)
P := $(ROOT)/platform
OUT ?= $(HERE)/build

CC ?= gcc
CXX ?= g++
FLAGS := -MMD -MP -g -O1 -frounding-math -Wall -Wextra -Wno-unused-parameter -fshort-wchar -pthread \
         -I$(P)/include -iquote $(ROOT)/shim/include -I$(ROOT)/shim/include -I$(P)/crt
CFLAGS_ := -std=gnu11 $(FLAGS)
CXXFLAGS_ := -std=gnu++17 $(FLAGS)

S4_C := $(P)/os/os_posix.c $(wildcard $(P)/crt/*.c)
S4_CXX := $(wildcard $(P)/win32/*.cpp) $(wildcard $(P)/crt/*.cpp)
S7_CXX := $(wildcard $(P)/audio/*.cpp) $(wildcard $(P)/input/*.cpp)
S7_HZ := $(P)/audio/backend_audren.c
TEST_CXX := $(wildcard $(HERE)/*.cpp)

OBJS := $(patsubst $(ROOT)/%,$(OUT)/%.o,$(S4_C) $(S4_CXX) $(S7_CXX) $(TEST_CXX))

.PHONY: all test ilp32 clean
all: test
-include $(OBJS:.o=.d)

$(OUT)/%.c.o: $(ROOT)/%.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS_) -c $< -o $@

$(OUT)/%.cpp.o: $(ROOT)/%.cpp
	@mkdir -p $(dir $@)
	$(CXX) $(CXXFLAGS_) -c $< -o $@

$(OUT)/s7_tests: $(OBJS)
	$(CXX) $(CXXFLAGS_) $^ -o $@ -lm

test: $(OUT)/s7_tests
	rm -rf $(OUT)/root && mkdir -p $(OUT)/root/game && FNVNX_ROOT=$(OUT)/root $(OUT)/s7_tests

XCC ?= aarch64-linux-gnu-gcc
XCXX ?= aarch64-linux-gnu-g++
LIBNX ?= $(ROOT)/ext/libnx
XFLAGS := -mabi=ilp32 -fsyntax-only -frounding-math -Wall -Wextra -Wno-unused-parameter -fshort-wchar \
          -I$(P)/include -iquote $(ROOT)/shim/include -I$(ROOT)/shim/include -I$(P)/crt
ilp32:
	@set -e; for f in $(S7_CXX); do echo "ilp32 c++ $${f#$(ROOT)/}"; $(XCXX) -std=gnu++17 $(XFLAGS) $$f; done
	@echo "ilp32 (libnx) platform/audio/backend_audren.c platform/input/input_horizon.cpp"
	@$(XCC) -std=gnu11 $(XFLAGS) -D__SWITCH__ -I$(ROOT)/tests/s4/ilp32-stubs -I$(LIBNX)/nx/include $(S7_HZ)
	@$(XCXX) -std=gnu++17 $(XFLAGS) -D__SWITCH__ $(P)/input/xinput.cpp
	@# libnx's C++ headers are not ILP32-clean (BITL enums): input_horizon.cpp sits on the LP64 side of the
	@# boundary (its interface, input.h/os.h, is fixed-width C), so it is checked with the LP64 ABI.
	@$(XCXX) -std=gnu++17 $(subst -mabi=ilp32,-mabi=lp64,$(XFLAGS)) -D__SWITCH__ -I$(ROOT)/tests/s4/ilp32-stubs -I$(LIBNX)/nx/include $(P)/input/input_horizon.cpp
	@echo "ilp32: all S7 sources parse"

clean:
	rm -rf $(OUT)
