# L6 R11 enum census (measurement only). Rebuild ./enum_run.sh. Universe = d3d9types.h (+d3d9.h flags). Columns: members(no FORCE_DWORD) covered ignored fallback mark skip notreach
Scope: translator d3d9_state.c + exec d3d9_exec.cpp + front/gl/host consumers. Not read: render/nine (guest D3D9 front end), shader-side use of the exec UBO.
```
D3DRENDERSTATETYPE                  103    44   59    0    0    0    0
D3DSAMPLERSTATETYPE                  13    11    2    0    0    0    0
D3DTEXTURESTAGESTATETYPE             18     0    0    0    0   18    0
D3DTEXTUREOP                         26     0    0    0    0   26    0
D3DTA + D3DTSS_TCI + TEXTURETRANSFORMFLAGS   21     0    0    0    0   21    0
D3DDECLTYPE                          18    16    0    2    0    0    0
D3DDECLUSAGE (member)                14     0    0   13    0    1    0
D3DDECLUSAGE x index 0..15          224    16    0  192    0   16    0
D3DDECLMETHOD                         7     1    6    0    0    0    0
D3DPRIMITIVETYPE                      6     6    0    0    0    0    0
D3DFVF bits + TEXCOORDSIZE           64     0    0    0    0   64    0
D3DTRANSFORMSTATETYPE (10 + 256 WORLD)  266     0    0    0    0  266    0
D3DLIGHTTYPE                          3     0    0    0    0    3    0
D3DMATERIALCOLORSOURCE                3     0    3    0    0    0    0
D3DFOGMODE (table fog: all covered)    4     0    4    0    0    0    0
D3DCLIPPLANE bits                     6     0    6    0    0    0    0
D3DFILLMODE + D3DCULL                 6     6    0    0    0    0    0
D3DSHADEMODE                          3     1    1    1    0    0    0
D3DCMPFUNC                            8     8    0    0    0    0    0
D3DSTENCILOP                          8     8    0    0    0    0    0
D3DBLEND                             17    17    0    0    0    0    0
D3DBLENDOP                            5     5    0    0    0    0    0
D3DTEXTUREADDRESS                     5     5    0    0    0    0    0
D3DTEXTUREFILTERTYPE                  9     4    0    5    0    0    0
D3DZBUFFERTYPE                        3     2    0    1    0    0    0
D3DCOLORWRITEENABLE bits              4     4    0    0    0    0    0
D3DWRAPCOORD+D3DWRAP bits             7     0    7    0    0    0    0
D3DCLEAR flags                        3     3    0    0    0    0    0
D3DMULTISAMPLE_TYPE                  17     1    0    0    0    0   16
D3DSWAPEFFECT + D3DBACKBUFFER_TYPE    8     0    0    0    0    0    8
D3DLOCK + D3DUSAGE flags             29     0    0    0    0    0   29
D3DQUERYTYPE + D3DSTATEBLOCKTYPE     18     0    0    0    0    0   18
D3DRESOURCETYPE                       7     5    0    0    0    2    0
D3DPOOL                               4     0    0    0    0    0    4
D3DCUBEMAP_FACES                      6     6    0    0    0    0    0
D3DPATCHEDGESTYLE+DEGREETYPE+BASISTYPE    9     0    6    0    0    0    3
D3DVERTEXBLENDFLAGS + DEBUGMONITORTOKENS    8     0    8    0    0    0    0
D3DFORMAT texture-role (T+RT)        54    34    0    0   20    0    0
D3DFORMAT render-target-role         23    20    0    0    3    0    0
D3DFORMAT depth-role (+INTZ/DF16/DF24/RAWZ)   15     9    0    0    6    0    0
D3DFORMAT vertex/index-role           3     2    0    0    0    0    1
other: shader tokens, caps/create/present flags  220     0    0    0    0    0  220
```
RS detail: 44 of 103 D3DRS_* change translated output and reach a consumer; 59 ignored = 6 translated but unconsumed (SHADEMODE,CLIPPLANEENABLE,POINTSIZE,POINTSIZE_MIN,POINTSPRITEENABLE,POINTSIZE_MAX) + 53 stored, never read. Point sprites/scale: 9 states (POINTSIZE/MIN/MAX, SPRITEENABLE, SCALEENABLE, SCALE_A..C) all ignored; N-patch/tessellation: 9 states ignored; user clip planes: no plane data path.
Sampler: 13 states x 16 = 208 pairs, 176 covered, 32 ignored (ELEMENTINDEX, DMAPOFFSET). TSS: 18 x 8 stages = 144 pairs, all skip_path. Fixed function absent: every draw without VS+PS is skipped.
Skip/Fail: enum_skips.tsv = 13 Skip( sites, 52 Fail( sites (2 unsupported, 18 limit, 32 malformed), 12 silent-drop sites. Probe: 8610 cases, 0 static/dynamic mismatches. Per state x value: enum_coverage_sv.tsv.
Undetermined: RT-role format list is from D3D9 docs memory; CONVOLUTIONMONO validity per sampler state; whether WRAPn applies to shader texcoords; shader-side handling of exec UBO.
