// m1_misc.cpp: IDirect3DDevice9Ex::SetConvolutionMonoKernel and ComposeRects. Sources: MS pages of both methods, D3DCOMPOSERECT* structures / D3DCOMPOSERECTSOP page, env table (CheckDeviceFormat rows), res_fmt.cpp.
// SetConvolutionMonoKernel(width, height, rows, columns): MS "width 1 - D3DCONVOLUTIONMONO_MAXWIDTH, height 1 - ..MAXHEIGHT" (page remark: kernel resolution 1 x 1 to 7 x 7), "rows / columns must be NULL, which sets the weights
//  equal to the default value" (a monochrome convolution filter is a box filter with all weights 1.0, so a kernel is its size alone), "recorded in state blocks as a part of D3DSBT_PIXELSTATE" = slots NX_S_convk + 0 / 1 of the
//  state model (state_model.py), default 1 x 1, back to the default by Reset (every state does). Violations = D3DERR_INVALIDCALL (the page lists no failure code). The kernel is read only by sampling a D3DFMT_A1 texture with
//  D3DTEXF_CONVOLUTIONMONO: A1 textures cannot exist here (see ComposeRects) and TextureFilterCaps lacks D3DPTFILTERCAPS_CONVOLUTIONMONO (ValidateDevice reports UNSUPPORTEDTEXTUREFILTER), so the state is not observable beyond Capture / Apply.
// ComposeRects: wine-9.0 dlls/d3d9/device.c d3d9_device_ComposeRects is `FIXME("... stub!"); return E_NOTIMPL;` and looks at no argument: the body does exactly that, one fnvnx_wine_fixme(47) per call
//  (const_fixme.tsv). (The earlier decision D3DERR_INVALIDCALL, from the proof that no D3DFMT_A1 surface exists, is replaced by the Wine rule: the result of the Wine function is the source.)
#include "res_tex.h"
#include "../gen/d9impl.gen.h"
#include "dev_core.h"
#include "const_fixme.h"

enum { M1_KERNEL_MAX = 7 };   // MS SetConvolutionMonoKernel remarks: 1 x 1 to 7 x 7
HRESULT STDMETHODCALLTYPE NxDevice9Ex::SetConvolutionMonoKernel(UINT w, UINT h, float* rows, float* cols) {
    if (w < 1 || w > M1_KERNEL_MAX || h < 1 || h > M1_KERNEL_MAX || rows || cols) return D3DERR_INVALIDCALL;
    uint32_t k[2] = {w, h}; put(NX_S_convk, &k[0]); put(NX_S_convk + 1, &k[1]); return D3D_OK;
}
HRESULT STDMETHODCALLTYPE NxDevice9Ex::ComposeRects(IDirect3DSurface9*, IDirect3DSurface9*, IDirect3DVertexBuffer9*, UINT, IDirect3DVertexBuffer9*, D3DCOMPOSERECTSOP, INT, INT) { fnvnx_wine_fixme(NX_CF_ComposeRects); return E_NOTIMPL; }
