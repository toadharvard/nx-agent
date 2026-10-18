// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>
/* Reboot-to-config word kept in the MAX77620 RTC alarm registers (datasheet register map):
   RTCDOWA1 0x11 and RTCDOWA2 0x18 hold the magic, RTCYEARA1 0x13 holds word bits 0-5,
   RTCYEARA2 0x1A holds word bits 6-11. Word: reason 0-3, autoboot index 4-7, autoboot list 8, UMS index 9-11. */
#define R2C_REG_MAGIC1 0x11
#define R2C_REG_MAGIC2 0x18
#define R2C_REG_VAL1 0x13
#define R2C_REG_VAL2 0x1a
#define R2C_MAGIC 0x77
#define R2C_REG_BITS 6
#define R2C_REG_MASK 0x3fu
#define R2C_REASON_SHIFT 0
#define R2C_REASON_MASK 0xfu
#define R2C_IDX_SHIFT 4
#define R2C_IDX_MASK 0xfu
#define R2C_LIST_SHIFT 8
#define R2C_LIST_MASK 0x1u
#define R2C_UMS_SHIFT 9
#define R2C_UMS_MASK 0x7u
#define R2C_REASON_AUTOBOOT 1
#define R2C_REASON_MENU 2
#define R2C_REASON_UMS 3
#define R2C_IDX_MIN 1
#define R2C_IDX_MAX 8
static inline unsigned r2c_word(uint8_t val1,uint8_t val2){return (val1&R2C_REG_MASK)|(val2&R2C_REG_MASK)<<R2C_REG_BITS;}
static inline unsigned r2c_field(unsigned word,unsigned shift,unsigned mask){return word>>shift&mask;}
