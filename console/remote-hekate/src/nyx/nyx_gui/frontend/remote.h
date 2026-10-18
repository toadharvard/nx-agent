/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef NYX_REMOTE_H
#define NYX_REMOTE_H
#include "remote_core.h"
void nyx_remote_init(void);
void nyx_remote_poll(void);
void nyx_remote_service(void);
void nyx_remote_local_activity(void);
void nyx_remote_suspend(void);
void nyx_remote_resume(void);
#endif
