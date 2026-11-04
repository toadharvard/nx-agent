/* V6n statediff: the one NineDevice9 field nine_convert_rasterizer_state reads (driver_caps.offset_units_unscaled). */
#include <stdlib.h>
#include "device9.h"
struct NineDevice9* v6n_fake_device(int offset_units_unscaled) {
    struct NineDevice9* d = calloc(1, sizeof *d);
    d->driver_caps.offset_units_unscaled = offset_units_unscaled;
    return d;
}
