// SPDX-License-Identifier: MIT
#include <stdint.h>
#include "rescue.h"
static uint32_t query_result=PM_NOT_FOUND;
void mock_set_running(int yes){query_result=yes?0:PM_NOT_FOUND;}
void mock_set_query_result(uint32_t rc){query_result=rc;}
uint32_t worker_query(uint64_t *pid){*pid=query_result?0:123;return query_result;}
uint32_t worker_stop(void){query_result=PM_NOT_FOUND;return 0;}
uint32_t worker_start(uint64_t *pid){if(!query_result)return 0x40f;query_result=0;*pid=123;return 0;}
