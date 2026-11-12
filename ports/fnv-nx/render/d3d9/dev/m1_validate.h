// m1_validate.h: the ONE predicate of draw-time legality (m1_validate.cpp). ValidateDevice of both device classes is this function plus "*pNumPasses = 1".
// CALL SITE for the draw path (not wired here: draw_api.cpp is not part of M1): DrawPrimitive / DrawIndexedPrimitive / DrawPrimitiveUP / DrawIndexedPrimitiveUP would refuse a draw with
// `if (HRESULT h = nx_m1_draw_legal(d)) return h;` at the place of rule D1 (after the declaration test, before nx_draw_exec). Windows draws in these states do not fail (the result is undefined), so wiring it
// is the lead's decision; the predicate is what both would share.
#pragma once
#include "dev_core.h"
HRESULT nx_m1_draw_legal(NxDeviceCore* d);   // D3D_OK = the current state can be rendered by this device in one pass, else the documented ValidateDevice error (m1_validate.cpp lists each row)
