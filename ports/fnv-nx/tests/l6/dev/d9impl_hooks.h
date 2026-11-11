// TEST copy of the device-only hooks (tests/l6/dev builds without the resource layer); the product header is dev/d9impl_hooks.h.
#pragma once
#include "state_rt.h"
#define NX_BASES_NxDevice9 NxDeviceCore
#define NX_BASES_NxDevice9Ex NxDeviceCore
#define NX_BASES_NxStateBlock9 NxStateBlockCore
#define NX_BASES_NxVertexDeclaration9 NxDeclCore
