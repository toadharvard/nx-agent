// SPDX-License-Identifier: MIT
#include <switch.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/stat.h>
#include <ctype.h>
#include <fcntl.h>
#include "control.h"
#include "fileops.h"
#include "logic.h"

static unsigned char hid_work[0x1000] __attribute__((aligned(4096)));
/* pads[0]: JoyRight1 (rail pair) or FullKey3; pads[1]: JoyLeft2 (rail pair only). */
static HiddbgHdlsHandle pads[2];
static HiddbgHdlsSessionId session;
static bool hid_ready,work_ready,pad_ready,left_ready;
/* HOS 21 drops a FullKey3/Bluetooth HDLS device (2202-3602) unless hid assigns it an npad; rail merges into handheld. */
static unsigned controller_mode;
static Result left_result,fullkey_result;
static u64 release_tick,pad_activity_tick;
#define PAD_LIFETIME_NS 600000000000ULL
static PadStep seq_steps[SEQ_MAX_STEPS];
static unsigned seq_count,seq_index;
static bool seq_active;
static u64 seq_step_end;
static const s32 zero_sticks[4];
static unsigned char *jpeg;
static u64 jpeg_size,jpeg_tick;
static Result last_control_result;
static bool reboot_pending,r2c_pending,reboot_by_pmic;
static u64 reboot_tick;
static u64 power_release_tick,sleep_lease_tick;
static bool sleep_lease_active;
static SetSysSleepSettings sleep_original;
#define SLEEP_JOURNAL "sdmc:/config/usb-sidecar/sleep-lease.bin"
#define SLEEP_WAKES_AT_POWER_STATE_CHANGE 2
typedef struct {u32 magic;SetSysSleepSettings value;} SleepJournal;
static PscPmModule psc;
static bool psc_ready;
static u32 wake_alarm_s;
static Service alarm_srv,alarm_obj;
static bool alarm_ready,alarm_armed;
#define PSC_MODULE_ID ((PscPmModuleId)0x7b)

static Result get_sleep(SetSysSleepSettings *value) {
    Result rc=setsysInitialize();if(R_FAILED(rc))return rc;
    rc=setsysGetSleepSettings(value);setsysExit();return rc;
}
/* idle:sys command 4 applies the stored policy; command 5 resets idle time.
 * Settings readback alone does not prove that the running idle policy changed. */
static Result idle_command(u32 command) {
    Service idle={0};Result rc=smGetService(&idle,"idle:sys");
    if(R_FAILED(rc))return rc;
    rc=serviceDispatch(&idle,command);serviceClose(&idle);return rc;
}
static u64 idle_report_tick;
static int lease_arm(u32 seconds);
static Result set_sleep(const SetSysSleepSettings *value) {
    Result rc=setsysInitialize();if(R_FAILED(rc))return rc;
    rc=setsysSetSleepSettings(value);setsysExit();
    if(R_SUCCEEDED(rc))rc=idle_command(4);
    return rc;
}
static int restore_sleep(void) {
    FILE *f=fopen(SLEEP_JOURNAL,"rb");if(!f)return errno==ENOENT?0:errno;
    SleepJournal j;size_t n=fread(&j,1,sizeof(j),f);fclose(f);
    if(n!=sizeof(j) || j.magic!=0x534c5035)return EINVAL;
    SetSysSleepSettings cur;Result rc=get_sleep(&cur);if(R_FAILED(rc))return (int)rc;
    if(memcmp(&cur,&j.value,sizeof(cur))) {
        /* v7 leases also set WakesAtPowerStateChange; v6 journals did not. */
        bool leased=cur.flags==(j.value.flags|SLEEP_WAKES_AT_POWER_STATE_CHANGE) || cur.flags==j.value.flags;
        if(cur.handheld_sleep_plan!=5 || cur.console_sleep_plan!=5 || !leased)return ESTALE;
        rc=set_sleep(&j.value);if(R_FAILED(rc))return (int)rc;
    }
    rc=idle_command(4);if(R_FAILED(rc))return (int)rc;
    if(unlink(SLEEP_JOURNAL))return errno;
    sleep_lease_active=false;sleep_lease_tick=0;return 0;
}

