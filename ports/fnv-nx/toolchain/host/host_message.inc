/* host_message.inc - MessageBoxA on the device (FNVNX_PLAT_MESSAGE, host.c platform_ext): the system error applet
 * with the caption and the text, which blocks until the player presses OK. The applet's short message is the
 * caption and the first line of the text, the details screen the whole text. Standalone (only <switch.h>) so
 * tests/s4/backends runs it under qemu. */
#define FNVNX_MSG_SHORT 512
#define FNVNX_MSG_LONG 2048
static void host_message_compose(const char *caption, const char *text, char *dlg, size_t dn, char *full, size_t fn) {
    if (!caption) caption = "";
    if (!text) text = "";
    size_t first = strcspn(text, "\r\n");
    snprintf(dlg, dn, "%s%s%.*s", caption, *caption && first ? "\n" : "", (int)first, text);
    snprintf(full, fn, "%s%s%s", caption, *caption ? "\n\n" : "", text);
}
static u64 host_show_message(const char *caption, const char *text) {
    static char dlg[FNVNX_MSG_SHORT], full[FNVNX_MSG_LONG];
    host_message_compose(caption, text, dlg, sizeof dlg, full, sizeof full);
    ErrorApplicationConfig c;
    Result rc = errorApplicationCreate(&c, dlg, full);
    if (R_SUCCEEDED(rc)) rc = errorApplicationShow(&c);
    return rc;
}
