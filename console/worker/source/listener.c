// SPDX-License-Identifier: MIT
#include "listener.h"
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <poll.h>
void worker_listener_close(WorkerListener *l){if(l->fd>=0)close(l->fd);l->fd=-1;}
int worker_listener_poll(WorkerListener *l,uint64_t now,int *error){
 *error=0;
 if(l->fd<0){
  if(now<l->retry_at)return -1;
  l->fd=socket(AF_INET,SOCK_STREAM,0);
  if(l->fd<0)goto failed;
  int yes=1;
  if(setsockopt(l->fd,SOL_SOCKET,SO_REUSEADDR,&yes,sizeof(yes)))goto failed;
  struct sockaddr_in addr={.sin_family=AF_INET,.sin_port=htons(l->port)};
  addr.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
  if(bind(l->fd,(struct sockaddr *)&addr,sizeof(addr)) || listen(l->fd,1) || fcntl(l->fd,F_SETFL,O_NONBLOCK)<0)goto failed;
 }
 int peer=accept(l->fd,0,0);
 if(peer<0){
  if(errno==EAGAIN || errno==EWOULDBLOCK || errno==EINTR || errno==ECONNABORTED)return -1;
  goto failed;
 }
 if(fcntl(peer,F_SETFL,O_NONBLOCK)<0){*error=errno;close(peer);return -1;}
 return peer;
failed:
 *error=errno;worker_listener_close(l);l->retry_at=now+1000;return -1;
}
int worker_listener_probe(WorkerListener *l){
 if(l->fd<0)return 0;
 int s=socket(AF_INET,SOCK_STREAM,0);if(s<0)return errno;
 int e=0;
 struct sockaddr_in addr={.sin_family=AF_INET,.sin_port=htons(l->port)};
 addr.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
 if(fcntl(s,F_SETFL,O_NONBLOCK)<0)e=errno;
 else if(connect(s,(struct sockaddr *)&addr,sizeof(addr))<0){
  if(errno!=EINPROGRESS)e=errno;
  else {
   struct pollfd p={.fd=s,.events=POLLOUT};int r=poll(&p,1,100);
   if(r<0)e=errno;else if(!r)e=ETIMEDOUT;
   else {socklen_t size=sizeof(e);if(getsockopt(s,SOL_SOCKET,SO_ERROR,&e,&size))e=errno;}
  }
 }
 close(s);
 if(e){worker_listener_close(l);l->retry_at=0;}
 return e;
}