static Result set_one(HiddbgHdlsHandle h,u64 buttons,s32 lx,s32 ly,s32 rx,s32 ry) {
    HiddbgHdlsState s={.battery_level=4,.flags=1,.buttons=buttons,.analog_stick_l={lx,ly},.analog_stick_r={rx,ry}};
    return hiddbgSetHdlsState(h,&s);
}
static Result set_state(u64 buttons,const s32 sticks[4]) {
    if(!pad_ready)return 0;
    if(controller_mode==2)return set_one(pads[0],buttons,sticks[0],sticks[1],sticks[2],sticks[3]);
    PadSide right,left;pad_route(buttons,sticks,&right,&left);
    Result rc=set_one(pads[0],right.buttons,0,0,right.x,right.y);
    if(R_FAILED(rc))return rc;
    if(left_ready)return set_one(pads[1],left.buttons,left.x,left.y,0,0);
    return left.buttons || left.x || left.y?(left_result?left_result:EIO):0;
}
static void close_pad(void) {
    if(power_release_tick){hiddbgUnsetSleepButtonAutoPilotState();power_release_tick=0;}
    set_state(0,zero_sticks);release_tick=0;
    if(left_ready)hiddbgDetachHdlsVirtualDevice(pads[1]);
    if(pad_ready)hiddbgDetachHdlsVirtualDevice(pads[0]);
    if(work_ready)hiddbgReleaseHdlsWorkBuffer(session);
    if(hid_ready)hiddbgExit();
    pad_ready=left_ready=work_ready=hid_ready=false;
}
static void detach_all(void){seq_active=false;close_pad();}
static Result hdls_begin(void) {
    Result rc=0;
    if(!hid_ready){rc=hiddbgInitialize();if(R_FAILED(rc))return rc;hid_ready=true;}
    if(!work_ready){rc=hiddbgAttachHdlsWorkBuffer(&session,hid_work,sizeof(hid_work));if(R_FAILED(rc))return rc;work_ready=true;}
    return 0;
}
static Result attach(HiddbgHdlsHandle *h,u8 type,u8 iface) {
    HiddbgHdlsDeviceInfo info={.deviceType=type,.npadInterfaceType=iface,
        .singleColorBody=0x404040ff,.singleColorButtons=0x808080ff};
    return hiddbgAttachHdlsVirtualDevice(h,&info);
}
static Result open_rail(void) {
    Result rc=hdls_begin();
    if(R_SUCCEEDED(rc))rc=attach(&pads[0],HidDeviceType_JoyRight1,HidNpadInterfaceType_Rail);
    if(R_FAILED(rc)){close_pad();return rc;}
    pad_ready=true;controller_mode=0;
    rc=set_one(pads[0],0,0,0,0,0);
    if(R_FAILED(rc)){close_pad();return rc;}
    /* Left half is best effort: right-only buttons keep working if HOS refuses it. */
    left_result=attach(&pads[1],HidDeviceType_JoyLeft2,HidNpadInterfaceType_Rail);
    left_ready=R_SUCCEEDED(left_result);
    if(left_ready && R_FAILED(left_result=set_one(pads[1],0,0,0,0,0))){hiddbgDetachHdlsVirtualDevice(pads[1]);left_ready=false;}
    if(R_FAILED(left_result))last_control_result=left_result;
    return 0;
}
static const HidNpadIdType npad_ids[]={HidNpadIdType_No1,HidNpadIdType_No2,HidNpadIdType_No3,HidNpadIdType_No4,
    HidNpadIdType_No5,HidNpadIdType_No6,HidNpadIdType_No7,HidNpadIdType_No8,HidNpadIdType_Handheld};
#define NPAD_COUNT (sizeof(npad_ids)/sizeof(npad_ids[0]))
/* Dedicated FullKey3 is used only after hid assigns it a new npad and accepts a neutral state; else rail pair. */
static Result open_fullkey(void) {
    u32 before[NPAD_COUNT];
    fullkey_result=hidInitialize();
    if(R_SUCCEEDED(fullkey_result)) {
        for(size_t i=0;i<NPAD_COUNT;i++)before[i]=hidGetNpadStyleSet(npad_ids[i]);
        fullkey_result=hdls_begin();
        if(R_SUCCEEDED(fullkey_result))fullkey_result=attach(&pads[0],HidDeviceType_FullKey3,HidNpadInterfaceType_Bluetooth);
        if(R_SUCCEEDED(fullkey_result)) {
            pad_ready=true;controller_mode=2;bool found=false;
            for(int t=0;t<=100 && !found;t++) {
                if(t)svcSleepThread(10000000);
                for(size_t i=0;i<NPAD_COUNT;i++)if(!before[i] && hidGetNpadStyleSet(npad_ids[i]))found=true;
            }
            fullkey_result=found?set_one(pads[0],0,0,0,0,0):ETIMEDOUT;
        }
        hidExit();
    }
    if(R_SUCCEEDED(fullkey_result))return 0;
    last_control_result=fullkey_result;close_pad();return open_rail();
}
static Result open_mode(unsigned mode){return mode==2?open_fullkey():open_rail();}
static Result open_pad(void){return pad_ready?0:open_mode(controller_mode);}
/* Any SetHdlsState failure: detach, re-attach once, re-apply; still failing leaves the pad detached. */
static Result apply_state(u64 buttons,const s32 sticks[4]) {
    Result rc=open_pad();if(R_FAILED(rc))return rc;
    rc=set_state(buttons,sticks);
    if(R_FAILED(rc)) {
        unsigned mode=controller_mode;close_pad();
        rc=open_mode(mode);
        if(R_SUCCEEDED(rc))rc=set_state(buttons,sticks);
        if(R_FAILED(rc))close_pad();
    }
    return rc;
}

