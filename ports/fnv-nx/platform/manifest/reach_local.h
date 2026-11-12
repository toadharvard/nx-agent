/* reach_local.h - TEMPORARY numbering of the reachability facts this platform layer references (see reach_requests.tsv).
 * The graph gate (loader lead) generates the real fact numbers from the exe and module facts; until its header exists
 * these local ids stand in for them. One enum member per row of reach_requests.tsv, same order, same name. */
#ifndef FNVNX_PLATFORM_REACH_LOCAL_H
#define FNVNX_PLATFORM_REACH_LOCAL_H
enum fnvnx_reach_local {
    /* CreateFileMappingA arg 0 (hFile) is never INVALID_HANDLE_VALUE: no paging-file-backed section exists */
    REACH_KERNEL32_CreateFileMappingA_HFILE = 1,
    /* CreateFileMappingA arg 2 (flProtect) never has the PAGE_READWRITE (0x04) or PAGE_EXECUTE_READWRITE (0x40) bit:
     * no file-backed writable section exists */
    REACH_KERNEL32_CreateFileMappingA_PROT = 2,
    /* CreateFileMappingA arg 0 comes from a CreateFileA/W open with neither write access nor FILE_SHARE_WRITE: a
     * private-copy section never maps a file a writer can change */
    REACH_KERNEL32_CreateFileMappingA_WRITER = 3,
};
#endif
