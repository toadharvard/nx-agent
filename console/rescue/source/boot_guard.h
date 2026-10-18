// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>
#define GUARD_DIR "sdmc:/config/usb-rescue"
#define GUARD_JOURNAL GUARD_DIR "/boot-guard.bin"
#define GUARD_LAST GUARD_DIR "/boot-guard.last"
#define GUARD_THRESHOLD 2
#define GUARD_MAX_ENTRIES 8
/* op70: u32 count + count*{char from[200], char to[200]}; each entry reverts by renaming from -> to. */
int guard_arm(const unsigned char *data,unsigned len);
int guard_confirm(void);
void guard_status(uint32_t out[6]);
/* Rescue start, before Worker: count the boot, revert at threshold. */
int guard_boot(void);
