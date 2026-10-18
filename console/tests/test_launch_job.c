// SPDX-License-Identifier: MIT
#include "../rescue/source/launch_job.h"
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <unistd.h>
static atomic_int entered,release_call,calls;
uint32_t worker_start(uint64_t*p){atomic_fetch_add(&calls,1);atomic_store(&entered,1);while(!atomic_load(&release_call))usleep(1000);*p=321;return 0;}
static void*run(void*x){(void)x;launch_execute();return NULL;}
int main(void){uint64_t id,other;LaunchStatus s;assert(launch_submit(&id)==ENOSYS);launch_set_ready();assert(!launch_submit(&id));pthread_t t;pthread_create(&t,0,run,0);while(!atomic_load(&entered))usleep(1000);
 for(int i=0;i<1000;i++){launch_status(&s);assert(s.state==2&&s.id==id);assert(launch_submit(&other)==EBUSY);assert(launch_ack(id)==EBUSY);}
 atomic_store(&release_call,1);pthread_join(t,0);launch_status(&s);assert(s.state==3&&s.pid==321&&s.result==0);assert(launch_submit(&other)==EBUSY);assert(launch_ack(id+1)==EBUSY);assert(!launch_ack(id));launch_execute();assert(atomic_load(&calls)==1);puts("PASS hung launch permits 1000 status polls; no duplicate launch; result retained until exact ack");}
