# tests/s4 - host tests of the runtime layer (platform/) and the ILP32 syntax check.
#
#   make -C tests/s4            build and run the host tests (x86-64 or aarch64 Linux, os_posix backend)
#   make -C tests/s4 ilp32      -fsyntax-only of platform/ with aarch64-linux-gnu-gcc -mabi=ilp32
#                               (os_horizon.c against libnx headers: LIBNX=<libnx checkout>)
#
# No ILP32 libc exists on hosts, so the tests run LP64; DWORD/LONG are 64-bit there, which the code handles
# (W32_DW masks). -fshort-wchar matches the game build (WCHAR is 16-bit).

HERE := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
ROOT := $(abspath $(HERE)/../..)
P := $(ROOT)/platform
OUT ?= $(HERE)/build

CC ?= gcc
CXX ?= g++
FLAGS := -g -O1 -frounding-math -Wall -Wextra -Wno-unused-parameter -fshort-wchar -pthread \
         -I$(P)/include -iquote $(ROOT)/shim/include -I$(P)/crt
CFLAGS_ := -std=gnu11 $(FLAGS)
CXXFLAGS_ := -std=gnu++17 $(FLAGS)

PLAT_C := $(P)/os/os_posix.c $(wildcard $(P)/crt/*.c)
PLAT_CXX := $(wildcard $(P)/win32/*.cpp) $(wildcard $(P)/crt/*.cpp)
TEST_CXX := $(wildcard $(HERE)/*.cpp)

OBJS := $(patsubst $(ROOT)/%,$(OUT)/%.o,$(PLAT_C) $(PLAT_CXX) $(TEST_CXX))

.PHONY: all test ilp32 clean
all: test

$(OUT)/%.c.o: $(ROOT)/%.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS_) -c $< -o $@

$(OUT)/%.cpp.o: $(ROOT)/%.cpp
	@mkdir -p $(dir $@)
	$(CXX) $(CXXFLAGS_) -c $< -o $@

$(OUT)/s4_tests: $(OBJS)
	$(CXX) $(CXXFLAGS_) $^ -o $@ -lm

test: $(OUT)/s4_tests
	rm -rf $(OUT)/root && FNVNX_ROOT=$(OUT)/root $(OUT)/s4_tests

# ---- AArch64 ILP32 syntax check (the game's data model)
XCC ?= aarch64-linux-gnu-gcc
XCXX ?= aarch64-linux-gnu-g++
LIBNX ?= $(ROOT)/ext/libnx
XFLAGS := -mabi=ilp32 -fsyntax-only -frounding-math -Wall -Wextra -Wno-unused-parameter -fshort-wchar \
          -I$(P)/include -iquote $(ROOT)/shim/include -I$(P)/crt
ilp32:
	@set -e; for f in $(PLAT_C); do echo "ilp32 cc  $${f#$(ROOT)/}"; $(XCC) -std=gnu11 $(XFLAGS) $$f; done
	@set -e; for f in $(PLAT_CXX); do echo "ilp32 c++ $${f#$(ROOT)/}"; $(XCXX) -std=gnu++17 $(XFLAGS) $$f; done
	@echo "ilp32 cc  platform/os/os_horizon.c (libnx headers, newlib lock stub)"
	@$(XCC) -std=gnu11 $(XFLAGS) -D__SWITCH__ -I$(HERE)/ilp32-stubs -I$(LIBNX)/nx/include $(P)/os/os_horizon.c
	@echo "ilp32: all platform sources parse"

clean:
	rm -rf $(OUT)
