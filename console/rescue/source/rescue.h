// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>
#define RESCUE_MAGIC 0x31425255U
#define WORKER_ID 0x420000000000D06BULL
#define PM_NOT_FOUND 0x20fU
#define WORKER_PATH "sdmc:/atmosphere/contents/420000000000D06B/exefs.nsp"
#define GOOD_PATH "sdmc:/config/usb-rescue/known-good.nsp"
void rescue_dispatch(const unsigned char *in,unsigned char *out);
/* Set by ops 80/81: raw bytes follow the reply on the Rescue channel. */
extern unsigned bulk_direction;
extern uint64_t bulk_size;
uint32_t worker_query(uint64_t *pid);
uint32_t worker_stop(void);
uint32_t worker_start(uint64_t *pid);
