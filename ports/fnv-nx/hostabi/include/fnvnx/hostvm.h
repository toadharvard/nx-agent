#ifndef FNVNX_HOSTVM_H
#define FNVNX_HOSTVM_H
/* The sizes and limits of the host's VM and section services (hostapi.h 4..8, 11..14, 37), defined once. The guest (platform, loader), the host's
 * services (host_vm.inc, host_section.inc) and the boot machine's model all read them from here; no file restates a number. */
#define FNVNX_PAGE_SIZE 0x1000u        /* the page: the unit of every host VM service (4 KiB) and of the PE section alignment */
#define FNVNX_VM_ALIGN 0x10000ull      /* the allocation granularity of a reservation (Windows' 64 KiB granule) */
#define FNVNX_VM_FLOOR 0x10000ull      /* the guest's low 64 KiB stay unmapped: the lowest address a reservation may take */
#define FNVNX_MAP_FIXED_MAX 512u       /* map_fixed ranges live at once (more -> FNVNX_HE_NOMEM) */
#define FNVNX_SEC_SECTIONS 128u        /* section objects live at once (section_create, more -> FNVNX_SEC_E_FULL) */
#define FNVNX_SEC_VIEWS 512u           /* section views live at once (section_map, more -> FNVNX_SEC_E_FULL) */
/* the largest section: FNVNX_SEC_MAX_SIZE (hostapi.h) */
#endif
