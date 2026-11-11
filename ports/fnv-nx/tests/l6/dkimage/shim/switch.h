/* host shim for compiling deko3d's image_formats.cpp (the real dkImageFormatGetFlags) off the console: the libnx types it needs, nothing else */
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <stdlib.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef uint64_t u64;
#include <switch/nvidia/types.h>
