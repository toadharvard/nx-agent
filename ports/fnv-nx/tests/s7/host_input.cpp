/* The host definition of fnvnx_input_pump for this test binary. On Horizon the pump polls hid (input_horizon.cpp); a host
 * build has no polled source: keyboard, mouse and touch state is pushed through fnvnx_input_key / _mouse_move /
 * _mouse_button / fnvnx_touch_frame by the test itself, so a pump has nothing to fetch. */
#include "../../platform/input/input.h"

extern "C" void fnvnx_input_pump(void)
{
    /* nothing to fetch: the host sources are push-driven */
}
