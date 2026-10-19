// SPDX-License-Identifier: MIT
#include <switch.h>
#include <stdio.h>
#include <string.h>
#include <stdatomic.h>
#include <errno.h>
#include <sys/stat.h>
#include "rescue.h"
#include "fileops.h"
#include "bridge.h"
#include "launch_job.h"
#include "boot_guard.h"
#include "bulk.h"
#include "stream.h"
static unsigned char launch_stack[0x4000] __attribute__((aligned(4096)));
static Thread launch_thread;
static void launch_loop(void *unused){(void)unused;for(;;){launch_execute();svcSleepThread(1000000);}}
/* Different page-aligned buffers and completion state per USB interface. */
static unsigned char rx[2][4096] __attribute__((aligned(4096)));
static unsigned char tx[2][4096] __attribute__((aligned(4096)));
static unsigned char bridge_stack[0x10000] __attribute__((aligned(4096)));
static Thread bridge_thread;
static bool boot_recovered;
static unsigned char bulk_buf[0x20000] __attribute__((aligned(4096)));
u32 __nx_applet_type=AppletType_None;
u32 __nx_fs_num_sessions=1;
static Result init_result;static bool sm_ready,fs_ready,sd_ready;
void __libnx_initheap(void){static u8 heap[0x200000];extern void *fake_heap_start,*fake_heap_end;fake_heap_start=heap;fake_heap_end=heap+sizeof(heap);}
void __appInit(void){
    init_result=smInitialize();if(R_FAILED(init_result))return;sm_ready=true;
    init_result=setsysInitialize();if(R_FAILED(init_result))return;
    SetSysFirmwareVersion fw;init_result=setsysGetFirmwareVersion(&fw);setsysExit();if(R_FAILED(init_result))return;
    hosversionSet(MAKEHOSVERSION(fw.major,fw.minor,fw.micro));
    init_result=fsInitialize();if(R_FAILED(init_result))return;fs_ready=true;sd_ready=fsdevMountSdmc()==0;
}
void __appExit(void){if(sd_ready)fsdevUnmountAll();if(fs_ready)fsExit();if(sm_ready)smExit();}
static void log_stage(const char *stage,Result rc){
    if(!sd_ready)return;
    mkdir("sdmc:/config/usb-rescue",0700);
    const char *path="sdmc:/config/usb-rescue/status.txt";
    struct stat st;if(!stat(path,&st) && st.st_size>1024*1024)rename(path,"sdmc:/config/usb-rescue/status.previous.txt");
    FILE *f=fopen(path,"a");
    if(f){fprintf(f,"tick=%llu stage=%s result=%08X\n",(unsigned long long)armGetSystemTick(),stage,rc);fclose(f);}
}
static bool usb_configured(void){UsbState state;return R_SUCCEEDED(usbDsGetState(&state)) && state==UsbState_Configured;}
/* After an op80/81 reply exactly bulk_size raw bytes cross the Rescue channel in large transfers, directly
 * between USB and the SD card. Inbound bytes are always consumed (a write error is reported in the final
 * frame); outbound bytes are zero-filled after a read error (the host checks SHA-256). Returns true if tx
 * holds the final frame of an upload. */
