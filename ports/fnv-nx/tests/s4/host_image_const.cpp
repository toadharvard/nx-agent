/* host_image_const.cpp - fnvnx_image_const (platform/include/fnvnx_win32_ext.h) of the host test binaries: the game
 * build (toolchain/game/boot.cpp) reads constants the loader left unmapped from the exe file; a host binary has no
 * exe image, every constant is in mapped memory and is read where it is. */
#include <stdint.h>

extern "C" const void *fnvnx_image_const(const void *p, uint32_t, void *)
{
    return p;
}
