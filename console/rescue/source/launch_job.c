// SPDX-License-Identifier: MIT
#include "launch_job.h"
#include "rescue.h"
#include <stdatomic.h>
#include <errno.h>
/* USB thread submits/acknowledges; dedicated thread executes blocking IPC. */
static atomic_uint state;
static atomic_bool ready;
void launch_set_ready(void){atomic_store(&ready,1);}
int launch_ready(void){return atomic_load(&ready);}
static uint32_t result;
static uint64_t pid,serial;
int launch_submit(uint64_t *id){
 if(!launch_ready())return ENOSYS;
 if(atomic_load_explicit(&state,memory_order_acquire))return EBUSY;
 result=0;pid=0;serial++;*id=serial;
 atomic_store_explicit(&state,1,memory_order_release);return 0;
}
void launch_status(LaunchStatus*out){
 out->state=atomic_load_explicit(&state,memory_order_acquire);out->id=serial;
 out->result=out->state==3?result:0;out->pid=out->state==3?pid:0;
}
int launch_ack(uint64_t id){
 if(atomic_load_explicit(&state,memory_order_acquire)!=3||id!=serial)return EBUSY;
 atomic_store_explicit(&state,0,memory_order_release);return 0;
}
void launch_execute(void){
 unsigned expected=1;
 if(!atomic_compare_exchange_strong_explicit(&state,&expected,2,memory_order_acq_rel,memory_order_acquire))return;
 result=worker_start(&pid);
 atomic_store_explicit(&state,3,memory_order_release);
}
