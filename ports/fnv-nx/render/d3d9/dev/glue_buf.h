// glue_buf.h: the GPU mirror of vertex / index buffers (glue_buf.cpp). The CPU memory of NxBufCore stays the truth for locks; the mirror is the 4 KiB aligned block the backend reads.
#pragma once
#include "res_core.h"
// Makes the mirror current: creates it (backend memory block over a 4 KiB aligned copy) on first use, then copies the dirty byte range (NxBufCore::take_dirty) and returns the block id for
// NxOp_BindVtxN / NxOp_BindIdx (offset = byte offset in the buffer). Hazard rule: the backend reads the block when the stream is replayed (submit), so a CPU write after a draw that references the
// block must not change what that draw sees: the caller (the draw path) submits the stream (nxs_flush) before it writes the mirror of a buffer the pending stream binds, i.e. before this function when
// the buffer has dirty bytes and was bound since the last submit.
// Result 0 = the backend has no memory for the mirror (or for the stream ring): nothing changed, the dirty range is kept; the draw path returns D3DERR_OUTOFVIDEOMEMORY, PreLoad (void) leaves it for the next use.
NX_MUST_USE uint32_t nx_buf_gpu_sync(NxBufCore* b);
