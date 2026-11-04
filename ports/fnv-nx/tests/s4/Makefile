# tests/s4 - host tests of the runtime layer (platform/) and the ILP32 syntax check.
#
#   make -C tests/s4            build and run the host tests (x86-64 or aarch64 Linux, os_posix backend)
#   make -C tests/s4 m32        the -m32 tests (tests/s4/m32: platform/ + S7 + render/nine/d3dx on i386, the game's
#                               data model; needs gcc-multilib's lib32 libraries)
#   make -C tests/s4 iat EXE=<FalloutNV.exe>   S4's IAT metric: the exe's import slots with no export at boot
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

.PHONY: all test ilp32 m32 iat device clean
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

# ---- i386 (-m32) build: the ILP32 data model with a host libc. /usr/include/asm is the multilib packages' symlink,
# missing when a cross compiler replaced them; the x86-64 asm headers serve i386 too.
M32OUT := $(OUT)-m32
M32FLAGS := -m32 -idirafter /usr/include/x86_64-linux-gnu $(FLAGS) -I$(ROOT)/shim/include
M32_CXX := $(PLAT_CXX) $(wildcard $(P)/audio/*.cpp) $(wildcard $(P)/input/*.cpp)   # input_horizon.cpp: host no-op pump
# the D3D9=null guest's D3D side (toolchain/game/build.sh): D3DX and the null device (d3d9.dll module)
M32_D3D := $(wildcard $(ROOT)/render/nine/d3dx/*.cpp) $(ROOT)/toolchain/game/null_d3d9.cpp
M32_TEST := $(wildcard $(HERE)/m32/*.cpp)
M32_OBJS := $(patsubst $(ROOT)/%,$(M32OUT)/%.o,$(PLAT_C) $(M32_CXX) $(M32_D3D) $(M32_TEST))
M32_D3DFLAGS := -std=gnu++17 -m32 -idirafter /usr/include/x86_64-linux-gnu -g -O1 -frounding-math -Wall -fshort-wchar \
	-include $(HERE)/m32/compat_m32.h -I$(ROOT)/shim/include -I$(ROOT)/toolchain/include -I$(P)/include
$(M32OUT)/render/nine/d3dx/%.cpp.o: $(ROOT)/render/nine/d3dx/%.cpp
	@mkdir -p $(dir $@)
	$(CXX) $(M32_D3DFLAGS) -fno-exceptions -fno-rtti -c $< -o $@
$(M32OUT)/toolchain/game/null_d3d9.cpp.o: $(ROOT)/toolchain/game/null_d3d9.cpp
	@mkdir -p $(dir $@)
	$(CXX) $(M32_D3DFLAGS) -w -fms-extensions -fpermissive -c $< -o $@
$(M32OUT)/tests/s4/m32/%.cpp.o: $(HERE)/m32/%.cpp
	@mkdir -p $(dir $@)
	$(CXX) -std=gnu++17 $(M32FLAGS) -include $(HERE)/m32/compat_m32.h -c $< -o $@
$(M32OUT)/%.c.o: $(ROOT)/%.c
	@mkdir -p $(dir $@)
	$(CC) -std=gnu11 $(M32FLAGS) -c $< -o $@
$(M32OUT)/%.cpp.o: $(ROOT)/%.cpp
	@mkdir -p $(dir $@)
	$(CXX) -std=gnu++17 $(M32FLAGS) -c $< -o $@
$(M32OUT)/s4_m32: $(M32_OBJS)
	$(CXX) -m32 -pthread $^ -o $@ -lm
m32: $(M32OUT)/s4_m32
	rm -rf $(M32OUT)/root && FNVNX_ROOT=$(M32OUT)/root $(M32OUT)/s4_m32
iat: $(M32OUT)/s4_m32
	@test -n "$(EXE)" || { echo "set EXE=<FalloutNV.exe>"; exit 2; }
	python3 -I $(HERE)/iat/pe_imports.py "$(EXE)" > $(M32OUT)/imports.txt
	rm -rf $(M32OUT)/root && FNVNX_ROOT=$(M32OUT)/root $(M32OUT)/s4_m32 --iat $(M32OUT)/imports.txt

# ---- device backends: the inventory (tests/s4/backends/backends.tsv, metric device-open) and the qemu-aarch64 tests
# of the host-side pieces. With EXE=, the inventory is checked against every import of the exe.
device:
	@if [ -n "$(EXE)" ]; then $(MAKE) --no-print-directory iat EXE="$(EXE)" > /dev/null; \
		python3 -I $(HERE)/backends/check_backends.py --imports $(M32OUT)/imports.txt --list; \
	else python3 -I $(HERE)/backends/check_backends.py --list; fi
	$(HERE)/backends/run_qemu.sh $(OUT)-device

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
	rm -rf $(OUT) $(M32OUT) $(OUT)-device
