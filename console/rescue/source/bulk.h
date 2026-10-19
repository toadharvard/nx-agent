// SPDX-License-Identifier: MIT
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
/* Direct USB <-> SD file transfers on the Rescue channel (no Worker, no TCP). One transaction at a time. */
#define BULK_MAX 0xffffffffULL
bool bulk_path(const char *path, bool write);
int bulk_hash(const char *path, unsigned char hash[32], uint64_t *size);
int bulk_begin(const char *path, uint64_t size, const unsigned char old_hash[32], const unsigned char new_hash[32], bool exists);
int bulk_write(const void *data, size_t size);
int bulk_commit(void);
int bulk_recover(void);
int bulk_open_read(const char *path, uint64_t *size, unsigned char hash[32]);
long bulk_read(void *data, size_t size);
void bulk_close_read(void);
