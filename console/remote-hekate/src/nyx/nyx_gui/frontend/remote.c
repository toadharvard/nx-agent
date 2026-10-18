/* SPDX-License-Identifier: GPL-2.0-only */
#include <stdlib.h>
#include <bdk.h>
#include "../config.h"
#include "gui.h"
#include "gui_tools.h"
#include "remote.h"
#include <libs/fatfs/ff.h>

/* Same DRAM staging area and layout as nyx.c launch_payload / bootloader _launch_payload. */
#define REMOTE_PATCHED_RELOC_SZ 0x94
#define REMOTE_EXT_PAYLOAD_ADDR 0xC0000000
#define REMOTE_RCM_PAYLOAD_ADDR (REMOTE_EXT_PAYLOAD_ADDR + ALIGN(REMOTE_PATCHED_RELOC_SZ, 0x10))
#define REMOTE_MAX_SECTIONS 256

static usb_ops_t ops;
static bool enabled,active,paused,in_main;
static unsigned phase;
static u32 phase_since;
static u8 *request=(u8 *)USB_EP_BULK_OUT_BUF_ADDR;
static u8 *reply=(u8 *)USB_EP_BULK_IN_BUF_ADDR;

static_assert(sizeof(boot_cfg_t) == REMOTE_HEKATE_BCFG_SIZE, "remote b_cfg size");
static_assert(REMOTE_PATCHED_RELOC_SZ == REMOTE_HEKATE_BCFG_OFF, "remote b_cfg offset");

void nyx_remote_suspend(void){
 paused=true;remote_core_reset();
 if(active)ops.usbd_end(true,false);
 active=false;phase=0;
}

void nyx_remote_resume(void){
 paused=false;phase_since=get_tmr_ms();
 if(enabled)remote_core_kick(phase_since);
}

void nyx_remote_local_activity(void){
 if(enabled)remote_core_kick(get_tmr_ms());
}

static uint32_t _entries(void *ctx,char *out,uint32_t cap,uint32_t *len){
 LIST_INIT(ini_sections);
 remote_ini_sec_t *secs;
 uint32_t count=0,res;

 if(sd_mount() || ini_parse(&ini_sections,"bootloader/hekate_ipl.ini",false))
  return REMOTE_E_UNAVAIL;

 secs=malloc(sizeof(remote_ini_sec_t)*REMOTE_MAX_SECTIONS);

 if(!secs){
  ini_free(&ini_sections);

  return REMOTE_E_NOMEM;
 }

 LIST_FOREACH_ENTRY(ini_sec_t,ini_sec,&ini_sections,link)
 {
  if(count==REMOTE_MAX_SECTIONS)
   break;

  secs[count].type=ini_sec->type;
  secs[count].name=ini_sec->name;
  count++;
 }

 res=count==REMOTE_MAX_SECTIONS?REMOTE_E_TOOBIG:remote_core_format_entries(secs,count,out,cap,len);
 free(secs);
 ini_free(&ini_sections);

 return res;
}

static uint32_t _watchdog_from_ini(void){
 LIST_INIT(ini_sections);
 uint32_t seconds=REMOTE_WATCHDOG_DEFAULT_S;

 if(ini_parse(&ini_sections,"bootloader/remote/remote.ini",false))
  return seconds;

 LIST_FOREACH_ENTRY(ini_sec_t,ini_sec,&ini_sections,link)
 {
  if(ini_sec->type!=INI_CHOICE)
   continue;

  LIST_FOREACH_ENTRY(ini_kv_t,kv,&ini_sec->kvs,link)
  {
   if(!strcmp(kv->key,"watchdog"))
    seconds=remote_core_parse_watchdog(kv->val);
  }
 }

 ini_free(&ini_sections);

 return seconds;
}

/* Same conditions as gui.c _create_mbox_reboot / _reboot_action. */
static uint32_t _caps(void){
 u8 dram_id=fuse_read_dramid(true);
 u8 dram_id_adj=fuse_read_dramid(false);
 bool t210_8gb_dramid=!h_cfg.t210b01 && dram_id==LPDDR4_ICOSA_8GB_SAMSUNG_K4FBE3D4HM_MGXX;
 bool unmatched_fuses=h_cfg.t210b01 && dram_id_adj==LPDDR4X_AULA_8GB_SAMSUNG_K4UBE3D4AA_MGCL && dram_id!=dram_id_adj;
 uint32_t caps=0;

 if(!t210_8gb_dramid && !unmatched_fuses)
  caps|=REMOTE_CAP_REBOOT_OFW;

 if(h_cfg.rcm_patched && !t210_8gb_dramid)
  caps|=REMOTE_CAP_REBOOT_NORMAL;

 return caps;
}