static Result alarm_dispatch(u32 command,const s64 *ns) {
    return ns?serviceDispatchIn(&alarm_obj,command,*ns):serviceDispatch(&alarm_obj,command);
}
static Result alarm_arm(u32 seconds) {
    Result rc=0;
    if(!alarm_ready) {
        rc=smGetService(&alarm_srv,"time:al");if(R_FAILED(rc))return rc;
        /* IAlarmService 0 CreateWakeupAlarm -> ISteadyClockAlarm; 1 Enable(TimeSpan ns), 2 Disable. */
        rc=serviceDispatch(&alarm_srv,0,.out_num_objects=1,.out_objects=&alarm_obj);
        if(R_FAILED(rc)){serviceClose(&alarm_srv);return rc;}
        alarm_ready=true;
    }
    s64 ns=(s64)seconds*1000000000LL;rc=alarm_dispatch(1,&ns);
    if(R_SUCCEEDED(rc))alarm_armed=true;
    return rc;
}
static Result alarm_disarm(void) {
    if(!alarm_armed)return 0;
    alarm_armed=false;return alarm_dispatch(2,NULL);
}
static void alarm_close(void) {
    alarm_disarm();
    if(alarm_ready){serviceClose(&alarm_obj);serviceClose(&alarm_srv);}
    alarm_ready=false;
}
static u32 wake_reason(Result *out) {
    u32 v=0xffffffffU;Result rc=bpcInitialize();
    if(R_SUCCEEDED(rc)){rc=serviceDispatchOut(bpcGetServiceSession(),2,v);bpcExit();}
    if(out)*out=rc;
    return R_SUCCEEDED(rc)?v:0xffffffffU;
}
static u32 last_wake_reason=0xffffffffU;
static Result psc_open(void) {
    if(psc_ready)return 0;
    Result rc=pscmInitialize();if(R_FAILED(rc))return rc;
    /* Depend on fs and time so this module sees ReadySleep before them. */
    const u32 deps[]={PscPmModuleId_Fs,PscPmModuleId_Time};
    rc=pscmGetPmModule(&psc,PSC_MODULE_ID,deps,2,true);
    if(R_FAILED(rc))rc=pscmGetPmModule(&psc,PSC_MODULE_ID,NULL,0,true);
    if(R_FAILED(rc)){pscmExit();return rc;}
    psc_ready=true;return 0;
}
static void psc_close(void) {
    if(!psc_ready)return;
    pscPmModuleFinalize(&psc);pscPmModuleClose(&psc);pscmExit();psc_ready=false;
}
/* Never blocks: poll once per tick, always acknowledge, record failures only. */
static void psc_tick(void) {
    if(!psc_ready || R_FAILED(eventWait(&psc.event,0)))return;
    PscPmState state;u32 flags;Result rc=pscPmModuleGetRequest(&psc,&state,&flags);
    if(R_FAILED(rc)){last_control_result=rc;return;}
    Result action=0;
    if(state==PscPmState_ReadySleep && wake_alarm_s)action=alarm_arm(wake_alarm_s);
    else if(state==PscPmState_ReadyAwaken || state==PscPmState_Awake) {
        if(alarm_armed)action=alarm_disarm();
        if(state==PscPmState_Awake){Result wr;last_wake_reason=wake_reason(&wr);if(R_SUCCEEDED(action))action=wr;}
    }
    rc=pscPmModuleAcknowledge(&psc,state);
    if(R_FAILED(action))last_control_result=action;
    if(R_FAILED(rc))last_control_result=rc;
    char stage[48];snprintf(stage,sizeof(stage),"psc-state-%u-alarm-%u",(unsigned)state,(unsigned)alarm_armed);
    worker_log(stage,R_FAILED(rc)?rc:action);
}

