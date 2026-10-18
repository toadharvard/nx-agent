// SPDX-License-Identifier: MIT
#pragma once
#include <stddef.h>
#include <stdint.h>
int sidecar_control(unsigned op,const unsigned char *data,unsigned len,unsigned char *out,int *error);
void sidecar_control_tick(void);
void sidecar_control_close(void);
int sidecar_control_boot(void);
void sidecar_control_shutdown(void);
void worker_log(const char *stage,uint32_t rc);
void sidecar_note_host(void);
