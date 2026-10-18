// SPDX-License-Identifier: MIT
#include <assert.h>
#include <errno.h>
#include <unistd.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <stdio.h>
#include "listener.h"
static int connect_peer(int fd){
 struct sockaddr_in a;socklen_t n=sizeof(a);assert(!getsockname(fd,(void *)&a,&n));
 int p=socket(AF_INET,SOCK_STREAM,0);assert(p>=0);assert(!connect(p,(void *)&a,n));return p;
}
static int wait_peer(WorkerListener *l,uint64_t now,int *e){
 for(int i=0;i<100;i++){int p=worker_listener_poll(l,now,e);if(p>=0 || *e)return p;usleep(1000);}return -1;
}
int main(void){
 WorkerListener l={.fd=-1,.port=0};int e;
 assert(worker_listener_poll(&l,0,&e)==-1 && !e && l.fd>=0);
 int c=connect_peer(l.fd),p=wait_peer(&l,1,&e);assert(p>=0 && !e);close(p);close(c);
 /* Simulate a listener invalidated while the process remains alive. */
 close(l.fd);assert(worker_listener_poll(&l,10,&e)==-1 && e==EBADF && l.fd==-1);
 assert(worker_listener_poll(&l,1009,&e)==-1 && !e && l.fd==-1);
 assert(worker_listener_poll(&l,1010,&e)==-1 && !e && l.fd>=0);
 c=connect_peer(l.fd);p=wait_peer(&l,1011,&e);assert(p>=0 && !e);close(p);close(c);
 /* Bind failure is also retried without exiting the worker. */
 struct sockaddr_in a;socklen_t n=sizeof(a);assert(!getsockname(l.fd,(void *)&a,&n));
 WorkerListener busy={.fd=-1,.port=ntohs(a.sin_port)};
 assert(worker_listener_poll(&busy,0,&e)==-1 && e==EADDRINUSE && busy.fd==-1);
 worker_listener_close(&l);
 assert(worker_listener_poll(&busy,1000,&e)==-1 && !e && busy.fd>=0);
 c=connect_peer(busy.fd);p=wait_peer(&busy,1001,&e);assert(p>=0 && !e);close(p);close(c);worker_listener_close(&busy);
 puts("PASS: accept, invalidation, bounded retry, reconnect, bind failure recovery");
}
