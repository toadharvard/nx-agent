/* fnv-nx S6d: a fake nvc0 pipe_screen for the host (no GPU, no libdrm, no winsys).
 *
 * The capability queries are nvc0's own functions (this file includes nvc0_screen.c, compiled with
 * -D__SWITCH__ as on the console), run on a zeroed nvc0_screen that carries only what they read:
 * device->chipset, device->vram_size and base.class_3d. So st/nine sees exactly the caps it sees on
 * the Switch, and emits the same TGSI. Nothing else of the screen is usable (no resources/contexts). */
#include "nvc0/nvc0_screen.c"

#include "nine_host_screen.h"

/* referenced by the kept get_param path (PIPE_CAP_PCI_* / device id); never reached for shaders */
int
nouveau_getparam(struct nouveau_device *dev, uint64_t param, uint64_t *value)
{
   (void)dev; (void)param;
   *value = 0;
   return -1;
}

static const char *
fake_get_name(struct pipe_screen *ps)
{
   (void)ps;
   return "NV12B (host fake)";
}

static const char *
fake_get_vendor(struct pipe_screen *ps)
{
   (void)ps;
   return "nouveau";
}

struct pipe_screen *
nine_host_fake_nvc0_screen(uint16_t chipset)
{
   struct nvc0_screen *screen = CALLOC_STRUCT(nvc0_screen);
   struct nouveau_device *dev = CALLOC_STRUCT(nouveau_device);
   if (!screen || !dev)
      return NULL;
   dev->chipset = chipset;
   dev->vram_size = 4ull << 30; /* Switch: unified 4 GiB */
   screen->base.device = dev;
   /* same selection as nvc0_screen_create() */
   switch (chipset & ~0xf) {
   case 0x130: screen->base.class_3d = (chipset == 0x130 || chipset == 0x13b) ? GP100_3D_CLASS : GP102_3D_CLASS; break;
   case 0x120: screen->base.class_3d = GM200_3D_CLASS; break;
   case 0x110: screen->base.class_3d = GM107_3D_CLASS; break;
   case 0x100: case 0xf0: screen->base.class_3d = NVF0_3D_CLASS; break;
   case 0xe0: screen->base.class_3d = chipset == 0xea ? NVEA_3D_CLASS : NVE4_3D_CLASS; break;
   default: return NULL; /* Fermi: emitter compiled out on __SWITCH__ */
   }
   screen->base.prefer_nir = false;
   struct pipe_screen *ps = &screen->base.base;
   ps->get_param = nvc0_screen_get_param;
   ps->get_paramf = nvc0_screen_get_paramf;
   ps->get_shader_param = nvc0_screen_get_shader_param;
   ps->get_name = fake_get_name;
   ps->get_vendor = fake_get_vendor;
   ps->get_device_vendor = fake_get_vendor;
   return ps;
}
