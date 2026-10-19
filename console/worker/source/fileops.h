// SPDX-License-Identifier: MIT
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
bool sidecar_path(const char *path, bool write);
bool sidecar_tree_path(const char *path);
int sidecar_rename(const char *from, const char *to);
int sidecar_unlink(const char *path);
int sidecar_rmdir(const char *path);
int sidecar_mkdir(const char *path);
int sidecar_hash(const char *path, unsigned char hash[32], uint64_t *size);
int sidecar_begin(const char *path, uint64_t size, const unsigned char old_hash[32], const unsigned char new_hash[32], bool exists);
int sidecar_chunk(uint64_t offset, const void *data, size_t size);
int sidecar_commit(void);
int sidecar_abort(void);
int sidecar_recover(void);
bool sidecar_pending(void);
