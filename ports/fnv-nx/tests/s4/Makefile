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

# FreeType of the reference environment (Ubuntu 24.04: 2.13.2+dfsg-1ubuntu0.2 with its patches), unpacked by
# platform/win32/tools/fetch_freetype.sh <dir-with-the-two-tarballs> into the git-ignored ext/freetype. Built here with the
# modules of upstream modules.cfg (+ gxvalid/otvalid of the Debian patch) and the default ftoption.h + the Debian
# patch's FT_CONFIG_OPTION_SUBPIXEL_RENDERING; bundled zlib (gzip), lzw; no libpng / system zlib / brotli / bzip2 /
# harfbuzz (the Debian package links them for PCF.gz, PNG strikes, WOFF2, bzip2'd fonts: formats of no font in the
# reference set, see the font-format check in tests/s4/test_gdifont.cpp).
HERE := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
ROOT := $(abspath $(HERE)/../..)
P := $(ROOT)/platform
OUT ?= $(HERE)/build
FT := $(ROOT)/ext/freetype
FNVNX_FONT_REF ?= /home/user/gd/fontref
FNVNX_FONT_HOST ?= /home/user/gd/fonts/deb/usr/share/fonts
FT_SRCS := base/ftsystem base/ftinit base/ftdebug base/ftbase base/ftbbox base/ftbdf base/ftbitmap base/ftcid \
    base/ftfstype base/ftgasp base/ftglyph base/ftgxval base/ftmm base/ftotval base/ftpatent base/ftpfr base/ftstroke \
    base/ftsynth base/fttype1 base/ftwinfnt autofit/autofit bdf/bdf cff/cff cid/type1cid pcf/pcf pfr/pfr psaux/psaux \
    pshinter/pshinter psnames/psmodule raster/raster sdf/sdf sfnt/sfnt smooth/smooth svg/svg truetype/truetype \
    type1/type1 type42/type42 winfonts/winfnt gzip/ftgzip lzw/ftlzw bzip2/ftbzip2 cache/ftcache gxvalid/gxvalid \
    otvalid/otvalid
FT_CFLAGS := -O2 -w -DFT2_BUILD_LIBRARY -I$(FT)/include -I$(FT)/src

CC ?= gcc
CXX ?= g++
FLAGS := -g -O1 -frounding-math -Wall -Wextra -Wno-unused-parameter -fshort-wchar -pthread \
         -I$(P)/include -iquote $(ROOT)/shim/include -I$(ROOT)/shim/include -I$(P)/crt -I$(ROOT)/ext/minimp3 -I$(FT)/include -I$(ROOT)/loader/include -I$(ROOT)/toolchain/include
CFLAGS_ := -std=gnu11 $(FLAGS)
CXXFLAGS_ := -std=gnu++17 $(FLAGS)

