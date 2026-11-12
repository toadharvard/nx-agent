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
         -I$(P)/include -iquote $(ROOT)/shim/include -I$(ROOT)/shim/include -I$(P)/crt -I$(ROOT)/ext/minimp3 -I$(ROOT)/ext/freetype/include -I$(ROOT)/loader/include -I$(ROOT)/toolchain/include
CFLAGS_ := -std=gnu11 $(FLAGS)
CXXFLAGS_ := -std=gnu++17 $(FLAGS)

S4_C := $(P)/os/os_posix.c $(P)/os/os_world.c $(wildcard $(P)/crt/*.c)
S4_CXX := $(P)/clock/clock_rt.cpp $(P)/clock/clock_real.cpp $(filter-out %/filemap_private.cpp,$(wildcard $(P)/win32/*.cpp)) $(wildcard $(P)/crt/*.cpp)
S7_CXX := $(P)/com/combridge.cpp $(P)/com/combridge.gen.cpp $(P)/com/combridge_exports.gen.cpp $(wildcard $(P)/audio/*.cpp) $(filter-out %/input_horizon.cpp,$(wildcard $(P)/input/*.cpp))   # input_horizon.cpp: libnx only (make ilp32)
S7_HZ := $(P)/audio/backend_audren.c
TEST_CXX := $(wildcard $(HERE)/*.cpp) $(ROOT)/tests/s4/host_boundary.cpp

OBJS := $(patsubst $(ROOT)/%,$(OUT)/%.o,$(S4_C) $(S4_CXX) $(S7_CXX) $(TEST_CXX))

# The platform's own sources see the game's callbacks (WNDPROC, ...) as fnvnx_guestfn (shim/include/fnvnx_callback.h:
# a call goes through the boundary, a direct call does not compile); the tests are "the game" and keep plain pointers.
$(filter $(OUT)/platform/%.cpp.o,$(OBJS)): CXXFLAGS_ += -DFNVNX_PLATFORM_BUILD

.PHONY: all test vclock ilp32 qemu clean com-spec-check com-nocompile
all: test

# the imports whose documented result is one constant are generated from platform/manifest/env_const.tsv
ENVC_SRC := audio/dshow audio/dshow_filters audio/dsound audio/waveout input/dinput
ENVC_INC := $(foreach s,$(ENVC_SRC),$(P)/$(s).env_const.gen.inc)
$(P)/%.env_const.gen.inc: $(P)/manifest/env_const.tsv $(P)/manifest/gen_env_const.py
	python3 -I $(P)/manifest/gen_env_const.py $(P)/manifest/env_const.tsv $@ --only $*.cpp
$(foreach s,$(ENVC_SRC),$(eval $(OUT)/platform/$(s).cpp.o: $(P)/$(s).env_const.gen.inc))

# the COM bridge of the platform's objects (platform/com): thunks, proxies and tables generated from com_spec.json
COMGEN := $(P)/com/gen_combridge.py $(P)/com/com_spec.json
COMOUT := $(P)/com/combridge.gen.cpp $(P)/com/combridge_exports.gen.cpp $(P)/com/combridge_if.gen.h $(P)/com/combridge_rows.tsv
$(COMOUT) &: $(COMGEN)
	python3 -I $(P)/com/gen_combridge.py $(P)/com/com_spec.json $(P)/com
-include $(OBJS:.o=.d)

$(OUT)/%.c.o: $(ROOT)/%.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS_) -c $< -o $@

# the constant-result functions (platform/manifest/env_const.tsv), generated per source file that includes them
ENV_GEN := $(P)/win32/env_const.gen.inc $(P)/crt/env_const.gen.inc $(P)/os/env_const_posix.gen.inc $(P)/os/env_const_horizon.gen.inc
ENV_SRC := $(P)/manifest/env_const.tsv $(P)/manifest/gen_env_const.py
$(P)/win32/env_const.gen.inc: $(ENV_SRC)
	python3 -I $(P)/manifest/gen_env_const.py $(P)/manifest/env_const.tsv $@ --only win32/
$(P)/crt/env_const.gen.inc: $(ENV_SRC)
	python3 -I $(P)/manifest/gen_env_const.py $(P)/manifest/env_const.tsv $@ --only crt/
$(P)/os/env_const_posix.gen.inc: $(ENV_SRC)
	python3 -I $(P)/manifest/gen_env_const.py $(P)/manifest/env_const.tsv $@ --only os/os_posix.c
$(P)/os/env_const_horizon.gen.inc: $(ENV_SRC)
	python3 -I $(P)/manifest/gen_env_const.py $(P)/manifest/env_const.tsv $@ --only os/os_horizon.c
$(OUT)/platform/win32/env_const.cpp.o: $(P)/win32/env_const.gen.inc
$(OUT)/platform/crt/msvcrt_exports.cpp.o: $(P)/crt/env_const.gen.inc
$(OUT)/platform/os/os_posix.c.o: $(P)/os/env_const_posix.gen.inc

# the registry's first-boot rows: platform/manifest/declared_registry.tsv, checked against the static read sites (REGSITES, the exe-derived
# private regsites.tsv of the product build; the tests use their own synthetic file) and generated into registry_seed.gen.inc
REGSITES ?= $(ROOT)/tests/s4/regsites_synth.tsv
REG_DECL := $(P)/manifest/declared_registry.tsv
$(P)/win32/registry_seed.gen.inc: $(REG_DECL) $(P)/manifest/gen_registry_seed.py $(P)/manifest/regsites.py $(REGSITES)
	python3 -I $(P)/manifest/gen_registry_seed.py $(REGSITES) $(REG_DECL) $@
$(OUT)/platform/win32/registry_seed.cpp.o: $(P)/win32/registry_seed.gen.inc

# the declared FalloutPrefs.ini: the launcher gate's setting (PREFS_REQUIRED, the exe-derived private output of prefs_settings.py in the product
# build; the tests use their own synthetic file) = the adapter Description in the render front end's identity images (IDENT_*; synthetic here)
PREFS_REQUIRED ?= $(ROOT)/tests/s4/declfiles_required_synth.tsv
IDENT_HANDHELD ?= $(ROOT)/tests/s4/ident_synth_handheld.bin
IDENT_DOCKED ?= $(ROOT)/tests/s4/ident_synth_docked.bin
$(P)/win32/declared_files.gen.inc: $(PREFS_REQUIRED) $(IDENT_HANDHELD) $(IDENT_DOCKED) $(P)/manifest/gen_prefs_ini.py
	python3 -I $(P)/manifest/gen_prefs_ini.py $(PREFS_REQUIRED) $@ --ident $(IDENT_HANDHELD) --ident $(IDENT_DOCKED)
$(OUT)/platform/win32/declared_files.cpp.o: $(P)/win32/declared_files.gen.inc

$(OUT)/%.cpp.o: $(ROOT)/%.cpp
	@mkdir -p $(dir $@)
	$(CXX) $(CXXFLAGS_) -c $< -o $@

# platform/win32/gdifont.cpp needs the reference FreeType: the archive tests/s4 builds from ext/freetype
FTLIB := $(HERE)/../s4/build/libfreetype.a
$(FTLIB):
	$(MAKE) -C $(HERE)/../s4 $(FTLIB)
$(OUT)/s7_tests: $(OBJS) $(FTLIB)
	$(CXX) $(CXXFLAGS_) $^ -o $@ -lm

# the same test objects linked with the virtual clock (platform/clock/clock_virtual.cpp): the vc_ cases (tests/s7/test_vclock_audio.cpp)
VC_OBJS := $(filter-out %/clock_real.cpp.o,$(OBJS)) $(OUT)/platform/clock/clock_virtual.cpp.o
$(OUT)/platform/clock/clock_virtual.cpp.o: CXXFLAGS_ += -DFNVNX_PLATFORM_BUILD
$(OUT)/s7_tests_vclock: $(VC_OBJS) $(FTLIB)
	$(CXX) $(CXXFLAGS_) $^ -o $@ -lm
vclock: com-spec-check $(OUT)/s7_tests_vclock
	rm -rf $(OUT)/root-vclock && mkdir -p $(OUT)/root-vclock/game && S7_DATA=$(HERE)/data FNVNX_ROOT=$(OUT)/root-vclock $(OUT)/s7_tests_vclock vc_

# the spec is derived from the C++ declarations: it must be what the extractor writes now
com-spec-check:
	@mkdir -p $(OUT) && python3 -I $(P)/com/spec_extract.py $(OUT)/com_spec.check.json >/dev/null && cmp -s $(OUT)/com_spec.check.json $(P)/com/com_spec.json \
		|| { echo "com_spec.json is not what platform/com/spec_extract.py derives from the headers (run it)"; exit 1; }

test: com-spec-check $(OUT)/s7_tests
	rm -rf $(OUT)/root && mkdir -p $(OUT)/root/game && S7_DATA=$(HERE)/data FNVNX_ROOT=$(OUT)/root $(OUT)/s7_tests

# the same tests built static for AArch64 and run under qemu-aarch64
qemu:
	$(MAKE) --no-print-directory OUT=$(OUT)-a64 CC="aarch64-linux-gnu-gcc -static" CXX="aarch64-linux-gnu-g++ -static" \
		$(OUT)-a64/s7_tests
	rm -rf $(OUT)-a64/root && mkdir -p $(OUT)-a64/root/game && S7_DATA=$(HERE)/data FNVNX_ROOT=$(OUT)-a64/root qemu-aarch64 $(OUT)-a64/s7_tests

XCC ?= aarch64-linux-gnu-gcc
XCXX ?= aarch64-linux-gnu-g++
LIBNX ?= $(ROOT)/ext/libnx
XFLAGS := -DFNVNX_PLATFORM_BUILD -mabi=ilp32 -fsyntax-only -frounding-math -Wall -Wextra -Wno-unused-parameter -fshort-wchar \
          -I$(P)/include -iquote $(ROOT)/shim/include -I$(ROOT)/shim/include -I$(P)/crt -I$(ROOT)/ext/minimp3 -I$(ROOT)/ext/freetype/include -I$(ROOT)/loader/include -I$(ROOT)/toolchain/include
ilp32: $(ENVC_INC) $(COMOUT)
	@set -e; for f in $(S7_CXX); do echo "ilp32 c++ $${f#$(ROOT)/}"; $(XCXX) -std=gnu++17 $(XFLAGS) $$f; done
	@echo "ilp32 (libnx) platform/audio/backend_audren.c platform/input/input_horizon.cpp"
	@$(XCC) -std=gnu11 $(XFLAGS) -D__SWITCH__ -I$(ROOT)/tests/s4/ilp32-stubs -I$(LIBNX)/nx/include $(S7_HZ)
	@$(XCXX) -std=gnu++17 $(XFLAGS) -D__SWITCH__ $(P)/input/xinput.cpp
	@# libnx's C++ headers are not ILP32-clean (BITL enums): input_horizon.cpp sits on the LP64 side of the
	@# boundary (its interface, input.h/os.h, is fixed-width C), so it is checked with the LP64 ABI.
	@$(XCXX) -std=gnu++17 $(subst -mabi=ilp32,-mabi=lp64,$(XFLAGS)) -D__SWITCH__ -I$(ROOT)/tests/s4/ilp32-stubs -I$(LIBNX)/nx/include $(P)/input/input_horizon.cpp
	@echo "ilp32: all S7 sources parse"

clean:
	rm -rf $(OUT) $(OUT)-a64

# ---- the spec is checked against the declarations (not part of the default target): a spec whose argument type differs from the
# header's must not compile (static_assert), and a spec item the generator has no rule for must stop it (exit 2)
com-nocompile: $(COMOUT)
	@d=$(OUT)/com-nocompile && rm -rf $$d && mkdir -p $$d && \
	python3 -I $(P)/com/com_spec_mutate.py $(P)/com/com_spec.json $$d/type.json ctype LPDWORD && \
	python3 -I $(P)/com/com_spec_mutate.py $(P)/com/com_spec.json $$d/kind.json kind weird && \
	python3 -I $(P)/com/gen_combridge.py $$d/type.json $$d >/dev/null && \
	if $(CXX) $(CXXFLAGS_) -DFNVNX_PLATFORM_BUILD -I$(P)/com -I$$d -fsyntax-only $$d/combridge.gen.cpp >$$d/type.log 2>&1; then echo "com-nocompile: a wrong argument type COMPILED"; exit 1; fi && \
	grep -q "the spec and the declaration disagree" $$d/type.log && \
	if python3 -I $(P)/com/gen_combridge.py $$d/kind.json $$d/k >/dev/null 2>&1; then echo "com-nocompile: an unknown kind did not stop the generator"; exit 1; fi && \
	echo "com-nocompile: ok (a wrong type fails the build, an unknown kind stops the generator)"
