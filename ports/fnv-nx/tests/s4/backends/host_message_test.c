/* host_message_test.c - MessageBoxA's host side (toolchain/host/host_message.inc) executed under qemu-aarch64 with
 * libnx's error applet stubbed: the short and the long message it builds and that it shows the applet. */
#include <stdio.h>
#include <string.h>
#include <switch.h>

static char s_dlg[4096], s_full[4096];
static int s_shown;
static Result s_create_rc;
Result errorApplicationCreate(ErrorApplicationConfig *c, const char *d, const char *f)
{
    memset(c, 0, sizeof *c);
    snprintf(s_dlg, sizeof s_dlg, "%s", d);
    snprintf(s_full, sizeof s_full, "%s", f);
    return s_create_rc;
}
Result errorApplicationShow(ErrorApplicationConfig *c) { (void)c; s_shown++; return 0; }

#include "../../../toolchain/host/host_message.inc"

static int g_fail;
#define T(c) do { if (!(c)) { g_fail++; printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); } } while (0)

int main(void)
{
    T(host_show_message("Fallout: New Vegas", "Failed to initialize renderer.\nYour display doesn't support...") == 0);
    T(s_shown == 1);
    T(!strcmp(s_dlg, "Fallout: New Vegas\nFailed to initialize renderer."));
    T(!strcmp(s_full, "Fallout: New Vegas\n\nFailed to initialize renderer.\nYour display doesn't support..."));
    T(host_show_message(NULL, "only text") == 0 && !strcmp(s_dlg, "only text") && !strcmp(s_full, "only text"));
    char big[5000];
    memset(big, 'x', sizeof big - 1);
    big[sizeof big - 1] = 0;
    T(host_show_message("c", big) == 0 && strlen(s_dlg) == FNVNX_MSG_SHORT - 1 && strlen(s_full) == FNVNX_MSG_LONG - 1);
    s_create_rc = MAKERESULT(Module_Libnx, LibnxError_BadInput);
    T(host_show_message("c", "t") == MAKERESULT(Module_Libnx, LibnxError_BadInput) && s_shown == 3);   /* not shown */
    printf("host_message: %s\n", g_fail ? "FAIL" : "ok");
    return g_fail != 0;
}
