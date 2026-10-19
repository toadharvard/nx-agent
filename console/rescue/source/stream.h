// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>
/* A Rescue reply to op80/op81 with reserved word 3 == STREAM_MAGIC is followed by raw bytes on the Rescue
   channel. Reply data: u32 direction, u32 0, u64 length (then u8 sha256[32] for STREAM_OUT).
   STREAM_IN: the host sends exactly length bytes, then reads one final 4096-byte frame (the commit result).
   STREAM_OUT: the console sends exactly length bytes (zero-filled after a read error); no final frame. */
#define STREAM_MAGIC 0x4d525453u
#define STREAM_IN 1u
#define STREAM_OUT 2u
