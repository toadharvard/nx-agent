OUT ?= .
CFLAGS += -O2 -Wall $(shell pkg-config --cflags libmtp)
LDLIBS += $(shell pkg-config --libs libmtp)

all: $(OUT)/mtp_read $(OUT)/mtp_mailbox_submit

$(OUT)/%: %.c
	$(CC) $(CFLAGS) -o $@ $< $(LDLIBS)
