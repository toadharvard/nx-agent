// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>
/* A Worker reply with reserved word 3 == STREAM_MAGIC switches the Rescue bridge to raw bytes for one transfer.
   Reply data: u32 direction, u32 0, u64 length (then u8 sha256[32] for STREAM_OUT).
   STREAM_IN: the host sends exactly length bytes, then reads one final 4096-byte frame.
   STREAM_OUT: the console sends exactly length bytes (zero-padded if the Worker fails); no final frame. */
#define STREAM_MAGIC 0x4d525453u
#define STREAM_IN 1u
#define STREAM_OUT 2u
#define STREAM_MAX 0xffffffffULL
