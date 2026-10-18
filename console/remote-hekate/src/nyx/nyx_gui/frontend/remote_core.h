/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef NYX_REMOTE_CORE_H
#define NYX_REMOTE_CORE_H
#include <stdint.h>
#define REMOTE_MAGIC 0x5258594eU
#define REMOTE_FRAME_SIZE (1280U*720U*4U)
#define REMOTE_REPLY_SIZE 4096U
#define REMOTE_DATA_SIZE (REMOTE_REPLY_SIZE-32U)
#define REMOTE_VERSION 2U

#define REMOTE_WATCHDOG_DEFAULT_S 90U
#define REMOTE_WATCHDOG_MIN_S 30U
#define REMOTE_WATCHDOG_MAX_S 3600U
/* Delay between a completed reply and its side effect. */
#define REMOTE_ACTION_DELAY_MS 200U
/* A reply the host never collects does not block the action forever. */
#define REMOTE_ACTION_REPLY_TIMEOUT_MS 2000U

/* Reply error codes (reply header word 2). */
#define REMOTE_OK 0U
#define REMOTE_E_OP 1U      /* unknown op */
#define REMOTE_E_ARG 2U     /* bad length, magic or arguments */
#define REMOTE_E_NOMEM 3U
#define REMOTE_E_STALE 4U   /* snapshot generation mismatch */
#define REMOTE_E_BUSY 5U    /* a side-effect action is already pending */
#define REMOTE_E_UNAVAIL 6U /* not possible on this device / data source failed */
#define REMOTE_E_TOOBIG 7U  /* reply would not fit in 4064 bytes */

/* Deferred side-effect actions executed by the Nyx main loop. */
#define REMOTE_ACT_NONE 0
#define REMOTE_ACT_LAUNCH 1   /* op7 or watchdog: chainload bootloader/update.bin */
#define REMOTE_ACT_UMS 2      /* op8: SD UMS */
#define REMOTE_ACT_REBOOT 3   /* op9: arg 0 = OFW (REBOOT_BYPASS_FUSES), 1 = Normal (POWER_OFF_REBOOT) */
#define REMOTE_ACT_POWEROFF 4 /* op10: POWER_OFF_RESET */

#define REMOTE_REBOOT_OFW 0U
#define REMOTE_REBOOT_NORMAL 1U

/* Device capabilities set by the platform layer. */
#define REMOTE_CAP_REBOOT_OFW 1U    /* Nyx power menu shows OFW without the DRAM-ID warning */
#define REMOTE_CAP_REBOOT_NORMAL 2U /* Nyx power menu "Normal" maps to POWER_OFF_REBOOT */

/* Section types, identical to bdk/utils/ini.h. */
#define REMOTE_INI_CHOICE 3U

typedef struct {
 uint32_t type;
 const char *name;
} remote_ini_sec_t;

/* Fills out[0..cap) with op6 text; returns a REMOTE_* error code. */
typedef uint32_t (*remote_entries_fn)(void *ctx,char *out,uint32_t cap,uint32_t *len);

/* Fixed 64-byte requests; fixed replies. No arbitrary memory or code execution. */
void remote_core_request(const void *request,uint32_t length,void *reply,uint32_t now,const void *framebuffer);
int remote_core_touch(uint32_t now,uint32_t *x,uint32_t *y,int *pressed);
/* Drops snapshot and touch state only; pending actions and watchdog survive USB resets. */
void remote_core_reset(void);

/* Full state reset (host tests and Nyx start). */
void remote_core_init(uint32_t now,uint32_t watchdog_s,uint32_t caps);
void remote_core_set_entries(remote_entries_fn fn,void *ctx);
/* Nonzero while polled from manual_system_maintenance (a blocking Nyx tool runs): ops 6-10 answer BUSY. */
void remote_core_set_nested(int nested);
/* Restart the countdown without counting a host command (UMS end, local touch). */
void remote_core_kick(uint32_t now);
/* Strict decimal parse of remote.ini `watchdog`: 0 = off, clamp to 30..3600, junk = default. */
uint32_t remote_core_parse_watchdog(const char *value);

/* Reply transfer outcome for the request that was just answered. */
void remote_core_reply_done(uint32_t now);
void remote_core_reply_failed(uint32_t now);
/* Returns the action to execute now (and its argument), or REMOTE_ACT_NONE. */
int remote_core_take_action(uint32_t now,uint32_t *arg);

/* op12 view. */
void remote_core_status(uint32_t now,uint32_t out[4]);

/* op6 formatting with hekate _auto_launch autoboot numbering. */
uint32_t remote_core_format_entries(const remote_ini_sec_t *secs,uint32_t count,char *out,uint32_t cap,uint32_t *len);

/* Validates a hekate image before its b_cfg slot (offset 0x94) is written. */
#define REMOTE_HEKATE_MAGIC 0x43544349U
#define REMOTE_HEKATE_BCFG_OFF 0x94U
#define REMOTE_HEKATE_BCFG_SIZE 0x84U
#define REMOTE_HEKATE_MAX_SIZE 0x30000U
int remote_core_check_hekate(const void *image,uint32_t size);
#endif
