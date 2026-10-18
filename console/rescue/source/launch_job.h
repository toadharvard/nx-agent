// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>
typedef struct {uint32_t state,result;uint64_t pid,id;} LaunchStatus;
void launch_set_ready(void);
int launch_ready(void);
int launch_submit(uint64_t *id);
void launch_status(LaunchStatus *out);
int launch_ack(uint64_t id);
void launch_execute(void);
