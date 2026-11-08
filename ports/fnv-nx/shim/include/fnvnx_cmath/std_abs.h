/* fnvnx_cmath/std_abs.h - std::abs for a unit that declared it itself in namespace std (`namespace std { float
 * abs(float); }`, 49 sites); prepare.py DECL1 includes this in place of that declaration (DECL1-STDABS).
 * On the target it is <stdlib.h>, as before: libstdc++'s <stdlib.h> declares std::abs for int, long, long long, float,
 * double and long double and brings them into the global namespace. The MSVC branch is the same meaning for the
 * S11 gate: MSVC 2008's <stdlib.h> has only ::abs(int) and declares nothing in std, and its <math.h> would also add
 * the global float overloads of every math function, which libstdc++'s <stdlib.h> does not. */
#ifdef __GNUC__
#pragma GCC system_header
#endif
#ifndef FNVNX_CMATH_std_abs
#define FNVNX_CMATH_std_abs
#if !defined(_MSC_VER)
#include <stdlib.h>
#else
#include <stdlib.h>
#include "fabs.h"
namespace std {
using ::abs;
inline long __cdecl abs(long _X) { return labs(_X); }
inline double __cdecl abs(double _X) { return fabs(_X); }
inline float __cdecl abs(float _X) { return (float)fabs((double)_X); }
inline long double __cdecl abs(long double _X) { return fabs((double)_X); }
}
/* no `using std::abs;` at global scope here: a later <math.h> defines the same global overloads (abs(long),
 * abs(double), abs(float)) itself and would conflict with the using-declaration (bgsprimitive) */
#endif
#endif
