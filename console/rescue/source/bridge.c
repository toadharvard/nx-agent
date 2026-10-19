// SPDX-License-Identifier: MIT
/* A bounded, non-replaying local transport, never called on the rescue thread. */
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <poll.h>
#include <time.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#include "bridge.h"
#include "rescue.h"
static int peer=-1;
static int64_t now_ms(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (int64_t)t.tv_sec*1000+t.tv_nsec/1000000;}
static int await_io(short events,int64_t deadline){
    while(1){int64_t left=deadline-now_ms();if(left<=0)return ETIMEDOUT;
        struct pollfd p={.fd=peer,.events=events};int r=poll(&p,1,(int)left);
        if(r<0){if(errno==EINTR)continue;return errno;}
        if(!r)return ETIMEDOUT;
        if(p.revents&events)return 0;
        return ECONNRESET;
    }
}
static int exchange(const unsigned char *rx,unsigned char *tx){
    int64_t end=now_ms()+1400;int e=0;
    if(peer<0){
        peer=socket(AF_INET,SOCK_STREAM,0);if(peer<0)return errno;
        if(fcntl(peer,F_SETFL,O_NONBLOCK)<0)return errno;
#ifdef SO_NOSIGPIPE
        int one=1;setsockopt(peer,SOL_SOCKET,SO_NOSIGPIPE,&one,sizeof(one));
#endif
        struct sockaddr_in addr={.sin_family=AF_INET,.sin_port=htons(22226)};addr.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
        if(connect(peer,(struct sockaddr *)&addr,sizeof(addr))<0){
            if(errno!=EINPROGRESS)return errno;
            if((e=await_io(POLLOUT,end)))return e;
            socklen_t size=sizeof(e);if(getsockopt(peer,SOL_SOCKET,SO_ERROR,&e,&size))return errno;
            if(e)return e;
        }
    }
    for(int phase=0;phase<2;phase++){
        size_t done=0;while(done<4096){
            if((e=await_io(phase?POLLIN:POLLOUT,end)))return e;
            ssize_t n=phase?recv(peer,tx+done,4096-done,0):send(peer,rx+done,4096-done,0);
            if(n>0)done+=n;
            else if(!n)return ECONNRESET;
            else if(errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR)return errno;
        }
    }
    uint32_t h[4];memcpy(h,tx,16);return h[0]!=RESCUE_MAGIC || h[2]>4080?EPROTO:0;
}
void bridge_exchange(const unsigned char *rx,unsigned char *tx){
    int e=exchange(rx,tx);if(!e)return;
    if(peer>=0)close(peer);
    peer=-1;memset(tx,0,4096);uint32_t h[]={RESCUE_MAGIC,(uint32_t)e,0,0};memcpy(tx,h,16);
}
