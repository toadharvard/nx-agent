// SPDX-License-Identifier: MIT
#include "rescue.h"
#include "fileops.h"
#include "launch_job.h"
#include "boot_guard.h"
#include <string.h>
#include <stdio.h>
#include <errno.h>
#include <stdatomic.h>

static int stopped(void) {
    LaunchStatus job;launch_status(&job);if(job.state)return EBUSY;
    uint64_t pid=0;uint32_t rc=worker_query(&pid);
    return rc==PM_NOT_FOUND?0:(rc?(int)rc:EBUSY);
}
/* No worker-controlled code or synchronous IPC to the worker is used here.
 * features: 1 boot guard, 2 raw stream bridge on interface 0. */
void rescue_dispatch(const unsigned char *rx,unsigned char *tx) {
    uint32_t in[4],out[4]={RESCUE_MAGIC,0,0,0};memcpy(in,rx,16);
    memset(tx,0,4096);int e=0,n=0;unsigned op=in[1],len=in[2];
    if(in[0]!=RESCUE_MAGIC || len>4080)e=EINVAL;
    else if(op==6 && !len){uint32_t s[]={7,0x52534355,1,0};memcpy(tx+16,s,16);n=16;}
    else if(op==9 && !len){uint32_t s[]={7,launch_ready()?1U:0U,16*1024*1024,0,sidecar_pending(),3};memcpy(tx+16,s,24);n=24;}
    else if(op==4 && !len){} /* Legacy client cleanup: local no-op. */
    else if(op==60 && !len){uint64_t pid=0;uint32_t rc=worker_query(&pid);memcpy(tx+16,&rc,4);memcpy(tx+20,&pid,8);n=12;}
    else if(op==61 && len==4 && !memcmp(rx+16,"STOP",4)){e=(int)worker_stop();if(e==PM_NOT_FOUND)e=0;}
    else if(op==62 && len==5 && !memcmp(rx+16,"START",5)){
        if(sidecar_pending())e=EBUSY;
        else {uint64_t pid=0;e=(int)worker_start(&pid);memcpy(tx+16,&pid,8);n=e?0:8;}
    }
    else if(op==64 && len==5 && !memcmp(rx+16,"START",5)){
        if(sidecar_pending())e=EBUSY;
        else {uint64_t id=0;e=launch_submit(&id);memcpy(tx+16,&id,8);n=e?0:8;}
    }
    else if(op==65 && !len){LaunchStatus job;launch_status(&job);memcpy(tx+16,&job,sizeof(job));n=sizeof(job);}
    else if(op==66 && len==8){uint64_t id;memcpy(&id,rx+16,8);e=launch_ack(id);}
    else if(op==70)e=guard_arm(rx+16,len);
    else if(op==71){e=len?EINVAL:guard_confirm();}
    else if(op==72 && !len){uint32_t s[6];guard_status(s);memcpy(tx+16,s,24);n=24;}
    else if(op==17 || op==18) {
        unsigned prefix=op==17?8:0;
        if(len<=prefix || len-prefix>=768 || memchr(rx+16+prefix,0,len-prefix))e=EINVAL;
        else {
            char path[768];memcpy(path,rx+16+prefix,len-prefix);path[len-prefix]=0;
            if(!sidecar_path(path,false))e=EACCES;
            else if(op==18){uint64_t size=0;e=sidecar_hash(path,tx+24,&size);memcpy(tx+16,&size,8);n=e?0:40;}
            else {uint64_t off;memcpy(&off,rx+16,8);
                if(off>0x7fffffffULL)e=EFBIG;
                else {FILE *f=fopen(path,"rb");if(!f)e=errno;
                    else {if(fseeko(f,(off_t)off,SEEK_SET))e=errno;
                        else {n=fread(tx+16,1,4080,f);if(ferror(f))e=EIO;}fclose(f);}}
            }
        }
    }
    else if(op==32){
        if(len<=73 || len-73>=768 || rx[16]>1 || memchr(rx+89,0,len-73))e=EINVAL;
        else {char path[768];uint64_t size;memcpy(&size,rx+17,8);memcpy(path,rx+89,len-73);path[len-73]=0;
            if((e=stopped())==0)e=sidecar_begin(path,size,rx+25,rx+57,rx[16]);}
    }
    else if(op==33){if(len<8)e=EINVAL;else {uint64_t off;memcpy(&off,rx+16,8);e=sidecar_chunk(off,rx+24,len-8);}}
    else if(op==34){if(len)e=EINVAL;else if((e=stopped())==0)e=sidecar_commit();}
    else if(op==35){if(len)e=EINVAL;else if((e=stopped())==0)e=sidecar_abort();}
    else e=ENOSYS;
    out[1]=(uint32_t)e;out[2]=e?0:(uint32_t)n;memcpy(tx,out,16);
}