/*
 * bootloader/main.c _launch_payload(path, update=true): the image is copied unrelocated to
 * RCM_PAYLOAD_ADDR, its b_cfg slot (offset 0x94) is written and EMC_HEKA_UPD tells the
 * receiving hekate that it was chainloaded as an update. The slot is zeroed, which is the
 * state of a cold boot from boot.dat: no AUTOBOOT/FROM_LAUNCH/FROM_ID/UMS override, so the
 * receiving hekate applies [config] of bootloader/hekate_ipl.ini.
 */
static void _launch_original(void){
 u32 size=0;
 u8 *buf;

 if(sd_mount())
  return;

 buf=sd_file_read("bootloader/update.bin",&size);

 if(!buf)
  return;

 if(!remote_core_check_hekate(buf,size)){
  free(buf);

  return;
 }

 nyx_remote_suspend();
 sd_end();

 memcpy((void *)REMOTE_RCM_PAYLOAD_ADDR,buf,size);
 memset((u8 *)(REMOTE_RCM_PAYLOAD_ADDR+REMOTE_PATCHED_RELOC_SZ),0,sizeof(boot_cfg_t));
 EMC(EMC_SCRATCH0)|=EMC_HEKA_UPD;

 hw_deinit(false);

 void (*payload_ptr)()=(void *)REMOTE_RCM_PAYLOAD_ADDR;
 (*payload_ptr)();
}

static void _execute(int action,uint32_t arg){
 switch(action){
 case REMOTE_ACT_LAUNCH:
  _launch_original();
  nyx_remote_resume();
  break;
 case REMOTE_ACT_UMS:
  /* action_ums_sd already wraps the gadget with nyx_remote_suspend/resume. */
  action_ums_sd(NULL);
  break;
 case REMOTE_ACT_REBOOT:
  nyx_remote_suspend();
  power_set_state(arg==REMOTE_REBOOT_NORMAL?POWER_OFF_REBOOT:REBOOT_BYPASS_FUSES);
  break;
 case REMOTE_ACT_POWEROFF:
  nyx_remote_suspend();
  power_set_state(POWER_OFF_RESET);
  break;
 }
}

void nyx_remote_init(void){
 /* A feature flag preserves stock behavior and provides an offline escape. */
 if(!h_cfg.t210b01)return;
 sd_mount();enabled=!f_stat("bootloader/remote/enable",NULL);
 phase_since=get_tmr_ms();
 if(!enabled)return;
 remote_core_init(phase_since,_watchdog_from_ini(),_caps());
 remote_core_set_entries(_entries,NULL);
 xusb_device_get_ops(&ops);
}

static void _restart_usb(u32 now){
 remote_core_reply_failed(now);
 nyx_remote_suspend();
 nyx_remote_resume();
}

static void _service(u32 now){
 if(!active){
  if((u32)(now-phase_since)<1000)return;
  phase_since=now;
  if(ops.usb_device_init())return;
  if(xusb_device_enumerate_begin(USB_GADGET_REMOTE)){ops.usbd_end(true,false);return;}
  active=true;phase=0;
 }
 /* One bounded event-pump step; never wait for a host in the GUI loop. */
 if(!xusb_remote_configured()){
  if(phase==2)remote_core_reply_failed(now);
  phase=0;
  return;
 }
 int rc;u32 size=0;
 if(phase==0){
  rc=ops.usb_device_ep1_out_read(request,64,NULL,USB_XFER_START);
  if(!rc){phase=1;phase_since=now;}
 }else if(phase==1){
  rc=ops.usb_device_ep1_out_reading_finish(&size,1);
  if(rc==USB_ERROR_TIMEOUT)return;
  if(rc){_restart_usb(now);return;}
  remote_core_set_nested(!in_main);
  remote_core_request(request,size,reply,now,(void *)NYX_FB2_ADDRESS);
  rc=ops.usb_device_ep1_in_write(reply,REMOTE_REPLY_SIZE,NULL,USB_XFER_START);
  if(rc){_restart_usb(now);return;}
  phase=2;phase_since=now;
 }else{
  rc=ops.usb_device_ep1_in_writing_finish(&size,1);
  if(rc==USB_ERROR_TIMEOUT){
   /* Lost host must not leave the GUI or pointer stuck. */
   if((u32)(now-phase_since)>5000)_restart_usb(now);
   return;
  }
  if(rc){_restart_usb(now);return;}
  remote_core_reply_done(now);
  phase=0;
 }
}

/* GUI main loop: services USB and runs deferred actions once their reply is delivered. */
void nyx_remote_poll(void){
 if(!enabled || paused)return;
 u32 now=get_tmr_ms();
 uint32_t arg=0;
 in_main=true;
 _service(now);
 in_main=false;
 if(paused)return;
 int action=remote_core_take_action(get_tmr_ms(),&arg);
 if(action!=REMOTE_ACT_NONE)_execute(action,arg);
}

/* manual_system_maintenance: a blocking Nyx tool is running; keep USB alive, defer everything else. */
void nyx_remote_service(void){
 if(!enabled || paused)return;
 u32 now=get_tmr_ms();
 _service(now);
 remote_core_kick(now);
}
