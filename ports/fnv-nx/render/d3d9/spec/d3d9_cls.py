"""d3d9_cls.py: output classes (det/env/pix) with one-line justifications; see README.md for the definitions.
Lookup order for a parameter: "Iface9.Method.param", "Method.param", struct default (STRUCT), else det.
For a return value the key is "Method.$ret". Entries are (class, justification)."""
ENV_CAPS = ("env", "capabilities and identity of the adapter/driver/display, a property of the environment")
ENV_ALLOC = ("env", "allocation can fail with D3DERR_OUTOFVIDEOMEMORY/NOTAVAILABLE: depends on device capacity")
ENV_STATE = ("env", "device/window/display state outside the call stream (device lost, occlusion, mode change)")
ENV_CREATE = ("env", "device creation/reset outcome depends on adapter, display mode and window state")
PIX_POLL = ("pix", "S_FALSE until the GPU has finished: depends on GPU timing")
STRUCT = {"D3DCAPS9": ENV_CAPS, "D3DADAPTER_IDENTIFIER9": ENV_CAPS, "D3DDISPLAYMODE": ENV_CAPS, "D3DDISPLAYMODEEX": ENV_CAPS,
          "LUID": ENV_CAPS, "D3DRASTER_STATUS": ("pix", "scanline and in-vblank flag are display timing, not a function of the stream"),
          "D3DPRESENTSTATS": ("pix", "present counters and QPC/GPU timestamps are display and GPU timing")}
CLS = {m + ".$ret": ENV_ALLOC for m in ("CreateTexture", "CreateVolumeTexture", "CreateCubeTexture", "CreateVertexBuffer", "CreateIndexBuffer",
       "CreateRenderTarget", "CreateDepthStencilSurface", "CreateOffscreenPlainSurface", "CreateRenderTargetEx", "CreateDepthStencilSurfaceEx",
       "CreateOffscreenPlainSurfaceEx", "CreateQuery")}
CLS.update({m + ".$ret": ENV_CAPS for m in ("GetAdapterCount", "GetAdapterModeCount", "GetAdapterModeCountEx", "GetAdapterMonitor", "GetAdapterLUID",
            "CheckDeviceType", "CheckDeviceFormat", "CheckDeviceMultiSampleType", "CheckDepthStencilMatch", "CheckDeviceFormatConversion",
            "GetDeviceCaps", "EnumAdapterModes", "EnumAdapterModesEx", "GetAdapterIdentifier", "GetAdapterDisplayMode", "GetAdapterDisplayModeEx",
            "GetDisplayMode", "GetDisplayModeEx", "RegisterSoftwareDevice", "ValidateDevice")})
CLS.update({m + ".$ret": ENV_STATE for m in ("Present", "PresentEx", "TestCooperativeLevel", "CheckDeviceState", "WaitForVBlank")})
CLS.update({m + ".$ret": ENV_CREATE for m in ("CreateDevice", "CreateDeviceEx", "Reset", "ResetEx", "CreateAdditionalSwapChain")})
CLS.update({
    "GetAvailableTextureMem.$ret": ("env", "free video memory of the adapter"),
    "CheckResourceResidency.$ret": ("env", "residency is the memory manager's decision"),
    "GetData.$ret": PIX_POLL,
    "CheckDeviceMultiSampleType.pQualityLevels": ("env", "quality levels of the adapter/driver"),
    "ValidateDevice.pNumPasses": ("env", "passes needed on this driver and hardware"),
    "GetAdapterDisplayModeEx.rotation": ("env", "rotation of the display"), "GetDisplayModeEx.rotation": ("env", "rotation of the display"),
    "CreateDevice.pPresentationParameters": ("env", "the runtime rewrites zero/UNKNOWN (and further) fields from window client rect, desktop mode and swap effect"),
    "CreateDeviceEx.parameters": ("env", "same as CreateDevice.pPresentationParameters"), "Reset.pPresentationParameters": ("env", "same as CreateDevice.pPresentationParameters"),
    "ResetEx.parameters": ("env", "same as CreateDevice.pPresentationParameters"), "CreateAdditionalSwapChain.pPresentationParameters": ("env", "same as CreateDevice.pPresentationParameters"),
    "pSharedHandle": ("env", "shared handle value is chosen by the runtime (D3D9Ex only; reserved NULL otherwise)"),
    "shared_handle": ("env", "shared handle value is chosen by the runtime (D3D9Ex only; reserved NULL otherwise)"),
})
LOCKED_FIELDS = {"D3DLOCKED_RECT": {"Pitch": ("env", "row stride is chosen by the driver; the journal records logical rows, not stride")},
                 "D3DLOCKED_BOX": {"RowPitch": ("env", "row stride chosen by the driver"), "SlicePitch": ("env", "slice stride chosen by the driver")}}
