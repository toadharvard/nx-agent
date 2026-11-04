# S6n status (native renderer)

## Done
* `include/nx/state.h` - backend-neutral state encoding (NxRenderState, NxDynamicState, shader bits,
  NxSamplerState, NxFormat/NxFormatInfo).
* `front/d3d9_state.{h,c}` - pure D3D9 -> native translator mirroring Gallium Nine 20.1. Tests:
  `make -C tests/s6n` -> `d3d9_state: 390 checks, 0 failed`.

## In progress
* Environment (`env.sh`): fnvnx-a64 toolchain build from source (toolchain/gcc/build.sh, JOBS=2).

## Open problems
* none yet
