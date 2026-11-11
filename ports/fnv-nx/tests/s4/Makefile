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
         -I$(P)/include -iquote $(ROOT)/shim/include -I$(ROOT)/shim/include -I$(P)/crt -I$(ROOT)/ext/minimp3 -I$(FT)/include
CFLAGS_ := -std=gnu11 $(FLAGS)
CXXFLAGS_ := -std=gnu++17 $(FLAGS)

PLAT_C := $(P)/os/os_posix.c $(wildcard $(P)/crt/*.c)
PLAT_CXX := $(P)/com/combridge.cpp $(P)/com/combridge.gen.cpp $(filter-out %/filemap_private.cpp,$(wildcard $(P)/win32/*.cpp)) $(wildcard $(P)/crt/*.cpp) $(wildcard $(P)/audio/*.cpp)   # audio: winmm waveOut* are exports of the table
TEST_CXX := $(filter-out %/private_residue_probe.cpp,$(wildcard $(HERE)/*.cpp))   # the probe belongs to the private binary only

OBJS := $(patsubst $(ROOT)/%,$(OUT)/%.o,$(PLAT_C) $(PLAT_CXX) $(TEST_CXX))

# The platform's own sources see the game's callbacks (WNDPROC, ...) as fnvnx_guestfn (shim/include/fnvnx_callback.h:
# a call goes through the boundary, a direct call does not compile); the tests are "the game" and keep plain pointers.
$(filter $(OUT)/platform/%.cpp.o,$(OBJS)): CXXFLAGS_ += -DFNVNX_PLATFORM_BUILD

.PHONY: all test private ilp32 m32 iat device qemu clean gfn-nocompile
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
$(OUT)-m32/platform/win32/env_const.cpp.o: $(P)/win32/env_const.gen.inc
$(OUT)-m32/platform/crt/msvcrt_exports.cpp.o: $(P)/crt/env_const.gen.inc
$(OUT)-m32/platform/os/os_posix.c.o: $(P)/os/env_const_posix.gen.inc

$(OUT)/%.cpp.o: $(ROOT)/%.cpp
	@mkdir -p $(dir $@)
	$(CXX) $(CXXFLAGS_) -c $< -o $@

# the per-source constant-result bodies of platform/audio and platform/input, and the COM bridge generated from
# platform/com/com_spec.json: a fresh checkout builds with the default target
ENVC_SRC := audio/dshow audio/dshow_filters audio/dsound audio/waveout input/dinput
$(P)/%.env_const.gen.inc: $(P)/manifest/env_const.tsv $(P)/manifest/gen_env_const.py
	python3 -I $(P)/manifest/gen_env_const.py $(P)/manifest/env_const.tsv $@ --only $*.cpp
$(foreach s,$(ENVC_SRC),$(eval $(OUT)/platform/$(s).cpp.o: $(P)/$(s).env_const.gen.inc))
COMGEN := $(P)/com/gen_combridge.py $(P)/com/com_spec.json
COMOUT := $(P)/com/combridge.gen.cpp $(P)/com/combridge_exports.gen.cpp $(P)/com/combridge_if.gen.h $(P)/com/combridge_rows.tsv
$(COMOUT) &: $(COMGEN)
	python3 -I $(P)/com/gen_combridge.py $(P)/com/com_spec.json $(P)/com

$(OUT)/s4_tests: $(OBJS) $(OUT)/libfreetype.a
	$(CXX) $(CXXFLAGS_) $(OBJS) $(OUT)/libfreetype.a -o $@ -lm $(LDEXTRA)

# ---- the same test objects linked with the product's file-mapping backend (platform/win32/filemap_private.cpp) instead of
# filemap_shared.cpp, plus the test-only definition of its residue (private_residue_probe.cpp); runs the vmp_ cases
PRIV_OBJS := $(filter-out %/filemap_shared.cpp.o,$(OBJS)) $(OUT)/platform/win32/filemap_private.cpp.o $(OUT)/tests/s4/private_residue_probe.cpp.o
$(OUT)/platform/win32/filemap_private.cpp.o: CXXFLAGS_ += -DFNVNX_PLATFORM_BUILD
$(OUT)/s4_tests_private: $(PRIV_OBJS) $(OUT)/libfreetype.a
	$(CXX) $(CXXFLAGS_) $(PRIV_OBJS) $(OUT)/libfreetype.a -o $@ -lm $(LDEXTRA)
private: $(OUT)/s4_tests_private
	rm -rf $(OUT)/root-private && FNVNX_TEST_ONLY=vmp_ FNVNX_FONT_REF=$(FNVNX_FONT_REF) FNVNX_FONT_HOST=$(FNVNX_FONT_HOST) FNVNX_ROOT=$(OUT)/root-private $(OUT)/s4_tests_private

test: $(OUT)/s4_tests
	rm -rf $(OUT)/root && FNVNX_FONT_REF=$(FNVNX_FONT_REF) FNVNX_FONT_HOST=$(FNVNX_FONT_HOST) FNVNX_ROOT=$(OUT)/root GLIBC_TUNABLES=glibc.malloc.tcache_count=0 $(OUT)/s4_tests

# ---- i386 (-m32) build: the ILP32 data model with a host libc. /usr/include/asm is the multilib packages' symlink,
# missing when a cross compiler replaced them; the x86-64 asm headers serve i386 too.
M32OUT := $(OUT)-m32
M32FLAGS := -m32 -idirafter /usr/include/x86_64-linux-gnu $(FLAGS) -I$(ROOT)/shim/include
M32_CXX := $(PLAT_CXX) $(P)/com/combridge_exports.gen.cpp $(wildcard $(P)/audio/*.cpp) $(filter-out %/input_horizon.cpp,$(wildcard $(P)/input/*.cpp))   # input_horizon.cpp: libnx only; m32/host_input.cpp pumps
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

# ---- AArch64 ILP32 syntax check (the game's data model)
XCC ?= aarch64-linux-gnu-gcc
XCXX ?= aarch64-linux-gnu-g++
LIBNX ?= $(ROOT)/ext/libnx
XFLAGS := -DFNVNX_PLATFORM_BUILD -mabi=ilp32 -fsyntax-only -frounding-math -Wall -Wextra -Wno-unused-parameter -fshort-wchar \
          -I$(P)/include -iquote $(ROOT)/shim/include -I$(ROOT)/shim/include -I$(P)/crt -I$(ROOT)/ext/minimp3 -I$(FT)/include
ilp32: $(ENV_GEN)
	@set -e; for f in $(PLAT_C); do echo "ilp32 cc  $${f#$(ROOT)/}"; $(XCC) -std=gnu11 $(XFLAGS) $$f; done
	@set -e; for f in $(PLAT_CXX); do echo "ilp32 c++ $${f#$(ROOT)/}"; $(XCXX) -std=gnu++17 $(XFLAGS) $$f; done
	@echo "ilp32 cc  platform/os/os_horizon.c (libnx headers, newlib lock stub)"
	@$(XCC) -std=gnu11 $(XFLAGS) -D__SWITCH__ -I$(HERE)/ilp32-stubs -I$(LIBNX)/nx/include $(P)/os/os_horizon.c
	@echo "ilp32: all platform sources parse"

# BSD socket services, guest side <-> host side over loopback (sockwire/Makefile; not part of the default target)
sockwire:
	$(MAKE) -C $(HERE)/sockwire
.PHONY: sockwire

clean:
	rm -rf $(OUT) $(M32OUT) $(OUT)-device $(OUT)-a64
