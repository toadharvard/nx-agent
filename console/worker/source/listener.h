// SPDX-License-Identifier: MIT
#ifndef WORKER_LISTENER_H
#define WORKER_LISTENER_H
#include <stdint.h>
typedef struct { int fd; uint16_t port; uint64_t retry_at; } WorkerListener;
/* Returns a nonblocking peer, or -1. error=0 means ordinary idle/retry delay. */
int worker_listener_poll(WorkerListener *listener,uint64_t now_ms,int *error);
void worker_listener_close(WorkerListener *listener);
/* Self-connect check; on failure the listener is rebuilt by the next poll. */
int worker_listener_probe(WorkerListener *listener);
#endif
