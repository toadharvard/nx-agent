/* fnvnx_log_00703c00 - native variadic replacement of the game's console logger at 0x00703c00 (docs/LINK.md,
 * "Overrides").
 *
 * The decomp defines the logger as void __cdecl (const char *text, const char *context) and hands &context to the
 * console as the argument list: x86 stack varargs. On AArch64 that address is a spilled register and float
 * arguments travel in v registers, so the callers' arguments never reach the text. Here the text is formatted with
 * vsnprintf and passed on with every '%' doubled and no argument list, so the console's own formatter prints it as
 * is. Same conditions as the original: the UI root and its key flag, then an active console or FUN_005b6f70.
 *
 * The game's functions are called by address (link by address: __fnv_<addr> is the native definition, AAPCS64
 * ILP32, `this` first). */
#include <stdarg.h>
#include <stdio.h>

extern void *__fnv_004b7210(void);                     /* the UI root (Modifier *) */
extern unsigned char __fnv_009373f0(void *self);        /* Modifier::GetKey */
extern void *__fnv_0071b160(unsigned char create);       /* ConsoleManager::Instance(bool) */
extern unsigned char __fnv_004a4020(void *self);        /* ConsoleManager::IsConsoleActive */
extern unsigned char __fnv_005b6f70(void);
extern void __fnv_0071d0a0(void *self, const char *format, char *arguments);   /* ConsoleManager::Print */

#define LINE 2048   /* the console copies a line into a 2048-byte buffer */

int fnvnx_log_00703c00(const char *fmt, ...)
{
    char text[LINE], esc[2 * LINE];
    va_list ap;
    unsigned i, o;

    if (!(__fnv_004b7210() && __fnv_009373f0(__fnv_004b7210())))
        return 0;
    if (!((__fnv_0071b160(0) && __fnv_004a4020(__fnv_0071b160(1))) || __fnv_005b6f70()))
        return 0;
    va_start(ap, fmt);
    vsnprintf(text, sizeof text, fmt ? fmt : "", ap);
    va_end(ap);
    for (i = o = 0; text[i] && o + 2 < LINE; i++) {
        if (text[i] == '%')
            esc[o++] = '%';
        esc[o++] = text[i];
    }
    esc[o] = 0;
    __fnv_0071d0a0(__fnv_0071b160(1), esc, 0);
    return 0;
}
