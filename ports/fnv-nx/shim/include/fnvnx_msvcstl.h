/* fnvnx_msvcstl.h - the internals of the MSVC 2008 (Dinkumware) C++ library that decomp code calls by name: the
 * _SECURE_SCL checks and the iterator tags of the checked copy algorithms (<xutility>). libstdc++ has none of them.
 * prepare.py STL1 includes this header in a unit that uses them without defining them.
 *
 * _SCL_SECURE_VALIDATE / _RANGE are the release-build forms (_SECURE_SCL=1, the VS2008 default): a failed check calls
 * the CRT's invalid-parameter handler (_invalid_parameter_noinfo, platform/crt), as the game's code does.
 * _Checked_base returns an unchecked iterator itself (the decomp passes raw pointers); _Ptr_cat picks
 * _Scalar_ptr_iterator_tag for pointers to the same scalar type and pointer-to-pointer pairs, as <xutility> does. */
#ifndef FNVNX_MSVCSTL_H
#define FNVNX_MSVCSTL_H
#ifdef __cplusplus

extern "C" void _invalid_parameter_noinfo(void);

#ifndef _SCL_SECURE_VALIDATE
#define _SCL_SECURE_VALIDATE(cond) { if (!(cond)) { _invalid_parameter_noinfo(); } }
#endif
#ifndef _SCL_SECURE_VALIDATE_RANGE
#define _SCL_SECURE_VALIDATE_RANGE(cond) { if (!(cond)) { _invalid_parameter_noinfo(); } }
#endif

namespace std {
struct _Unchecked_iterator_tag {};
struct _Range_checked_iterator_tag {};
struct _Nonscalar_ptr_iterator_tag {};
struct _Scalar_ptr_iterator_tag {};

template <class _T1, class _T2> struct _Ptr_cat_helper { typedef _Nonscalar_ptr_iterator_tag _Ptr_cat; };
template <class _Ty> struct _Ptr_cat_helper<_Ty **, _Ty **> { typedef _Scalar_ptr_iterator_tag _Ptr_cat; };
template <class _Ty> struct _Ptr_cat_helper<_Ty **, const _Ty **> { typedef _Scalar_ptr_iterator_tag _Ptr_cat; };
template <class _Ty> struct _Ptr_cat_helper<_Ty *const *, _Ty **> { typedef _Scalar_ptr_iterator_tag _Ptr_cat; };
template <class _Ty> struct _Ptr_cat_helper<_Ty *const *, const _Ty **> { typedef _Scalar_ptr_iterator_tag _Ptr_cat; };
#define FNVNX_PTR_CAT_SCALAR(T) \
    template <> struct _Ptr_cat_helper<T *, T *> { typedef _Scalar_ptr_iterator_tag _Ptr_cat; }; \
    template <> struct _Ptr_cat_helper<const T *, T *> { typedef _Scalar_ptr_iterator_tag _Ptr_cat; };
FNVNX_PTR_CAT_SCALAR(bool)
FNVNX_PTR_CAT_SCALAR(char)
FNVNX_PTR_CAT_SCALAR(signed char)
FNVNX_PTR_CAT_SCALAR(unsigned char)
FNVNX_PTR_CAT_SCALAR(wchar_t)
FNVNX_PTR_CAT_SCALAR(short)
FNVNX_PTR_CAT_SCALAR(unsigned short)
FNVNX_PTR_CAT_SCALAR(int)
FNVNX_PTR_CAT_SCALAR(unsigned int)
FNVNX_PTR_CAT_SCALAR(long)
FNVNX_PTR_CAT_SCALAR(unsigned long)
FNVNX_PTR_CAT_SCALAR(float)
FNVNX_PTR_CAT_SCALAR(double)
FNVNX_PTR_CAT_SCALAR(long double)
FNVNX_PTR_CAT_SCALAR(long long)
FNVNX_PTR_CAT_SCALAR(unsigned long long)
#undef FNVNX_PTR_CAT_SCALAR

template <class _T1, class _T2> inline typename _Ptr_cat_helper<_T1, _T2>::_Ptr_cat _Ptr_cat(_T1 &, _T2 &)
{
    typename _Ptr_cat_helper<_T1, _T2>::_Ptr_cat _Cat;
    return _Cat;
}

template <class _Iter> inline _Iter _Checked_base(const _Iter &_It) { return _It; }
} /* namespace std */

#endif
#endif
