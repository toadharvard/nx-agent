/* host_ext.inc - FnvnxHostApi slot ext_call: the registry of host-side modules (< 64), see hostapi.h. Standalone. */
static fnvnx_ext_handler g_ext[64];
int fnvnx_host_register_ext(uint32_t module, fnvnx_ext_handler handler) {
    if (module >= 64 || !handler || g_ext[module]) return -1;
    g_ext[module] = handler;
    return 0;
}
static u64 h_ext_call(u64 module, u64 fn, u64 args, u64 nargs) {
    if (module >= 64 || !g_ext[module]) return FNVNX_EXT_NO_MODULE;
    if (fn > UINT32_MAX) return FNVNX_EXT_NO_SERVICE;   /* no module numbers a service above 32 bits */
    return g_ext[module]((uint32_t)fn, (const uint64_t *)args, nargs > UINT32_MAX ? UINT32_MAX : (uint32_t)nargs);
}

