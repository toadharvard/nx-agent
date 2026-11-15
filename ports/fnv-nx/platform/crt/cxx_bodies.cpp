/* cxx_bodies.cpp - the bodies of cxx_types.tsv: Wine 9.0 dlls/msvcrt/cpp.c and cppexcept.h (CREATE_EXCEPTION_OBJECT, CREATE_TYPE_INFO_VTABLE),
 * _MSVCR_VER >= 80, each function the one citation of its row. The objects are the exe's x86 layout (fnvnx_cxx.h); the functions are
 * reached from guest code through the thunks that gen_cxx.py generates, as thiscall entries.
 *
 * operator delete of the exe's CRT is c_free (the one heap: msvcrt_exports.cpp). throw_bad_alloc ends in _CxxThrowException, the
 * dispatch through the lifted frames (the exceptions layer, not defined here). */
#include "fnvnx_cxx.h"

#include <stdlib.h>
#include <string.h>

#include "../com/combridge.h"
#include "../com/combridge_cxx.gen.h"

extern "C" void c_free(void *p);
extern "C" void _CxxThrowException(void *obj, const void *type) __attribute__((noreturn, nothrow));

static fnvnx_exception *exception_ctor(fnvnx_exception *self, const char *str, const void *const *vtbl)   /* __exception_ctor */
{
    if (str) {
        size_t len = strlen(str) + 1;
        self->name = (char *)malloc(len);
        memcpy(self->name, str, len);
        self->do_free = 1;
    } else {
        self->name = NULL;
        self->do_free = 0;
    }
    self->vtable = vtbl;
    return self;
}

static fnvnx_exception *exception_copy_ctor(fnvnx_exception *self, const fnvnx_exception *rhs, const void *const *vtbl)   /* __exception_copy_ctor */
{
    if (rhs->do_free) {
        exception_ctor(self, rhs->name, vtbl);
    } else {
        *self = *rhs;
        self->vtable = vtbl;
    }
    return self;
}

static void exception_dtor(fnvnx_exception *self)   /* exception_dtor */
{
    if (self->do_free) free(self->name);
}

extern "C" {

void *fnvnx_cxx_type_info_vector_dtor(fnvnx_type_info *self, unsigned flags)   /* type_info_vector_dtor */
{
    if (flags & 2) {
        /* an array, with the number of elements stored before the first object */
        int *ptr = (int *)self - 1;
        for (int i = *ptr - 1; i >= 0; i--) free(self[i].name);
        free(ptr);
    } else {
        free(self->name);
        if (flags & 1) free(self);
    }
    return self;
}

void *fnvnx_cxx_exception_vector_dtor(fnvnx_exception *self, unsigned flags)   /* exception_vector_dtor */
{
    if (flags & 2) {
        int *ptr = (int *)self - 1;
        for (int i = *ptr - 1; i >= 0; i--) exception_dtor(self + i);
        c_free(ptr);
    } else {
        exception_dtor(self);
        if (flags & 1) c_free(self);
    }
    return self;
}

const char *fnvnx_cxx_exception_what(fnvnx_exception *self)   /* exception_what */
{
    return self->name ? self->name : "Unknown exception";
}

fnvnx_exception *fnvnx_cxx_exception_copy_ctor(fnvnx_exception *self, const fnvnx_exception *rhs)   /* exception_copy_ctor */
{
    return exception_copy_ctor(self, rhs, nxcom_cxx_exception_vtable);
}

fnvnx_exception *fnvnx_cxx_bad_alloc_copy_ctor(fnvnx_exception *self, const fnvnx_exception *rhs)   /* bad_alloc_copy_ctor */
{
    return exception_copy_ctor(self, rhs, nxcom_cxx_bad_alloc_vtable);
}

void fnvnx_cxx_bad_alloc_dtor(fnvnx_exception *self)   /* bad_alloc_dtor */
{
    exception_dtor(self);
}

/* throw_bad_alloc: Wine 9.0 dlls/msvcrt/cpp.c. The records hold their keys once nxcom::init has resolved them. */
void throw_bad_alloc(void)   /* declared in fnvnx_cxx.h */
{
    nxcom::init();
    fnvnx_exception e;
    exception_ctor(&e, "bad allocation", nxcom_cxx_bad_alloc_vtable);
    _CxxThrowException(&e, &nxcom_cxx_bad_alloc_throw);
}

}
