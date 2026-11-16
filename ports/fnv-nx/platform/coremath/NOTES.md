# coremath root

CORE-MATH (upstream a44b8b08c3e3feae8b51a9684b50464f9f24fb15, mirror fnv-nx-build `tools/core-math`) binary64 functions that are the targets of the x87
transcendentals: f2xm1 -> exp2m1, fyl2x -> log2, fyl2xp1 -> log2p1, fptan -> tan, fpatan -> atan2, fsin -> sin, fcos -> cos. The function set is the argument list of
the generated stage in build.json and nowhere else.

`-frounding-math` (root flag, after the profile flags): CORE-MATH's README, "to zero or downward. This requires the option -frounding-math on some compilers": the
functions are correctly rounded in every rounding mode, and the lifted code calls each under both RN and RZ, so the compiler must not fold a constant of the
function (for example exp2m1's -1.0+0x1p-54) in round-to-nearest. It is a flag of this root, not a global one.
