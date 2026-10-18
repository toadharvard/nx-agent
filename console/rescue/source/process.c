// SPDX-License-Identifier: MIT
#include <switch.h>
#include "rescue.h"
uint32_t worker_query(uint64_t *pid) {
    Result rc=pmdmntInitialize();if(R_FAILED(rc))return rc;
    rc=pmdmntGetProcessId(pid,WORKER_ID);pmdmntExit();return rc;
}
uint32_t worker_stop(void) {
    Result rc=pmshellInitialize();if(R_FAILED(rc))return rc;
    rc=pmshellTerminateProgram(WORKER_ID);pmshellExit();return rc;
}
uint32_t worker_start(uint64_t *pid) {
    Result rc=pmshellInitialize();if(R_FAILED(rc))return rc;
    NcmProgramLocation loc={.program_id=WORKER_ID,.storageID=NcmStorageId_None};
    rc=pmshellLaunchProgram(0,&loc,pid);pmshellExit();return rc;
}
