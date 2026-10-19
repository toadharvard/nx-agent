OUT ?= .
CFLAGS ?= -O2
override CFLAGS += -Wall -Wextra $(shell pkg-config --cflags libmtp)
LDLIBS += $(shell pkg-config --libs libmtp)

all: $(OUT)/mtp_fs

$(OUT)/%: %.c
	$(CC) $(CFLAGS) -o $@ $< $(LDLIBS)
