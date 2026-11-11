# Host build of the data-image loader and the ILP32 compile check.
#   make                 -> $(OUT)/libfnvnx_loader.a (sanitizers on)
#   make ilp32-check     -> compiles the portable sources for aarch64 -mabi=ilp32 with -Werror
OUT     ?= ../build/loader
CC      ?= cc
CFLAGS  ?= -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer
WARN     = -std=c99 -Wall -Wextra -Wshadow -Wconversion -Wno-sign-conversion -Werror
ILP32_CC ?= aarch64-linux-gnu-gcc

SRC  = src/pe.c src/sha256.c src/image.c src/imports.c src/identity.c src/tls.c src/start.c src/modules.c
INC  = -Iinclude -Isrc

all: $(OUT)/libfnvnx_loader.a

$(OUT)/%.o: src/%.c include/fnvnx_loader.h src/internal.h src/start_internal.h include/fnvnx_start.h include/fnvnx_modules.h | $(OUT)
	$(CC) $(CFLAGS) $(WARN) $(INC) -c $< -o $@

$(OUT)/libfnvnx_loader.a: $(SRC:src/%.c=$(OUT)/%.o)
	ar rcs $@ $^

ilp32-check: | $(OUT)
	@for f in $(SRC); do \
	  $(ILP32_CC) -mabi=ilp32 -O2 $(WARN) -ffreestanding $(INC) -c $$f -o $(OUT)/ilp32-$$(basename $$f .c).o || exit 1; \
	done
	@echo "ilp32-check: ok ($(words $(SRC)) files)"

$(OUT):
	mkdir -p $(OUT)

clean:
	rm -rf $(OUT)

# Start sequence test (rules S1..S8): synthetic PE from synth/synth_start.py; the real exe (private, only its path
# is passed) is added when EXE exists.
EXE ?= /home/user/l1data/gamedata_exe/FalloutNV.exe
$(OUT)/synth_start.pe $(OUT)/synth_start.h: synth/synth_start.py | $(OUT)
	python3 -I synth/synth_start.py $(OUT)/synth_start.pe $(OUT)/synth_start.h

$(OUT)/startcheck: host/startcheck.c $(OUT)/synth_start.h $(OUT)/libfnvnx_loader.a include/fnvnx_start.h
	$(CC) $(CFLAGS) -std=c99 -Wall -Wextra -Werror $(INC) -I$(OUT) host/startcheck.c $(OUT)/libfnvnx_loader.a -o $@

startcheck: $(OUT)/startcheck $(OUT)/synth_start.pe
	$(OUT)/startcheck $(OUT)/synth_start.pe $(if $(wildcard $(EXE)),--exe $(EXE))

.PHONY: all ilp32-check clean startcheck

# The import-key lookup compiles for ILP32 (the generated table is checked by contract/tests/test_iat_keys.py).
ilp32-importkey:
	$(ILP32_CC) -mabi=ilp32 -O2 $(WARN) -ffreestanding -Iinclude -c src/importkey.c -o /dev/null
.PHONY: ilp32-importkey
