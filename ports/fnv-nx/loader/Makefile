# Host build of the data-image loader and the ILP32 compile check.
#   make                 -> $(OUT)/libfnvnx_loader.a, $(OUT)/loadcheck (sanitizers on)
#   make ilp32-check     -> compiles the portable sources for aarch64 -mabi=ilp32 with -Werror
OUT     ?= ../build/loader
CC      ?= cc
CFLAGS  ?= -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer
WARN     = -std=c99 -Wall -Wextra -Wshadow -Wconversion -Wno-sign-conversion -Werror
ILP32_CC ?= aarch64-linux-gnu-gcc

SRC  = src/pe.c ../lib/sha256/sha256.c src/image.c src/imports.c src/codeptr.c src/deldtor.c src/trap.c
HOST = host/fnvnx_host.c
INC  = -Iinclude -Isrc -Ihost -I../lib/sha256/include

all: $(OUT)/libfnvnx_loader.a $(OUT)/loadcheck

$(OUT)/sha256.o: ../lib/sha256/sha256.c ../lib/sha256/include/fnvnx_sha256.h | $(OUT)
	$(CC) $(CFLAGS) $(WARN) $(INC) -c $< -o $@

$(OUT)/%.o: src/%.c include/fnvnx_loader.h src/internal.h | $(OUT)
	$(CC) $(CFLAGS) $(WARN) $(INC) -c $< -o $@

OBJ  = $(addprefix $(OUT)/,$(notdir $(SRC:.c=.o)))

$(OUT)/libfnvnx_loader.a: $(OBJ)
	ar rcs $@ $^

$(OUT)/loadcheck: host/loadcheck.c $(HOST) host/fnvnx_host.h $(OUT)/libfnvnx_loader.a
	$(CC) $(CFLAGS) -std=c99 -Wall -Wextra -Werror $(INC) host/loadcheck.c $(HOST) $(OUT)/libfnvnx_loader.a -o $@

ilp32-check: | $(OUT)
	@for f in $(SRC); do \
	  $(ILP32_CC) -mabi=ilp32 -O2 $(WARN) -ffreestanding $(INC) -c $$f -o $(OUT)/ilp32-$$(basename $$f .c).o || exit 1; \
	done
	@echo "ilp32-check: ok ($(words $(SRC)) files)"

$(OUT):
	mkdir -p $(OUT)

clean:
	rm -rf $(OUT)

.PHONY: all ilp32-check clean
