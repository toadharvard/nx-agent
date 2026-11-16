/* The interface the loader (loader/guest/boot.c) and the platform both declare: it lives in the lowest root both use (hostabi/include), because
 * the platform uses the loader's outputs and the loader cannot use the platform. platform/include/fnvnx_win32_ext.h includes this header. */
#ifndef FNVNX_LOADER_IFACE_H
#define FNVNX_LOADER_IFACE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* The declared fonts: an input of the platform like the registry hive and FalloutPrefs.ini. The one definition is loader/guest/boot.c, which
 * returns FNVNX_SD_FONTS of hostabi/include/fnvnx/sdlayout.h (the one path record; the boot machine mounts the tools/fonts-wine9 mirror there,
 * read from the same macro), so a root that links the platform without the loader does not link. The directory holds the Wine 9.0 data
 * files (the .fon bitmap fonts and the TrueType files); registry exports and host fonts are not read. There is no setter and no unset
 * state: GDI reads it when the first font is realised. */
struct fnvnx_font_dirs { const char *dir; };   /* the one declared directory: exactly the fnv-nx-build tools/fonts-wine9 mirror */
const struct fnvnx_font_dirs *fnvnx_declared_fonts(void);

/* The loader reports a mapped image (the exe, or a translated game module after mapping it): the module table gets it
 * (HMODULE = base; GetModuleHandle/GetModuleFileName/LoadLibrary/GetProcAddress by the PE export directory in memory,
 * forwarders followed; FindResource* on its resource directory) and the page map records it as MEM_IMAGE, the whole
 * image and then each of its sections with the protection of its R/W/X bits. `name` is the file name or path. */
void fnvnx_win32_register_image(const char *name, uint32_t base, uint32_t size);

#ifdef __cplusplus
}
#endif
#endif