static Result i2c_read8(I2cSession *s,u8 reg,u8 *value) {
    Result rc=i2csessionSendAuto(s,&reg,1,I2cTransactionOption_Start);
    return R_SUCCEEDED(rc)?i2csessionReceiveAuto(s,value,1,I2cTransactionOption_All):rc;
}
static Result i2c_read16(I2cSession *s,u8 reg,u16 *value) {
    u8 b[2];Result rc=i2csessionSendAuto(s,&reg,1,I2cTransactionOption_Start);
    if(R_SUCCEEDED(rc))rc=i2csessionReceiveAuto(s,b,2,I2cTransactionOption_All);
    if(R_SUCCEEDED(rc))*value=b[0]|(b[1]<<8);
    return rc;
}
static Result power_telemetry(u8 *out) {
    static const u8 gauge[]={0x06,0x08,0x09,0x0a,0x0b,0x19};
    static const u8 charger[]={0x00,0x01,0x02,0x08,0x09};
    Result rc=i2cInitialize();if(R_FAILED(rc))return rc;
    I2cSession s;rc=i2cOpenSession(&s,I2cDevice_Max17050);
    if(R_SUCCEEDED(rc)) {
        for(int i=0;R_SUCCEEDED(rc) && i<6;i++){u16 v=0;rc=i2c_read16(&s,gauge[i],&v);memcpy(out+i*2,&v,2);}
        i2csessionClose(&s);
    }
    if(R_SUCCEEDED(rc)) {
        rc=i2cOpenSession(&s,I2cDevice_Bq24193);
        if(R_SUCCEEDED(rc)) {
            for(int i=0;R_SUCCEEDED(rc) && i<5;i++)rc=i2c_read8(&s,charger[i],out+12+i);
            i2csessionClose(&s);
        }
    }
    i2cExit();return rc;
}
static Result charger_hiz(bool enable,u8 *reg0) {
    Result rc=i2cInitialize();if(R_FAILED(rc))return rc;
    I2cSession s;rc=i2cOpenSession(&s,I2cDevice_Bq24193);
    if(R_SUCCEEDED(rc)) {
        u8 v;rc=i2c_read8(&s,0x00,&v);
        if(R_SUCCEEDED(rc)) {
            u8 b[2]={0x00,enable?(u8)(v|0x80):(u8)(v&0x7f)};
            rc=i2csessionSendAuto(&s,b,2,I2cTransactionOption_All);
        }
        if(R_SUCCEEDED(rc))rc=i2c_read8(&s,0x00,reg0);
        i2csessionClose(&s);
    }
    i2cExit();return rc;
}
static Result rtc_write(I2cSession *s,u8 reg,u8 value) {
    u8 b[2]={reg,value};return i2csessionSendAuto(s,b,2,I2cTransactionOption_All);
}
static Result rtc_read(I2cSession *s,u8 reg,u8 *value) {
    Result rc=i2csessionSendAuto(s,&reg,1,I2cTransactionOption_Start);
    return R_SUCCEEDED(rc)?i2csessionReceiveAuto(s,value,1,I2cTransactionOption_All):rc;
}
/* MAX77620 RTC: RBUDR read-back via RTCUPDATE0 (0x04), clear the alarm enable bit 7 in ALARM1/ALARM2
   (0x0E-0x1B), store the word in RTCYEARA1 (0x13)/RTCYEARA2 (0x1A) and magic in RTCDOWA1 (0x11)/RTCDOWA2 (0x18),
   commit with UDR, then read back. */
static Result r2c_write(u8 val1,u8 val2) {
    Result rc=i2cInitialize();if(R_FAILED(rc))return rc;
    I2cSession s;rc=i2cOpenSession(&s,I2cDevice_Max77620Rtc);
    if(R_FAILED(rc)){i2cExit();return rc;}
    bool magic_written=false;
    rc=rtc_write(&s,0x04,0x10);svcSleepThread(16000000);
    for(u8 reg=0x0e;R_SUCCEEDED(rc) && reg<=0x1b;reg++) {
        u8 v;rc=rtc_read(&s,reg,&v);
        if(R_SUCCEEDED(rc))rc=rtc_write(&s,reg,v&0x7f);
    }
    if(R_SUCCEEDED(rc))rc=rtc_write(&s,0x13,val1);
    if(R_SUCCEEDED(rc))rc=rtc_write(&s,0x1a,val2);
    if(R_SUCCEEDED(rc)){magic_written=true;rc=rtc_write(&s,0x11,0x77);}
    if(R_SUCCEEDED(rc))rc=rtc_write(&s,0x18,0x77);
    if(R_SUCCEEDED(rc)){rc=rtc_write(&s,0x04,0x01);svcSleepThread(16000000);}
    if(R_SUCCEEDED(rc)){rc=rtc_write(&s,0x04,0x10);svcSleepThread(16000000);}
    const u8 regs[]={0x11,0x18,0x13,0x1a},want[]={0x77,0x77,val1,val2};
    for(int i=0;R_SUCCEEDED(rc) && i<4;i++){u8 v;rc=rtc_read(&s,regs[i],&v);if(R_SUCCEEDED(rc) && v!=want[i])rc=EIO;}
    /* Never leave a half-written reboot reason for the next normal boot. */
    if(R_FAILED(rc) && magic_written){rtc_write(&s,0x11,0);rtc_write(&s,0x18,0);rtc_write(&s,0x04,0x01);svcSleepThread(16000000);}
    i2csessionClose(&s);i2cExit();return rc;
}

