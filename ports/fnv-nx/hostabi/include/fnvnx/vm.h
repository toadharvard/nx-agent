#ifndef FNVNX_VM_H
#define FNVNX_VM_H
/* The guest address space's two sizes, defined once (hostapi.h's VM services work in these units). Everything that needs a page or a
 * reservation granule includes this header; no file restates the numbers. */
#define FNVNX_PAGE_SIZE 0x1000u     /* the page: the unit of every host VM service (4 KiB, hostapi.h) and of the PE section alignment */
#define FNVNX_VM_GRANULE 0x10000u   /* the allocation granularity of a reservation (the Win32 64 KiB granule) */
#endif
