// m1_misc.cpp: IDirect3DDevice9Ex::SetConvolutionMonoKernel and ComposeRects. Sources: MS pages of both methods, D3DCOMPOSERECT* structures / D3DCOMPOSERECTSOP page, env table (CheckDeviceFormat rows), res_fmt.cpp.
// SetConvolutionMonoKernel(width, height, rows, columns): MS "width 1 - D3DCONVOLUTIONMONO_MAXWIDTH, height 1 - ..MAXHEIGHT" (page remark: kernel resolution 1 x 1 to 7 x 7), "rows / columns must be NULL, which sets the weights
//  equal to the default value" (a monochrome convolution filter is a box filter with all weights 1.0, so a kernel is its size alone), "recorded in state blocks as a part of D3DSBT_PIXELSTATE" = slots NX_S_convk + 0 / 1 of the
//  state model (state_model.py), default 1 x 1, back to the default by Reset (every state does). Violations = D3DERR_INVALIDCALL (the page lists no failure code). The kernel is read only by sampling a D3DFMT_A1 texture with
//  D3DTEXF_CONVOLUTIONMONO: A1 textures cannot exist here (see ComposeRects) and TextureFilterCaps lacks D3DPTFILTERCAPS_CONVOLUTIONMONO (ValidateDevice reports UNSUPPORTEDTEXTUREFILTER), so the state is not observable beyond Capture / Apply.
// ComposeRects: MS "source and destination must be D3DFMT_A1 surfaces; the source created with D3DUSAGE_TEXTAPI, DEFAULT pool; the destination DEFAULT or SYSTEMMEM; a part of a texture". PROOF that no call reaches pixels:
//  (1) every surface of this device is made by mk_surf / the texture creators, which refuse a format without a layout: nx_texfmt(D3DFMT_A1) is false (res_fmt.cpp has no 1 bpp layout), so no A1 surface of any type, pool or usage exists
//  (tests/l6/m1 part c creates every D3DFORMAT through every creator and reads the format back); (2) the env table answers D3DERR_NOTAVAILABLE for A1 with every usage and resource type but buffers, and for D3DUSAGE_TEXTAPI with every format.
//  Hence "pSrc and pDst are A1 surfaces" is false for every pair of objects the application can hold, and the page's only other outcome, success, is unreachable: the call is D3DERR_INVALIDCALL for every argument. The pixel rule
//  is also under-specified (D3DCOMPOSERECTS_NEG reads "Copy the negated source" and "Dst & ~Src"; the A1 bit order is not stated anywhere), which is why no pixel path is written: it would be a guess that nothing can call. Row in contract_consts.tsv.
#include "res_tex.h"
#include "d9impl.gen.h"
#include "dev_core.h"

enum { M1_KERNEL_MAX = 7 };   // MS SetConvolutionMonoKernel remarks: 1 x 1 to 7 x 7
HRESULT STDMETHODCALLTYPE NxDevice9Ex::SetConvolutionMonoKernel(UINT w, UINT h, float* rows, float* cols) {
    if (w < 1 || w > M1_KERNEL_MAX || h < 1 || h > M1_KERNEL_MAX || rows || cols) return D3DERR_INVALIDCALL;
    uint32_t k[2] = {w, h}; put(NX_S_convk, &k[0]); put(NX_S_convk + 1, &k[1]); return D3D_OK;
}
HRESULT STDMETHODCALLTYPE NxDevice9Ex::ComposeRects(IDirect3DSurface9*, IDirect3DSurface9*, IDirect3DVertexBuffer9*, UINT, IDirect3DVertexBuffer9*, D3DCOMPOSERECTSOP, INT, INT) { return D3DERR_INVALIDCALL; }
