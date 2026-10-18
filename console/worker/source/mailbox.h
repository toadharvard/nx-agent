// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>
/* Fixed 128-byte requests. Host must never retry an uncertain sequence. */
uint64_t mailbox_checksum(const unsigned char *p);
int mailbox_validate(const unsigned char *p, uint64_t token, uint64_t last, uint64_t now);
int mailbox_tick(uint64_t now);
