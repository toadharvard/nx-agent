/* rcpref.h: the identity of the one reference CPU whose RCPSS / RSQRTSS results rt/rcptab.c tabulates.
 * The values are what the CPUID instruction reports (leaf 0 vendor string; leaf 1 EAX: family = base family, plus extended
 * family when base is 0xF; model = model | extended model << 4 when base family is 6 or 0xF; stepping). Included by rt/rcptab.c,
 * tools/rcpcert.c and read by proof/gate_fpexact.py (the gate reads exactly these four #define lines). */
#ifndef FNVL_RCPREF_H
#define FNVL_RCPREF_H
#define FNVL_RCP_REF_VENDOR "GenuineIntel"
#define FNVL_RCP_REF_FAMILY 6
#define FNVL_RCP_REF_MODEL 207
#define FNVL_RCP_REF_STEPPING 2
#endif
