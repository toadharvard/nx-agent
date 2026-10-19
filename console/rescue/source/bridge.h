// SPDX-License-Identifier: MIT
#pragma once
void bridge_exchange(const unsigned char *rx,unsigned char *tx);
#include <stddef.h>
#include <stdint.h>
int bridge_send(const unsigned char *data,size_t size,int idle_ms);
int bridge_recv(unsigned char *data,size_t max,size_t *got,int idle_ms);
void bridge_final(unsigned char *tx,int error,int64_t timeout_ms);
