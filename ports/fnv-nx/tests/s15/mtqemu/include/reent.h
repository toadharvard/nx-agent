/* reent.h - stand-in for newlib's per-thread reentrancy struct (mtqemu guest: mlibc.c has no per-thread state). */
#ifndef FNVNX_MTQ_REENT_H
#define FNVNX_MTQ_REENT_H
struct _reent { int _errno; };
extern struct _reent *_impure_ptr;
#define _REENT_INIT_PTR(p) ((p)->_errno = 0)
void _reclaim_reent(struct _reent *);
#endif
