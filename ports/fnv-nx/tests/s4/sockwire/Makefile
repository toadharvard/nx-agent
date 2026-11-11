# Round trip of the BSD socket services (hostapi.h FNVNX_PLAT_SOCK_*) on the Linux host:
#   guest side  platform/os/sock_guest.c, compiled against the toolchain's newlib + libnx headers (-nostdinc, the
#               guest's view of the types and constants), its functions renamed gt_* so they do not clash with glibc
#   host side   toolchain/host/host_sock.inc, compiled against glibc (host_glue.c)
#   between     ext_call is a plain function call into the host's dispatcher (host_glue.c); guest_shim.c models
#               libsysbase's descriptor table (handle_manager.c) and errno for the guest-view objects
# make -C tests/s4 sockwire   (FNVNX_TC: toolchain prefix, default /home/user/gd/toolchain/prefix)
# LP64 only: the ILP32 layout of the guest side is covered by the static asserts of fnvnx/sockwire.h, not by this test.
HERE := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
ROOT := $(abspath $(HERE)/../../..)
FNVNX_TC ?= /home/user/gd/toolchain/prefix
NEWLIB_INC ?= $(FNVNX_TC)/aarch64-none-elf/include
LIBNX_INC ?= $(abspath $(FNVNX_TC)/../dkp/libnx/include)
OUT ?= $(HERE)/build
CC ?= gcc
GCCINC := $(shell $(CC) -print-file-name=include)
RENAMES := socket bind listen accept connect send sendto recv recvfrom \
           setsockopt getsockopt poll fcntl close dup read write
GUESTCF := -std=gnu11 -O1 -g -Wall -Wextra -Wno-unused-parameter -m64 -nostdinc -isystem $(GCCINC) \
           -isystem $(NEWLIB_INC) -idirafter $(LIBNX_INC) -D__LINUX_ERRNO_EXTENSIONS__ -DFNVNX_HOST_CALLS \
           -I$(ROOT)/toolchain/include $(foreach n,$(RENAMES),-D$(n)=gt_$(n))
HOSTCF := -std=gnu11 -O1 -g -Wall -Wextra -Wno-unused-parameter -pthread -I$(ROOT)/toolchain/include -I$(ROOT)/toolchain/host
INCS := $(wildcard $(ROOT)/toolchain/include/fnvnx/*.h)

sockwire: $(OUT)/sockwire_test
	$(OUT)/sockwire_test

$(OUT)/sockwire_test: $(OUT)/sock_guest.o $(OUT)/guest_shim.o $(OUT)/sockwire_test.o $(OUT)/host_glue.o
	$(CC) -pthread $^ -o $@

$(OUT)/sock_guest.o: $(ROOT)/platform/os/sock_guest.c $(INCS)
	@mkdir -p $(OUT) && $(CC) $(GUESTCF) -c $< -o $@
$(OUT)/guest_shim.o: $(HERE)/guest_shim.c $(INCS)
	@mkdir -p $(OUT) && $(CC) $(GUESTCF) -c $< -o $@
$(OUT)/sockwire_test.o: $(HERE)/sockwire_test.c $(INCS)
	@mkdir -p $(OUT) && $(CC) $(GUESTCF) -c $< -o $@
$(OUT)/host_glue.o: $(HERE)/host_glue.c $(ROOT)/toolchain/host/host_sock.inc $(INCS)
	@mkdir -p $(OUT) && $(CC) $(HOSTCF) -c $< -o $@

clean:
	rm -rf $(OUT)
.PHONY: sockwire clean
