/* fnv-nx S6d: host-side fake nvc0 screen (nvc0_fake_screen.c) */
#ifndef NINE_HOST_SCREEN_H
#define NINE_HOST_SCREEN_H
#include <stdint.h>

struct pipe_screen;

/* chipset: what libdrm_nouveau reports. On the Switch that is nvgpu's arch field, 0x120 (GM20B
 * would be 0x12b with the impl bits, which devkitPro's libdrm_nouveau does not add). */
struct pipe_screen *nine_host_fake_nvc0_screen(uint16_t chipset);

#endif