#define AUTOHOLD_FLAG "sdmc:/config/usb-sidecar/autohold"
static bool autohold;
static u64 autohold_retry_tick;
/* seconds==0 holds without expiry (autohold). An existing journal is adopted: it keeps the true original. */
static int lease_arm(u32 seconds) {
    Result rc;
    if(!sleep_lease_active) {
        FILE *f=fopen(SLEEP_JOURNAL,"rb");
        if(f) {
            SleepJournal j;size_t n=fread(&j,1,sizeof(j),f);fclose(f);
            if(n!=sizeof(j) || j.magic!=0x534c5035)return EINVAL;
            sleep_original=j.value;
        } else {
            rc=get_sleep(&sleep_original);if(R_FAILED(rc))return rc;
            if(mkdir("sdmc:/config/usb-sidecar",0700) && errno!=EEXIST)return errno;
            int fd=open(SLEEP_JOURNAL,O_WRONLY|O_CREAT|O_EXCL,0600);if(fd<0)return errno;
            SleepJournal j={.magic=0x534c5035,.value=sleep_original};
            bool ok=write(fd,&j,sizeof(j))==sizeof(j) && !fsync(fd);int saved=errno;close(fd);
            if(!ok)return saved?saved:EIO;
        }
        sleep_lease_active=true;
    }
    sleep_lease_tick=(autohold || !seconds)?UINT64_MAX:armGetSystemTick()+armNsToTicks((u64)seconds*1000000000ULL);
    SetSysSleepSettings value=sleep_original;value.handheld_sleep_plan=5;value.console_sleep_plan=5;
    value.flags|=SLEEP_WAKES_AT_POWER_STATE_CHANGE;
    rc=set_sleep(&value);
    if(R_SUCCEEDED(rc))rc=idle_command(5);
    if(R_SUCCEEDED(rc))idle_report_tick=armGetSystemTick()+armNsToTicks(10000000000ULL);
    return R_FAILED(rc)?(int)rc:0;
}
int sidecar_control_boot(void) {
    autohold=!access(AUTOHOLD_FLAG,F_OK);
    return autohold?lease_arm(0):restore_sleep();
}
static int set_autohold(bool on) {
    if(on) {
        if(mkdir("sdmc:/config/usb-sidecar",0700) && errno!=EEXIST)return errno;
        int fd=open(AUTOHOLD_FLAG,O_WRONLY|O_CREAT,0600);if(fd<0)return errno;
        int e=fsync(fd)?errno:0;close(fd);if(e)return e;
        autohold=true;return lease_arm(0);
    }
    if(unlink(AUTOHOLD_FLAG) && errno!=ENOENT)return errno;
    autohold=false;return restore_sleep();
}
#define ROLE_GUARD_OFF "sdmc:/config/usb-sidecar/role-guard.off"
static u64 role_check_tick,role_kick_tick,role_start_tick;
static u32 role_kicks;
static u8 role_streak,role_reg1;
static u64 role_host_tick;
void sidecar_note_host(void){role_host_tick=armGetSystemTick();}
static void role_guard_tick(u64 now) {
    if(now<role_check_tick)return;
    if(!role_start_tick)role_start_tick=now;
    if(armTicksToNs(now-role_start_tick)>90000000000ULL){role_check_tick=~0ULL;return;}
    role_check_tick=now+armNsToTicks(2000000000ULL);
    if(armTicksToNs(now-role_host_tick)<10000000000ULL){role_streak=0;return;}
    if(!access(ROLE_GUARD_OFF,F_OK)){role_streak=0;return;}
    if(R_FAILED(i2cInitialize()))return;
    I2cSession s;
    if(R_SUCCEEDED(i2cOpenSession(&s,I2cDevice_Bq24193))) {
        u8 v;
        if(R_SUCCEEDED(i2c_read8(&s,0x01,&v))) {
            role_reg1=v;
            role_streak=((v>>4)&3)>=2?(u8)(role_streak<255?role_streak+1:255):0;
            if(role_streak>=3 && now>=role_kick_tick) {
                u8 b[2]={0x01,(u8)((v&~0x30)|0x10)};
                if(R_SUCCEEDED(i2csessionSendAuto(&s,b,2,I2cTransactionOption_All))){role_kicks++;role_streak=0;}
                role_kick_tick=now+armNsToTicks(20000000000ULL);
            }
        }
        i2csessionClose(&s);
    }
    i2cExit();
}
void sidecar_control_shutdown(void){restore_sleep();detach_all();alarm_close();psc_close();}
void sidecar_control_tick(void) {
    u64 now=armGetSystemTick();
    psc_tick();
    role_guard_tick(now);
    if(power_release_tick && now>=power_release_tick){last_control_result=hiddbgUnsetSleepButtonAutoPilotState();power_release_tick=0;}
    if(sleep_lease_active && now>=sleep_lease_tick) {
        int e=restore_sleep();if(e){last_control_result=(Result)e;sleep_lease_tick=now+armNsToTicks(10000000000ULL);}
    }
    if(autohold && !sleep_lease_active && now>=autohold_retry_tick) {
        int e=lease_arm(0);if(e){last_control_result=(Result)e;autohold_retry_tick=now+armNsToTicks(1000000000ULL);}
    }
    if(sleep_lease_active && now<sleep_lease_tick && now>=idle_report_tick) {
        last_control_result=idle_command(5);
        idle_report_tick=now+armNsToTicks(10000000000ULL);
    }
    if(release_tick && now>=release_tick) {
        release_tick=0;Result rc=apply_state(0,zero_sticks);
        if(R_FAILED(rc))last_control_result=rc;
    }
    if(seq_active && now>=seq_step_end) {
        Result rc;
        if(++seq_index<seq_count) {
            const PadStep *s=&seq_steps[seq_index];rc=apply_state(s->buttons,s->sticks);
            seq_step_end=armGetSystemTick()+armNsToTicks((u64)s->ms*1000000);
        } else {seq_active=false;rc=apply_state(0,zero_sticks);}
        if(R_FAILED(rc)){seq_active=false;last_control_result=rc;}
    }
    if(pad_ready && armTicksToNs(now-pad_activity_tick)>PAD_LIFETIME_NS)detach_all();
    if(jpeg && armTicksToNs(now-jpeg_tick)>15000000000ULL){free(jpeg);jpeg=NULL;jpeg_size=0;}
    if(reboot_pending && now>=reboot_tick){reboot_pending=false;if(reboot_by_pmic){fsdevCommitDevice("sdmc");svcSleepThread(500000000);Result sr=splInitialize();if(R_SUCCEEDED(sr))sr=splSetConfig((SplConfigItem)65001,4);last_control_result=sr;}last_control_result=bpcRebootSystem();bpcExit();}
}
void sidecar_control_close(void){detach_all();free(jpeg);jpeg=NULL;jpeg_size=0;}
static u32 pad_idle_ms_left(void) {
    if(!pad_ready)return 0;
    u64 ms=armTicksToNs(armGetSystemTick()-pad_activity_tick)/1000000ULL;
    return ms>=PAD_LIFETIME_NS/1000000ULL?0:(u32)(PAD_LIFETIME_NS/1000000ULL-ms);
}
static Result schedule_reboot(void) {
    Result rc=bpcInitialize();
    if(R_SUCCEEDED(rc)){detach_all();reboot_pending=true;reboot_tick=armGetSystemTick()+armNsToTicks(1000000000);}
    return rc;
}
int sidecar_control(unsigned op,const unsigned char *data,unsigned len,unsigned char *out,int *error) {
    Result rc=0;int n=0;
    if(op==40 || op==41 || op==52)pad_activity_tick=armGetSystemTick();
    if(op==40) {
        /* 0 and legacy 1: rail pair; 2: dedicated FullKey3 with verified npad, else rail pair. */
        if(len>1 || (len==1 && data[0]>2)){*error=EINVAL;return 0;}
        if(seq_active){*error=EBUSY;return 0;}
        unsigned want=len && data[0]==2?2:0;fullkey_result=0;
        if(pad_ready && controller_mode!=want)close_pad();
        rc=pad_ready?0:open_mode(want);
        if(R_SUCCEEDED(rc)){u32 reply[2]={controller_mode,fullkey_result};memcpy(out,reply,8);n=8;}
    }
    else if(op==41) {
        /* u64 buttons, four s32 sticks, u32 duration_ms. Auto-release <=1s. */
        if(len!=28){*error=EINVAL;return 0;}
        PadStep s;if(pad_step_parse(data,1000,&s)){*error=EINVAL;return 0;}
        if(seq_active){*error=EBUSY;return 0;}
        rc=apply_state(s.buttons,s.sticks);
        if(R_SUCCEEDED(rc))release_tick=armGetSystemTick()+armNsToTicks((u64)s.ms*1000000);
    } else if(op==42) {if(len){*error=EINVAL;return 0;}detach_all();}
    else if(op==52) {
        unsigned count;PadStep steps[SEQ_MAX_STEPS];
        if(seq_parse(data,len,steps,&count)){*error=EINVAL;return 0;}
        if(!count) {
            if(seq_active){seq_active=false;rc=apply_state(0,zero_sticks);}
        } else if(seq_active){*error=EBUSY;return 0;}
        else {
            memcpy(seq_steps,steps,sizeof(PadStep)*count);seq_count=count;seq_index=0;release_tick=0;
            rc=apply_state(seq_steps[0].buttons,seq_steps[0].sticks);
            if(R_SUCCEEDED(rc)){seq_active=true;seq_step_end=armGetSystemTick()+armNsToTicks((u64)seq_steps[0].ms*1000000);}
        }
    }
    else if(op==43) {
        if(len>1 || (len==1 && (data[0]>6 || data[0]==5))){*error=EINVAL;return 0;}
        ViLayerStack layer=len?(ViLayerStack)data[0]:ViLayerStack_Default;
        free(jpeg);jpeg=NULL;jpeg_size=0;
        jpeg=aligned_alloc(0x1000,CAPSSC_JPEG_BUFFER_SIZE);
        if(!jpeg){*error=ENOMEM;return 0;}
        rc=capsscInitialize();
        if(R_SUCCEEDED(rc)) {
            rc=capsscCaptureJpegScreenShot(&jpeg_size,jpeg,CAPSSC_JPEG_BUFFER_SIZE,layer,100000000);
            capsscExit();
        }
        if(R_FAILED(rc) || jpeg_size>CAPSSC_JPEG_BUFFER_SIZE){free(jpeg);jpeg=NULL;jpeg_size=0;if(R_SUCCEEDED(rc))*error=EOVERFLOW;}
        else {memcpy(out,&jpeg_size,8);n=8;jpeg_tick=armGetSystemTick();}
    } else if(op==44) {
        if(len!=8){*error=EINVAL;return 0;}u64 off;memcpy(&off,data,8);
        if(!jpeg || off>jpeg_size){*error=EINVAL;return 0;}
        n=jpeg_size-off>4080?4080:(int)(jpeg_size-off);memcpy(out,jpeg+off,n);jpeg_tick=armGetSystemTick();
    } else if(op==45) {
        if(len){*error=EINVAL;return 0;}u64 pid=0,tid=0;
        rc=pmdmntInitialize();
        if(R_SUCCEEDED(rc)) {
            rc=pmdmntGetApplicationProcessId(&pid);
            if(R_SUCCEEDED(rc))rc=pmdmntGetProgramId(&tid,pid);
            pmdmntExit();
        }
        /* Return result explicitly: no-application is not a broken USB link. */
        memcpy(out,&rc,4);memcpy(out+4,&pid,8);memcpy(out+12,&tid,8);n=20;rc=0;
    } else if(op==46) {
        if(len!=6 || memcmp(data,"REBOOT",6)){*error=EINVAL;return 0;}
        if(sidecar_pending() || reboot_pending){*error=EBUSY;return 0;}
        rc=schedule_reboot();
    } else if(op==47) {
        /* Toggle a boot flag by reversible rename; no live kill. */
        if(len!=17 || data[0]>1){*error=EINVAL;return 0;}
        for(int i=1;i<17;i++)if(!isxdigit((unsigned char)data[i])){*error=EINVAL;return 0;}
        char path[160],disabled[180];snprintf(path,sizeof(path),"sdmc:/atmosphere/contents/%.*s/flags/boot2.flag",16,data+1);
        if(!sidecar_path(path,true)){*error=EACCES;return 0;}
        snprintf(disabled,sizeof(disabled),"%s.sidecar-disabled",path);
        const char *from=data[0]?disabled:path,*to=data[0]?path:disabled;
        struct stat st;
        if(sidecar_pending()){*error=EBUSY;return 0;}
        if(stat(from,&st)){*error=errno;return 0;}
        if(!S_ISREG(st.st_mode) || st.st_size>4096){*error=EINVAL;return 0;}
        if(!access(to,F_OK)){*error=EEXIST;return 0;}
        if(rename(from,to))*error=errno;
    } else if(op==48) {
        if(len){*error=EINVAL;return 0;}
        unsigned state[]={pad_ready,release_tick!=0,last_control_result,reboot_pending};
        memcpy(out,state,sizeof(state));n=sizeof(state);
    } else if(op==49) {
        if(len){*error=EINVAL;return 0;}
        SetSysSleepSettings value;rc=get_sleep(&value);
        if(R_SUCCEEDED(rc)){memcpy(out,&value,sizeof(value));u32 active=sleep_lease_active;memcpy(out+sizeof(value),&active,4);n=sizeof(value)+4;}
    } else if(op==50) {
        if(len!=4){*error=EINVAL;return 0;}u32 seconds;memcpy(&seconds,data,4);
        if(!seconds){*error=restore_sleep();return 0;}
        if(seconds<15 || seconds>600){*error=EINVAL;return 0;}
        pad_activity_tick=armGetSystemTick();
        *error=lease_arm(seconds);return 0;
    } else if(op==60) {
        /* Per-process memory: {u64 pid, u64 program_id, u64 used, u64 total} x up to 120. */
        if(len){*error=EINVAL;return 0;}
        u64 pids[128];s32 count=0;rc=svcGetProcessList(&count,pids,128);
        if(R_SUCCEEDED(rc))rc=pmdmntInitialize();
        if(R_SUCCEEDED(rc)) {
            for(s32 i=0;i<count && n+32<=4080;i++) {
                Handle h=INVALID_HANDLE;struct {NcmProgramLocation loc;u8 status[0x10];} info={0};
                u64 rec[4]={pids[i],0,0,0};
                Result pr=serviceDispatchInOut(pmdmntGetServiceSession(),65000,pids[i],info,
                    .out_handle_attrs={SfOutHandleAttr_HipcCopy},.out_handles=&h);
                if(R_SUCCEEDED(pr)) {
                    rec[1]=info.loc.program_id;
                    svcGetInfo(&rec[2],InfoType_UsedMemorySize,h,0);
                    svcGetInfo(&rec[3],InfoType_TotalMemorySize,h,0);
                    svcCloseHandle(h);
                }
                memcpy(out+n,rec,32);n+=32;
            }
            pmdmntExit();
        }
    } else if(op==59) {
        if(len){*error=EINVAL;return 0;}
        u64 v[8]={0};
        for(int pool=0;pool<4;pool++) {
            Result a=svcGetSystemInfo(&v[pool*2],0,INVALID_HANDLE,pool);
            Result b=svcGetSystemInfo(&v[pool*2+1],1,INVALID_HANDLE,pool);
            if(R_FAILED(a))rc=a;else if(R_FAILED(b))rc=b;
        }
        memcpy(out,v,sizeof(v));n=sizeof(v);
    } else if(op==62) {
        if(len!=1 || data[0]>1){*error=EINVAL;return 0;}
        rc=charger_hiz(data[0]==1,out);if(R_SUCCEEDED(rc))n=1;
    } else if(op==61) {
        if(len){*error=EINVAL;return 0;}
        rc=power_telemetry(out);
        if(R_SUCCEEDED(rc)){memcpy(out+17,&role_kicks,4);out[21]=role_streak;out[22]=role_reg1;n=23;}
    } else if(op==58) {
        if(len){*error=EINVAL;return 0;}
        rc=i2cInitialize();
        if(R_SUCCEEDED(rc)) {
            I2cSession is;rc=i2cOpenSession(&is,I2cDevice_Max77620Rtc);
            if(R_SUCCEEDED(rc)) {
                rc=rtc_write(&is,0x04,0x10);svcSleepThread(16000000);
                for(u8 reg=0x0e;R_SUCCEEDED(rc) && reg<=0x1b;reg++)rc=rtc_read(&is,reg,out+(reg-0x0e));
                i2csessionClose(&is);
            }
            i2cExit();
        }
        if(R_SUCCEEDED(rc))n=14;
    } else if(op==57) {
        /* Replace the restore target (journal original) with a validated value; applied directly when not holding. */
        if(len!=12){*error=EINVAL;return 0;}
        SetSysSleepSettings v;memcpy(&v,data,12);
        if(v.flags>3 || v.handheld_sleep_plan<0 || v.handheld_sleep_plan>5 || v.console_sleep_plan<0 || v.console_sleep_plan>5){*error=EINVAL;return 0;}
        if(sleep_lease_active) {
            int fd=open(SLEEP_JOURNAL,O_WRONLY|O_TRUNC);if(fd<0){*error=errno;return 0;}
            SleepJournal j={.magic=0x534c5035,.value=v};
            bool ok=write(fd,&j,sizeof(j))==sizeof(j) && !fsync(fd);int saved=errno;close(fd);
            if(!ok){*error=saved?saved:EIO;return 0;}
            sleep_original=v;
        } else rc=set_sleep(&v);
    } else if(op==56) {
        if(len!=1 || data[0]>1){*error=EINVAL;return 0;}
        *error=set_autohold(data[0]);u32 state=autohold;memcpy(out,&state,4);n=4;return n;
    } else if(op==51) {
        *error=ENOSYS;return 0;
    } else if(op==53) {
        /* "R2C", reason, idx, 0: one-shot bootloader reboot reason in MAX77620 RTC alarm registers. */
        u8 val1,val2;
        if(len!=6 || memcmp(data,"R2C",3) || data[5]>1 || r2c_encode(data[3],data[4],&val1,&val2)){*error=EINVAL;return 0;}
        if(sidecar_pending() || reboot_pending){*error=EBUSY;return 0;}
        /* bpc stays open from the RTC write to the reboot; failure clears the magic inside r2c_write. */
        rc=bpcInitialize();
        if(R_SUCCEEDED(rc)) {
            rc=r2c_write(val1,val2);
            if(R_FAILED(rc))bpcExit();
            else {detach_all();reboot_pending=r2c_pending=true;reboot_by_pmic=data[5]==1;reboot_tick=armGetSystemTick()+armNsToTicks(1000000000);}
        }
    } else if(op==54) {
        if(len){*error=EINVAL;return 0;}
        u32 state[]={1,controller_mode,pad_ready,seq_active,seq_index,seq_count,last_control_result,
            pad_idle_ms_left(),sleep_lease_active,wake_alarm_s,wake_reason(NULL),r2c_pending};
        if(state[10]!=0xffffffffU)last_wake_reason=state[10];else state[10]=last_wake_reason;
        memcpy(out,state,sizeof(state));n=sizeof(state);
    } else if(op==55) {
        if(len!=4){*error=EINVAL;return 0;}u32 seconds;memcpy(&seconds,data,4);
        if(seconds){*error=ENOSYS;return 0;}
        else rc=alarm_disarm();
        if(R_SUCCEEDED(rc) || !seconds)wake_alarm_s=seconds;
    } else {*error=ENOSYS;return 0;}
    if(R_FAILED(rc)){last_control_result=rc;*error=(int)rc;}
    return n;
}
