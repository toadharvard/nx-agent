/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Cross-check of the MIT reboot-to-config implementation (console/common/r2c_layout.h,
 * console/worker/source/logic.c) against hekate's own rtc_reboot_reason_t from
 * bdk/rtc/max77620-rtc.h. Lives in the GPL tree because it includes the hekate header.
 *
 * The bitfield union is laid out LSB-first on both the host (GCC/Clang, little-endian)
 * and ARM EABI, so the host view of rtc_reboot_reason_t equals the on-device bytes.
 * The hekate header goes first: bdk/utils/types.h defines NULL unconditionally.
 */
#include <rtc/max77620-rtc.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../../common/r2c_layout.h"
#include "../../worker/source/logic.h"

static unsigned checks;

#define CHECK(cond) do { checks++; if (!(cond)) { \
	fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); exit(1); } } while (0)

static rtc_reboot_reason_t hekate_rr(unsigned reason, unsigned idx)
{
	rtc_reboot_reason_t rr;

	memset(&rr, 0, sizeof(rr));
	rr.dec.reason = reason;

	if (reason == REBOOT_REASON_SELF)
		rr.dec.autoboot_idx = idx;

	return rr;
}

static void check_layout(void)
{
	CHECK(R2C_REG_MAGIC1 == MAX77620_ALARM1_WEEKDAY_REG);
	CHECK(R2C_REG_MAGIC2 == MAX77620_ALARM2_WEEKDAY_REG);
	CHECK(R2C_REG_VAL1 == MAX77620_ALARM1_YEAR_REG);
	CHECK(R2C_REG_VAL2 == MAX77620_ALARM2_YEAR_REG);
	CHECK(R2C_MAGIC == RTC_REBOOT_REASON_MAGIC);
	CHECK(R2C_REASON_AUTOBOOT == REBOOT_REASON_SELF);
	CHECK(R2C_REASON_MENU == REBOOT_REASON_MENU);
	CHECK(R2C_REASON_UMS == REBOOT_REASON_UMS);
	CHECK(sizeof(rtc_reboot_reason_t) == 2);
}

static void check_encode(void)
{
	unsigned accepted = 0;

	for (unsigned reason = 0; reason < 16; reason++)
	{
		for (unsigned idx = 0; idx < 16; idx++)
		{
			uint8_t v1 = 0xff, v2 = 0xff;
			int rc = r2c_encode(reason, idx, &v1, &v2);
			int valid = (reason == REBOOT_REASON_SELF && idx >= R2C_IDX_MIN && idx <= R2C_IDX_MAX) ||
				((reason == REBOOT_REASON_MENU || reason == REBOOT_REASON_UMS) && idx == 0);

			if (!valid)
			{
				CHECK(rc == EINVAL);
				continue;
			}

			CHECK(rc == 0);
			accepted++;

			rtc_reboot_reason_t want = hekate_rr(reason, idx);
			CHECK(want.enc.val1 == v1);
			CHECK(want.enc.val2 == v2);

			rtc_reboot_reason_t got;
			memset(&got, 0, sizeof(got));
			got.enc.val1 = v1;
			got.enc.val2 = v2;
			CHECK(got.dec.reason == reason);
			CHECK(got.dec.autoboot_idx == (reason == REBOOT_REASON_SELF ? idx : 0));
			CHECK(got.dec.autoboot_list == 0);
			CHECK(got.dec.ums_idx == 0);

			unsigned r = 99, i = 99;
			CHECK(r2c_decode(v1, v2, &r, &i) == 0);
			CHECK(r == reason && i == idx);
		}
	}

	CHECK(accepted == 10);
}

static void check_decode_all_bytes(void)
{
	for (unsigned v1 = 0; v1 < 64; v1++)
	{
		for (unsigned v2 = 0; v2 < 64; v2++)
		{
			rtc_reboot_reason_t rr;
			memset(&rr, 0, sizeof(rr));
			rr.enc.val1 = v1;
			rr.enc.val2 = v2;

			unsigned w = r2c_word(v1, v2);
			CHECK(r2c_field(w, R2C_REASON_SHIFT, R2C_REASON_MASK) == rr.dec.reason);
			CHECK(r2c_field(w, R2C_IDX_SHIFT, R2C_IDX_MASK) == rr.dec.autoboot_idx);
			CHECK(r2c_field(w, R2C_LIST_SHIFT, R2C_LIST_MASK) == rr.dec.autoboot_list);
			CHECK(r2c_field(w, R2C_UMS_SHIFT, R2C_UMS_MASK) == rr.dec.ums_idx);

			unsigned r = 99, i = 99;
			int rc = r2c_decode(v1, v2, &r, &i);
			int valid = !rr.dec.autoboot_list && !rr.dec.ums_idx &&
				((rr.dec.reason == REBOOT_REASON_SELF && rr.dec.autoboot_idx >= R2C_IDX_MIN && rr.dec.autoboot_idx <= R2C_IDX_MAX) ||
				 ((rr.dec.reason == REBOOT_REASON_MENU || rr.dec.reason == REBOOT_REASON_UMS) && !rr.dec.autoboot_idx));

			CHECK(valid ? rc == 0 : rc == EINVAL);

			if (valid)
				CHECK(r == rr.dec.reason && i == rr.dec.autoboot_idx);
		}
	}

	unsigned r, i;
	CHECK(r2c_decode(0x40, 0, &r, &i) == EINVAL);
	CHECK(r2c_decode(0, 0x40, &r, &i) == EINVAL);
}

int main(void)
{
	check_layout();
	check_encode();
	check_decode_all_bytes();
	printf("test_r2c_crosscheck: %u checks OK (r2c_layout.h + worker r2c_encode/r2c_decode == hekate rtc_reboot_reason_t)\n", checks);

	return 0;
}
