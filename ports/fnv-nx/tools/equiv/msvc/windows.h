/* windows.h - MSVC 2008 stand-in for shim/include/windows.h in S11 equivalence builds (tools/equiv), SDK-WINDOWS.
 *
 * On the target, a unit's own `#include <windows.h>` reaches the shim's windows.h, which is fnvnx_win32.h. Here a
 * prepared unit (fnvnx_equiv_compat.h is force-included into prepared units only) gets the same: the gate's
 * fnvnx_win32.h stand-in, the real SDK adjusted where the target shim deliberately differs (looser parameter types,
 * no A/W macros, ...). Before, such a unit saw the plain SDK and failed where it relies on those differences
 * (SendInput's void *, HANDLE returns stored in integers). The pristine decomp gets the SDK's own <windows.h>.
 */
#ifdef FNVNX_EQUIV_COMPAT_H
#include "fnvnx_win32.h"
#else
#include "fnvnx_equiv_sdk_windows.h"
#endif