PLAT_C := $(P)/os/os_posix.c $(P)/os/os_world.c $(wildcard $(P)/crt/*.c)
# the clock interface (platform/include/fnvnx_clock.h): clock_rt.cpp is in both, clock_real.cpp is the product's clock; `make vclock`
# links clock_virtual.cpp instead (the verification mode, definition VC1)
CLOCK_CXX := $(P)/clock/clock_rt.cpp $(P)/clock/clock_real.cpp
PLAT_CXX := $(CLOCK_CXX) $(P)/com/combridge.cpp $(P)/com/combridge.gen.cpp $(filter-out %/filemap_private.cpp,$(wildcard $(P)/win32/*.cpp)) $(filter-out %/msvcrt_table.gen.cpp,$(wildcard $(P)/crt/*.cpp)) $(P)/crt/msvcrt_table.gen.cpp $(wildcard $(P)/audio/*.cpp) $(filter-out %/input_horizon.cpp,$(wildcard $(P)/input/*.cpp))   # audio: winmm waveOut* are exports of the table; input: DirectInput, XInput, the keyboard applet (input_horizon.cpp is libnx only)
TEST_CXX := $(filter-out %/host_section.cpp,$(wildcard $(HERE)/*.cpp)) $(HERE)/m32/host_input.cpp   # host_input.cpp: the host has no polled input source (input_horizon.cpp is libnx only)   # the host section services belong to the private binary only

OBJS := $(patsubst $(ROOT)/%,$(OUT)/%.o,$(PLAT_C) $(PLAT_CXX) $(TEST_CXX))

# The platform's own sources see the game's callbacks (WNDPROC, ...) as fnvnx_guestfn (shim/include/fnvnx_callback.h:
# a call goes through the boundary, a direct call does not compile); the tests are "the game" and keep plain pointers.
$(filter $(OUT)/platform/%.cpp.o,$(OBJS)): CXXFLAGS_ += -DFNVNX_PLATFORM_BUILD

.PHONY: regsites-check all test vclock probe private ilp32 m32 iat device qemu clean gfn-nocompile hostapi-check dynsites-check
all: test

$(OUT)/%.c.o: $(ROOT)/%.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS_) -c $< -o $@

$(FT)/include/ft2build.h:
	@echo "FreeType: run platform/win32/tools/fetch_freetype.sh <dir-with-the-two-tarballs> (ext/freetype is missing)"; exit 1
$(OUT)/freetype/%.o: $(FT)/src/%.c $(FT)/include/ft2build.h
	@mkdir -p $(dir $@)
	$(CC) $(FT_CFLAGS) -c $< -o $@
$(OUT)/libfreetype.a: $(patsubst %,$(OUT)/freetype/%.o,$(FT_SRCS))
	rm -f $@ && ar rcs $@ $^

# platform/win32/nls_tables.inc is generated from the reference NLS data (Wine 9.0 nls/*.nls, LGPL; mirrored in the
# private fnv-nx-build branch tools/nls) and is not part of this repository: NLS_DIR=<directory with the .nls files>.
NLS_DIR ?= /home/user/gd/nls
$(P)/win32/nls_tables.inc: $(P)/win32/tools/gen_nls.py
	@test -f $(NLS_DIR)/locale.nls || { echo "nls_tables.inc: set NLS_DIR to the Wine 9.0 nls directory (fnv-nx-build tools/nls)"; exit 1; }
	python3 $< $(NLS_DIR) $@
$(OUT)/platform/win32/nls.cpp.o: $(P)/win32/nls_tables.inc

# platform/win32/winerr_msgs.inc (the FormatMessage system message table) is generated from Wine 9.0's
# dlls/kernelbase/winerror.mc (LGPL; mirrored in the private fnv-nx-build branch tools/mc); MC_DIR=<directory with it>.
MC_DIR ?= /home/user/gd/wine-mc
$(P)/win32/winerr_msgs.inc: $(P)/win32/tools/gen_winerr.py
	@test -f $(MC_DIR)/winerror.mc || { echo "winerr_msgs.inc: set MC_DIR to the directory with Wine 9.0 winerror.mc (fnv-nx-build tools/mc)"; exit 1; }
	python3 -I $< $(MC_DIR)/winerror.mc $@
$(OUT)/platform/win32/module.cpp.o: $(P)/win32/winerr_msgs.inc
$(OUT)-m32/platform/win32/module.cpp.o: $(P)/win32/winerr_msgs.inc

# the constant-result functions (platform/manifest/env_const.tsv), generated per source file that includes them
ENV_GEN := $(P)/win32/env_const.gen.inc $(P)/crt/env_const.gen.inc $(P)/os/env_const_posix.gen.inc $(P)/os/env_const_horizon.gen.inc
ENV_SRC := $(P)/manifest/env_const.tsv $(P)/manifest/gen_env_const.py $(ROOT)/verify/declared_machine.tsv
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
$(OUT)-m32/platform/win32/env_const.cpp.o: $(P)/win32/env_const.gen.inc
$(OUT)-m32/platform/crt/msvcrt_exports.cpp.o: $(P)/crt/env_const.gen.inc
$(OUT)-m32/platform/os/os_posix.c.o: $(P)/os/env_const_posix.gen.inc

# the registry's first-boot rows: platform/manifest/declared_registry.tsv, checked against the static read sites (REGSITES, the exe-derived
# private regsites.tsv of the product build; the tests use their own synthetic file) and generated into registry_seed.gen.inc
REGSITES ?= $(HERE)/regsites_synth.tsv
REG_DECL := $(P)/manifest/declared_registry.tsv
$(P)/win32/registry_seed.gen.inc: $(REG_DECL) $(P)/manifest/gen_registry_seed.py $(P)/manifest/regsites.py $(REGSITES)
	python3 -I $(P)/manifest/gen_registry_seed.py $(REGSITES) $(REG_DECL) $@
$(OUT)/platform/win32/registry_seed.cpp.o: $(P)/win32/registry_seed.gen.inc

# the declared FalloutPrefs.ini: the launcher gate's setting (PREFS_REQUIRED, the exe-derived private output of prefs_settings.py in the product
# build; the tests use their own synthetic file) = the adapter Description in the render front end's identity images (IDENT_*; synthetic here)
PREFS_REQUIRED ?= $(HERE)/declfiles_required_synth.tsv
IDENT_HANDHELD ?= $(HERE)/ident_synth_handheld.bin
IDENT_DOCKED ?= $(HERE)/ident_synth_docked.bin
$(P)/win32/declared_files.gen.inc: $(PREFS_REQUIRED) $(IDENT_HANDHELD) $(IDENT_DOCKED) $(P)/manifest/gen_prefs_ini.py
	python3 -I $(P)/manifest/gen_prefs_ini.py $(PREFS_REQUIRED) $@ --ident $(IDENT_HANDHELD) --ident $(IDENT_DOCKED)
$(OUT)/platform/win32/declared_files.cpp.o: $(P)/win32/declared_files.gen.inc
$(OUT)/tests/s4/test_declared_prefs.cpp.o: CXXFLAGS_ += -DIDENT_PATH='"$(IDENT_DOCKED)"'
$(OUT)-m32/platform/win32/declared_files.cpp.o: $(P)/win32/declared_files.gen.inc
$(OUT)-m32/platform/win32/registry_seed.cpp.o: $(P)/win32/registry_seed.gen.inc

$(OUT)/%.cpp.o: $(ROOT)/%.cpp
	@mkdir -p $(dir $@)
	$(CXX) $(CXXFLAGS_) -c $< -o $@

# the per-source constant-result bodies of platform/audio and platform/input, and the COM bridge generated from
# platform/com/com_spec.json: a fresh checkout builds with the default target
ENVC_SRC := audio/dshow audio/dshow_filters audio/dsound audio/waveout input/dinput
$(P)/%.env_const.gen.inc: $(P)/manifest/env_const.tsv $(P)/manifest/gen_env_const.py
	python3 -I $(P)/manifest/gen_env_const.py $(P)/manifest/env_const.tsv $@ --only $*.cpp
$(foreach s,$(ENVC_SRC),$(eval $(OUT)/platform/$(s).cpp.o: $(P)/$(s).env_const.gen.inc))
# the msvcrt lookup table: msvcrt_names.tsv (name, symbol, kind) is the one source; the generated unit is tracked and the gate `imports` reads the tsv
$(P)/crt/msvcrt_table.gen.cpp: $(P)/crt/msvcrt_names.tsv $(P)/crt/gen_msvcrt_table.py
	python3 -I $(P)/crt/gen_msvcrt_table.py $(P)/crt/msvcrt_names.tsv $@
COMGEN := $(P)/com/gen_combridge.py $(P)/com/com_spec.json
COMOUT := $(P)/com/combridge.gen.cpp $(P)/com/combridge_exports.gen.cpp $(P)/com/combridge_if.gen.h $(P)/com/combridge_rows.tsv
$(COMOUT) &: $(COMGEN)
	python3 -I $(P)/com/gen_combridge.py $(P)/com/com_spec.json $(P)/com

$(OUT)/s4_tests: $(OBJS) $(OUT)/libfreetype.a
	$(CXX) $(CXXFLAGS_) $(OBJS) $(OUT)/libfreetype.a -o $@ -lm $(LDEXTRA)

# ---- probe of the module/name lookups: `make probe` builds $(OUT)/probe_dynsites
probe: $(OUT)/probe_dynsites

# the probe of the module/name lookups (platform/manifest/probe_dynsites.cpp), driven by platform/manifest/check_dynsites.py
DYN_OBJS := $(patsubst $(ROOT)/%,$(OUT)/%.o,$(PLAT_C) $(PLAT_CXX)) $(OUT)/tests/s4/host_boundary.cpp.o $(OUT)/tests/s4/m32/host_input.cpp.o $(OUT)/platform/manifest/probe_dynsites.cpp.o
$(OUT)/platform/manifest/probe_dynsites.cpp.o: CXXFLAGS_ += -DFNVNX_PLATFORM_BUILD
$(OUT)/probe_dynsites: $(DYN_OBJS) $(OUT)/libfreetype.a
	$(CXX) $(CXXFLAGS_) $(DYN_OBJS) $(OUT)/libfreetype.a -o $@ -lm $(LDEXTRA)

# ---- the same test objects linked with the virtual clock (platform/clock/clock_virtual.cpp) instead of the real one; runs the vc_ cases
# (tests/s4/test_vclock.cpp: each returns at once unless the linked clock says "virtual")
VC_OBJS := $(filter-out %/clock_real.cpp.o,$(OBJS)) $(OUT)/platform/clock/clock_virtual.cpp.o
$(OUT)/platform/clock/clock_virtual.cpp.o: CXXFLAGS_ += -DFNVNX_PLATFORM_BUILD
$(OUT)/s4_tests_vclock: $(VC_OBJS) $(OUT)/libfreetype.a
	$(CXX) $(CXXFLAGS_) $(VC_OBJS) $(OUT)/libfreetype.a -o $@ -lm $(LDEXTRA)
vclock: $(OUT)/s4_tests_vclock
	rm -rf $(OUT)/root-vclock && FNVNX_TEST_ONLY=vc_ FNVNX_FONT_REF=$(FNVNX_FONT_REF) FNVNX_FONT_HOST=$(FNVNX_FONT_HOST) FNVNX_ROOT=$(OUT)/root-vclock $(OUT)/s4_tests_vclock

# ---- the same test objects linked with the product's file-mapping backend (platform/win32/filemap_private.cpp) instead of
# filemap_shared.cpp, plus the test-only host implementation of the section services it calls (host_section.cpp); runs the vmp_ cases
PRIV_OBJS := $(filter-out %/filemap_shared.cpp.o,$(OBJS)) $(OUT)/platform/win32/filemap_private.cpp.o $(OUT)/tests/s4/host_section.cpp.o
$(OUT)/platform/win32/filemap_private.cpp.o: CXXFLAGS_ += -DFNVNX_PLATFORM_BUILD
$(OUT)/s4_tests_private: $(PRIV_OBJS) $(OUT)/libfreetype.a
	$(CXX) $(CXXFLAGS_) $(PRIV_OBJS) $(OUT)/libfreetype.a -o $@ -lm $(LDEXTRA)
private: $(OUT)/s4_tests_private
	rm -rf $(OUT)/root-private && FNVNX_TEST_ONLY=vmp_ FNVNX_FONT_REF=$(FNVNX_FONT_REF) FNVNX_FONT_HOST=$(FNVNX_FONT_HOST) FNVNX_ROOT=$(OUT)/root-private $(OUT)/s4_tests_private

# every ext_call of the guest sources is a service of toolchain/include/fnvnx/hostapi.h with its declared argument count
hostapi-check:
	python3 -I $(ROOT)/platform/manifest/check_hostapi.py --root $(ROOT)

# every dynamic module/name lookup of the exe-derived private manifest against the platform's own answers; skips (rc 0)
# with a message when the private manifest is absent:  make -C tests/s4 dynsites-check [DYNSITES=<dynsites.tsv>]
DYNSITES ?= /home/user/fnv-nx-build/manifest/dynsites.tsv
dynsites-check: probe
	python3 -I $(ROOT)/platform/manifest/check_dynsites.py --selftest
	python3 -I $(ROOT)/platform/manifest/check_dynsites.py $(DYNSITES) --root $(ROOT)

# the registry-site extractor and the seed generator on synthetic input (no exe-derived file needed)
regsites-check:
	python3 -I $(ROOT)/platform/manifest/check_regsites.py
	python3 -I $(ROOT)/platform/manifest/check_gen_registry.py
	python3 -I $(ROOT)/platform/manifest/check_gen_prefs.py

test: hostapi-check regsites-check $(OUT)/s4_tests
	rm -rf $(OUT)/root && FNVNX_FONT_REF=$(FNVNX_FONT_REF) FNVNX_FONT_HOST=$(FNVNX_FONT_HOST) FNVNX_ROOT=$(OUT)/root GLIBC_TUNABLES=glibc.malloc.tcache_count=0 $(OUT)/s4_tests

# ---- i386 (-m32) build: the ILP32 data model with a host libc. /usr/include/asm is the multilib packages' symlink,
# missing when a cross compiler replaced them; the x86-64 asm headers serve i386 too.
M32OUT := $(OUT)-m32
M32FLAGS := -m32 -idirafter /usr/include/x86_64-linux-gnu $(FLAGS) -I$(ROOT)/shim/include
M32_CXX := $(PLAT_CXX) $(P)/com/combridge_exports.gen.cpp   # input and audio are in PLAT_CXX; the pump is m32/host_input.cpp
# the D3D9=null guest's D3D side (toolchain/game/build.sh): D3DX and the null device (d3d9.dll module)
M32_D3D := $(wildcard $(ROOT)/render/nine/d3dx/*.cpp) $(ROOT)/toolchain/game/null_d3d9.cpp
M32_TEST := $(wildcard $(HERE)/m32/*.cpp) $(HERE)/host_boundary.cpp
M32_OBJS := $(patsubst $(ROOT)/%,$(M32OUT)/%.o,$(PLAT_C) $(M32_CXX) $(M32_D3D) $(M32_TEST))
$(filter $(M32OUT)/platform/%.cpp.o,$(M32_OBJS)): M32FLAGS += -DFNVNX_PLATFORM_BUILD
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
# (ENVC_SRC and the generation rule: above)
$(foreach s,$(ENVC_SRC),$(eval $(M32OUT)/platform/$(s).cpp.o: $(P)/$(s).env_const.gen.inc))

$(M32OUT)/%.c.o: $(ROOT)/%.c
	@mkdir -p $(dir $@)
	$(CC) -std=gnu11 $(M32FLAGS) -c $< -o $@
$(M32OUT)/%.cpp.o: $(ROOT)/%.cpp
	@mkdir -p $(dir $@)
	$(CXX) -std=gnu++17 $(M32FLAGS) -c $< -o $@
$(M32OUT)/freetype/%.o: $(FT)/src/%.c $(FT)/include/ft2build.h
	@mkdir -p $(dir $@)
	$(CC) -m32 $(FT_CFLAGS) -c $< -o $@
$(M32OUT)/libfreetype.a: $(patsubst %,$(M32OUT)/freetype/%.o,$(FT_SRCS))
	rm -f $@ && ar rcs $@ $^
$(M32OUT)/s4_m32: $(M32_OBJS) $(M32OUT)/libfreetype.a
	$(CXX) -m32 -pthread $(M32_OBJS) $(M32OUT)/libfreetype.a -o $@ -lm
m32: $(M32OUT)/s4_m32
	rm -rf $(M32OUT)/root && FNVNX_ROOT=$(M32OUT)/root $(M32OUT)/s4_m32
iat: $(M32OUT)/s4_m32
	@test -n "$(EXE)" || { echo "set EXE=<FalloutNV.exe>"; exit 2; }
	python3 -I $(HERE)/iat/pe_imports.py "$(EXE)" > $(M32OUT)/imports.txt
	rm -rf $(M32OUT)/root && FNVNX_ROOT=$(M32OUT)/root $(M32OUT)/s4_m32 --iat $(M32OUT)/imports.txt

# ---- the host tests built static for AArch64 and run under qemu-aarch64 (the device's ISA: atomics, float, signals)
qemu:
	$(MAKE) --no-print-directory OUT=$(OUT)-a64 CC="aarch64-linux-gnu-gcc -static" CXX="aarch64-linux-gnu-g++ -static" \
		$(OUT)-a64/s4_tests
	rm -rf $(OUT)-a64/root && mkdir -p $(OUT)-a64/root && FNVNX_ROOT=$(OUT)-a64/root qemu-aarch64 $(OUT)-a64/s4_tests

# ---- device backends: the inventory (tests/s4/backends/backends.tsv, metric device-open) and the qemu-aarch64 tests
# of the host-side pieces. With EXE=, the inventory is checked against every import of the exe.
device:
	@if [ -n "$(EXE)" ]; then $(MAKE) --no-print-directory iat EXE="$(EXE)" > /dev/null; \
		python3 -I $(HERE)/backends/check_backends.py --imports $(M32OUT)/imports.txt --list; \
	else python3 -I $(HERE)/backends/check_backends.py --list; fi
	$(HERE)/backends/run_qemu.sh $(OUT)-device

# ---- the compiler enforces the boundary rule: a game code pointer (WNDPROC ...) is a fnvnx_guestfn under the platform macro,
# with no conversion to a native function pointer. nocompile/gfn_nocompile.cpp: CASE 0 (the call through the wrapper)
# must compile, CASE 1..4 (raw casts, implicit conversion, a native function stored as a game address) must be rejected.
# Not part of the default target: `make -C tests/s4 gfn-nocompile`.
gfn-nocompile:
	@$(CXX) $(CXXFLAGS_) -DFNVNX_PLATFORM_BUILD -DCASE=0 -fsyntax-only $(HERE)/nocompile/gfn_nocompile.cpp \
		|| { echo "gfn-nocompile: the control (CASE 0) does not compile"; exit 1; }
	@for c in 1 2 3 4; do \
		if $(CXX) $(CXXFLAGS_) -DFNVNX_PLATFORM_BUILD -DCASE=$$c -fsyntax-only $(HERE)/nocompile/gfn_nocompile.cpp >/dev/null 2>&1; \
		then echo "gfn-nocompile: CASE $$c COMPILED: a game code pointer can be called natively"; exit 1; fi; done; \
	echo "gfn-nocompile: ok (control compiles, 4 native-call forms rejected)"

# ---- the census of test doubles (platform/manifest/check_doubles.py; owner order 2026-11-11 night). Not part of the default
# target. Builds every object of every test binary (S4: s4_tests, vclock, private, probe_dynsites, m32; S7: its two) and every
# product unit for the guest (AArch64 ILP32, GOBJ), then checks (test-file symbols) ∩ (product symbols) = ∅.
#   make -C tests/s4 doubles [S4LOG=<log of make test> S7LOG=<log of make -C tests/s7 test>]
GOBJ := $(OUT)/guest-objs
TAB := $(shell printf '\t')
empty :=
space := $(empty) $(empty)
FNVNX_TC ?= /home/user/gd/toolchain/prefix
NEWLIB_INC ?= $(FNVNX_TC)/aarch64-none-elf/include
LIBNX_INC ?= $(ROOT)/ext/libnx/nx/include
PRODUCT_UNITS := $(shell python3 -I $(P)/manifest/check_doubles.py --root $(ROOT) --list-units)
GUESTF := -DFNVNX_PLATFORM_BUILD -mabi=ilp32 -frounding-math -fshort-wchar -I$(P)/include -iquote $(ROOT)/shim/include -I$(ROOT)/shim/include -I$(P)/crt -I$(ROOT)/ext/minimp3 -I$(FT)/include -I$(ROOT)/loader/include -I$(ROOT)/toolchain/include
# a guest object is named like build_guest.sh names it: the root-relative path with '/' -> '_'
define GUEST_RULE
$(GOBJ)/$(subst /,_,$(1)).o: $(ROOT)/$(1) | $(ENV_GEN) $(COMOUT)
	@mkdir -p $(GOBJ)
	$$(if $$(filter %.c,$(1)),$$(XCC) -std=gnu11,$$(XCXX) -std=gnu++17) $(GUESTF) $$(if $$(filter %/sock_guest.c,$(1)),-D__SWITCH__ -I$(NEWLIB_INC) -I$(LIBNX_INC),) -c $$< -o $$@
endef
$(foreach u,$(PRODUCT_UNITS),$(eval $(call GUEST_RULE,$(u))))
GUEST_OBJS := $(foreach u,$(PRODUCT_UNITS),$(GOBJ)/$(subst /,_,$(u)).o)
# units that need the guest toolchain's newlib headers are skipped when it is absent (their symbols are then reported UNKNOWN)
GUEST_OK := $(filter-out $(if $(wildcard $(NEWLIB_INC)/reent.h),,$(GOBJ)/platform_os_sock_guest.c.o),$(GUEST_OBJS))
$(OUT)/linkmaps.tsv: $(OUT)/mtq/linkmap.tsv $(OBJS) $(VC_OBJS) $(PRIV_OBJS) $(DYN_OBJS) $(M32_OBJS)
	@mkdir -p $(OUT)
	@{ printf 's4_tests$(TAB)%s\n' "$(subst $(space),$(TAB),$(OBJS))"; \
	   printf 's4_tests_vclock$(TAB)%s\n' "$(subst $(space),$(TAB),$(VC_OBJS))"; \
	   printf 's4_tests_private$(TAB)%s\n' "$(subst $(space),$(TAB),$(PRIV_OBJS))"; \
	   printf 'probe_dynsites$(TAB)%s\n' "$(subst $(space),$(TAB),$(DYN_OBJS))"; \
	   printf 's4_m32$(TAB)%s\n' "$(subst $(space),$(TAB),$(M32_OBJS))"; \
	   $(MAKE) --no-print-directory -s -C $(HERE)/../s7 linkmap; \
	   $(MAKE) --no-print-directory -s -C $(HERE)/sockwire linkmap OUT=$(HERE)/sockwire/build; \
	   cat $(OUT)/mtq/linkmap.tsv; } > $@
# the mtqemu guest (tests/s15/mtqemu/build.sh): its objects are built there; the script writes its link map before the link, which
# does not complete today (os_world_* are undefined in guest.elf), hence the leading '-'
$(OUT)/mtq/linkmap.tsv:
	-bash $(ROOT)/tests/s15/mtqemu/build.sh $(OUT)/mtq >$(OUT)/mtq.log 2>&1
	@test -f $@
doubles: $(OUT)/linkmaps.tsv $(GUEST_OK)
	python3 -I $(P)/manifest/check_doubles.py --root $(ROOT) --linkmaps $(OUT)/linkmaps.tsv --guest-objs $(GOBJ) --tsv $(OUT)/doubles.tsv \
		$(if $(S4LOG),--test-log s4_tests=$(S4LOG)) $(if $(S7LOG),--test-log s7_tests=$(S7LOG))
.PHONY: doubles

# ---- AArch64 ILP32 syntax check (the game's data model)
XCC ?= aarch64-linux-gnu-gcc
XCXX ?= aarch64-linux-gnu-g++
LIBNX ?= $(ROOT)/ext/libnx
XFLAGS := -DFNVNX_PLATFORM_BUILD -mabi=ilp32 -fsyntax-only -frounding-math -Wall -Wextra -Wno-unused-parameter -fshort-wchar \
          -I$(P)/include -iquote $(ROOT)/shim/include -I$(ROOT)/shim/include -I$(P)/crt -I$(ROOT)/ext/minimp3 -I$(FT)/include -I$(ROOT)/loader/include -I$(ROOT)/toolchain/include
ilp32: $(ENV_GEN)
	@set -e; for f in $(PLAT_C); do echo "ilp32 cc  $${f#$(ROOT)/}"; $(XCC) -std=gnu11 $(XFLAGS) $$f; done
	@set -e; for f in $(PLAT_CXX); do echo "ilp32 c++ $${f#$(ROOT)/}"; $(XCXX) -std=gnu++17 $(XFLAGS) $$f; done
	@echo "ilp32 c++ platform/clock/clock_virtual.cpp"; $(XCXX) -std=gnu++17 $(XFLAGS) $(P)/clock/clock_virtual.cpp
	@echo "ilp32 cc  platform/os/os_horizon.c (libnx headers, newlib lock stub)"
	@$(XCC) -std=gnu11 $(XFLAGS) -D__SWITCH__ -I$(HERE)/ilp32-stubs -I$(LIBNX)/nx/include $(P)/os/os_horizon.c
	@echo "ilp32: all platform sources parse"

# BSD socket services, guest side <-> host side over loopback (sockwire/Makefile; not part of the default target)
sockwire:
	$(MAKE) -C $(HERE)/sockwire
.PHONY: sockwire

clean:
	rm -rf $(OUT) $(M32OUT) $(OUT)-device $(OUT)-a64
