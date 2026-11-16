/* fnv-nx shim: <commctrl.h>. Grown from census misses; see docs/ARCHITECTURE.md. */
#ifndef FNVNX_SHIM_COMMCTRL_H
#define FNVNX_SHIM_COMMCTRL_H
#include "fnvnx_win32.h"

/* Tree-view notifications (the debug tree window of staging/blk_004c0000_od_decl_variant.cpp). Layouts as in the
 * Windows SDK of MSVC 2008 (x86; ILP32 keeps every offset): NMHDR 12 bytes, TVITEMA 40, TVITEMEXA 44 (the members
 * up to iIntegral), NMTREEVIEWA 104, NMTVKEYDOWN 18 (pshpack1 in the SDK). */
typedef struct _TREEITEM *HTREEITEM;
typedef struct tagNMHDR { HWND hwndFrom; UINT_PTR idFrom; UINT code; } NMHDR, *LPNMHDR;
typedef struct tagTVITEMA {
    UINT mask; HTREEITEM hItem; UINT state; UINT stateMask; LPSTR pszText; int cchTextMax; int iImage;
    int iSelectedImage; int cChildren; LPARAM lParam;
} TVITEMA, *LPTVITEMA;
typedef struct tagTVITEMEXA {
    UINT mask; HTREEITEM hItem; UINT state; UINT stateMask; LPSTR pszText; int cchTextMax; int iImage;
    int iSelectedImage; int cChildren; LPARAM lParam; int iIntegral;
} TVITEMEXA, *LPTVITEMEXA;
typedef struct tagNMTREEVIEWA { NMHDR hdr; UINT action; TVITEMA itemOld; TVITEMA itemNew; POINT ptDrag; }
    NMTREEVIEWA, *LPNMTREEVIEWA;
#pragma pack(push, 1)
typedef struct tagTVKEYDOWN { NMHDR hdr; WORD wVKey; UINT flags; } NMTVKEYDOWN, *LPNMTVKEYDOWN;
#pragma pack(pop)
#ifdef __cplusplus
static_assert(sizeof(NMHDR) == 12 && sizeof(TVITEMA) == 40 && sizeof(TVITEMEXA) == 44, "commctrl tree-view layouts");
static_assert(sizeof(NMTREEVIEWA) == 104 && sizeof(NMTVKEYDOWN) == 18, "commctrl tree-view layouts");
#endif
#endif