static bool bulk_pump(unsigned char *tx){
    unsigned direction=bulk_direction;uint64_t left=bulk_size;bulk_direction=0;
    u64 started=armGetSystemTick();int e=0;
    while(left){
        size_t want=left<sizeof(bulk_buf)?(size_t)left:sizeof(bulk_buf);
        if(direction==STREAM_IN){
            size_t got=usbCommsReadEx(bulk_buf,want,1);
            if(!got){if(!usb_configured()){e=EIO;break;}svcSleepThread(1000000);continue;}
            int w=bulk_write(bulk_buf,got);if(w && !e)e=w;
            left-=got;
        }else{
            long n=e?0:bulk_read(bulk_buf,want);
            if(n<=0){if(!e)e=n<0?(int)-n:ESTALE;memset(bulk_buf,0,want);n=(long)want;}
            if(usbCommsWriteEx(bulk_buf,(size_t)n,1)!=(size_t)n){e=EIO;break;}
            left-=(uint64_t)n;
        }
    }
    char tag[96];
    snprintf(tag,sizeof(tag),"bulk-%s-%llu-bytes-%llu-ms",direction==STREAM_IN?"in":"out",(unsigned long long)bulk_size,
             (unsigned long long)(armTicksToNs(armGetSystemTick()-started)/1000000ULL));
    log_stage(tag,e);
    if(direction!=STREAM_IN){bulk_close_read();return false;}
    if(left){bulk_recover();if(!e)e=EIO;}
    else{int c=bulk_commit();if(!e)e=c;}
    memset(tx,0,4096);uint32_t h[]={RESCUE_MAGIC,(uint32_t)e,0,0};memcpy(tx,h,16);
    log_stage("bulk-commit",e);
    return true;
}
static void channel_loop(unsigned channel,bool socket_ready){
    int state=0;u32 id=0,size=0;Result rc;
    u64 last_sample=0,last_heartbeat=0;int previous_usb=-1;unsigned requests=0;
    for(;;){
        /* Telemetry runs on the forwarding thread, never the rescue path. */
        u64 now=armGetSystemTick();
        if(channel==0 && armTicksToNs(now-last_sample)>=1000000000ULL){
            last_sample=now;UsbState usb_state;Result probe=usbDsGetState(&usb_state);
            int observed=R_SUCCEEDED(probe)?(int)usb_state:-1;
            if(observed!=previous_usb){char tag[48];snprintf(tag,sizeof(tag),"usb-state-%d",observed);log_stage(tag,probe);previous_usb=observed;}
            if(armTicksToNs(now-last_heartbeat)>=60000000000ULL){
                char tag[64];snprintf(tag,sizeof(tag),"alive-transfer-%d-requests-%u",state,requests);log_stage(tag,0);last_heartbeat=now;
            }
        }
        if(state==0){rc=usbCommsReadAsync(rx[channel],4096,&id,channel);if(R_SUCCEEDED(rc))state=1;}
        else if(state==1 && R_SUCCEEDED(eventWait(usbCommsGetReadCompletionEvent(channel),0))){
            rc=usbCommsGetReadResult(id,&size,channel);
            if(R_SUCCEEDED(rc) && size==4096){
                requests++;
                if(channel==1)rescue_dispatch(rx[channel],tx[channel]);
                else if(socket_ready)bridge_exchange(rx[channel],tx[channel]);
                else {memset(tx[channel],0,4096);uint32_t h[]={RESCUE_MAGIC,ENETDOWN,0,0};memcpy(tx[channel],h,16);}
                state=2;
            }else state=0;
        }else if(state==2){rc=usbCommsWriteAsync(tx[channel],4096,&id,channel);if(R_SUCCEEDED(rc))state=3;}
        else if(state==3 && R_SUCCEEDED(eventWait(usbCommsGetWriteCompletionEvent(channel),0))){
            usbCommsGetWriteResult(id,&size,channel);
            state=channel==1 && bulk_direction && bulk_pump(tx[channel])?2:0;
        }
        svcSleepThread(1000000);
    }
}
static void features(void *unused){
    (void)unused;
    if(boot_recovered){uint64_t pid=0;worker_start(&pid);}
    const SocketInitConfig cfg={.tcp_tx_buf_size=0x4000,.tcp_rx_buf_size=0x4000,.udp_tx_buf_size=0x1000,.udp_rx_buf_size=0x1000,.sb_efficiency=2,.num_bsd_sessions=2,.bsd_service_type=BsdServiceType_System};
    Result rc=socketInitialize(&cfg);
    channel_loop(0,R_SUCCEEDED(rc));
}
int main(void){
    if(R_FAILED(init_result))return 1;
    /* Worker has no boot2.flag: only rescue starts it after recovery. */
    int recovery=sidecar_recover();boot_recovered=sd_ready && recovery==0;
    log_stage("transaction-recovery",recovery);
    if(sd_ready)log_stage("bulk-recovery",bulk_recover());
    /* Boot guard runs before USB and before the features thread can start Worker. */
    if(sd_ready){uint32_t g[6];int guard=guard_boot();guard_status(g);
        char tag[64];snprintf(tag,sizeof(tag),"boot-guard-armed-%u-boots-%u-last-reverted-%u",g[0],g[1],g[5]);log_stage(tag,guard);}
    UsbCommsInterfaceInfo infos[2]={{0xff,0x52,0},{0xff,0x52,1}};
    Result rc;unsigned tries=0;
    while(R_FAILED(rc=usbCommsInitializeEx(2,infos,0x057e,0x3000))){
        if(++tries==1 || tries%60==0)log_stage("usb-retry",rc);
        svcSleepThread(1000000000);
    }
    log_stage("usb-ready",0);
    rc=threadCreate(&bridge_thread,features,NULL,bridge_stack,sizeof(bridge_stack),45,3);
    if(R_SUCCEEDED(rc))rc=threadStart(&bridge_thread);
    log_stage("bridge-thread",rc);
    rc=threadCreate(&launch_thread,launch_loop,NULL,launch_stack,sizeof(launch_stack),45,3);
    if(R_SUCCEEDED(rc))rc=threadStart(&launch_thread);
    if(R_SUCCEEDED(rc))launch_set_ready();
    log_stage("launch-thread",rc);
    /* Rescue stays available even if the feature thread fails to start. */
    channel_loop(1,false);return 0;
}
