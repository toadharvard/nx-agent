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

#include "fnvnx_crt.h"   /* _invalid_parameter_noinfo */

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

/* FNVNX_MSVCSTL_AUX (STL1 defines it in a unit that derives a container of its own from
 * std::_Container_base_aux_alloc_real, e.g. a hand-written vector<void*>): the _SECURE_SCL container base of
 * <xutility> (_Container_base_aux, its proxy _Aux_cont, _Container_base_aux_alloc_real<A>: proxy pointer and rebound
 * allocator, 8 bytes on ILP32 as on x86), <xmemory>'s _Destroy_range, <memory>'s
 * stdext::unchecked_uninitialized_copy and <vector>'s checked iterators (_Vector_const_iterator, _Vector_iterator:
 * proxy pointer + element pointer, constructed from (pointer, container)). STL1 spells vector<T>::const_iterator and
 * ::iterator as these in such a unit: libstdc++'s iterators take no container. The checks are the release forms of
 * _SECURE_SCL=1 (_SCL_SECURE_VALIDATE above); the container they read is laid out as MSVC's vector
 * (_Vector_val: base, _Alval, then _Myfirst / _Mylast / _Myend). */
#if defined(__cplusplus) && defined(FNVNX_MSVCSTL_AUX) && !defined(FNVNX_MSVCSTL_AUX_H)
#define FNVNX_MSVCSTL_AUX_H
#include <cstddef>
#include <cstring>
#include <iterator>
#include <memory>
#include <new>

namespace std {
class _Container_base_aux;

class _Aux_cont {   /* the container's proxy: iterators point at it, it points at the container */
public:
    explicit _Aux_cont(const _Container_base_aux *const _Pcont) : _Mycontainer(_Pcont) {}
    const _Container_base_aux *_Getcont() const { return _Mycontainer; }
private:
    const _Container_base_aux *_Mycontainer;
};

class _Container_base_aux {
public:
    _Container_base_aux() {}                                /* _Container_base_aux_alloc_real sets _Myownedaux */
    _Container_base_aux(const _Container_base_aux &) {}
    _Container_base_aux &operator=(const _Container_base_aux &) { return *this; }   /* keeps _Myownedaux */
    ~_Container_base_aux() {}                               /* _Container_base_aux_alloc_real frees it */
    void _Swap_aux(_Container_base_aux &_Right)
    {
        _Aux_cont *_Tmp = _Myownedaux; _Myownedaux = _Right._Myownedaux; _Right._Myownedaux = _Tmp;
        _Aux_cont _Tmpc = *_Myownedaux; *_Myownedaux = *_Right._Myownedaux; *_Right._Myownedaux = _Tmpc;
    }
protected:
    friend class _Iterator_base_aux;
    _Aux_cont *_Myownedaux;
};

template <class _Alloc> class _Container_base_aux_alloc_real : public _Container_base_aux {
protected:
    explicit _Container_base_aux_alloc_real(_Alloc _Al) : _Alaux(_Al)
    {
        _Myownedaux = new (_Alaux.allocate(1)) _Aux_cont(this);
    }
    _Container_base_aux_alloc_real(const _Container_base_aux_alloc_real &_Right) : _Alaux(_Right._Alaux)
    {
        _Myownedaux = new (_Alaux.allocate(1)) _Aux_cont(this);
    }
    _Container_base_aux_alloc_real &operator=(const _Container_base_aux_alloc_real &) { return *this; }
    ~_Container_base_aux_alloc_real()
    {
        _Myownedaux->~_Aux_cont();
        _Alaux.deallocate(_Myownedaux, 1);
    }
    typename allocator_traits<_Alloc>::template rebind_alloc<_Aux_cont> _Alaux;   /* allocates the proxy */
};

class _Iterator_base_aux {
public:
    _Iterator_base_aux() : _Myaux(0) {}
    void _Set_container(const _Container_base_aux *const _Container) { _Myaux = _Container->_Myownedaux; }
    const _Container_base_aux *_Getmycont() const { return _Myaux ? _Myaux->_Getcont() : 0; }
    bool _Same_container(const _Iterator_base_aux &_Other) const { return _Myaux == _Other._Myaux; }
    bool _Has_container() const { return _Myaux != 0; }
private:
    const _Aux_cont *_Myaux;
};

/* MSVC's vector<_Ty, _Alloc> as its checked iterators read it (_Vector_val, then the three pointers) */
template <class _Ty, class _Alloc> struct _Vector_val_layout : _Container_base_aux_alloc_real<_Alloc> {
    FNVNX_MSVC_BASE_END(_Container_base_aux_alloc_real<_Alloc>)
    typename allocator_traits<_Alloc>::template rebind_alloc<_Ty> _Alval;
    _Ty *_Myfirst, *_Mylast, *_Myend;
};

template <class _Ty, class _Alloc> class _Vector_const_iterator : public _Iterator_base_aux {
public:
    typedef _Vector_const_iterator<_Ty, _Alloc> _Myt;
    typedef _Vector_val_layout<_Ty, _Alloc> _Myvec;
    typedef _Ty *_Tptr;
    typedef random_access_iterator_tag iterator_category;
    typedef _Ty value_type;
    typedef ptrdiff_t difference_type;
    typedef const _Ty *pointer;
    typedef const _Ty &reference;
    typedef _Range_checked_iterator_tag _Checked_iterator_category;
    typedef _Tptr _Inner_type;          /* an array of _Ty: _Ptr_cat sees the element pointer */
    typedef pointer _Checked_iterator_base_type;

    _Vector_const_iterator() { _Myptr = 0; }
    _Vector_const_iterator(_Tptr _Ptr, const _Container_base_aux *_Pvector)
    {
        _SCL_SECURE_VALIDATE(_Pvector == 0 || (_Vec(_Pvector)->_Myfirst <= _Ptr && _Ptr <= _Vec(_Pvector)->_Mylast));
        this->_Set_container(_Pvector);
        _Myptr = _Ptr;
    }
    _Checked_iterator_base_type _Checked_iterator_base() const { return _Myptr; }
    void _Checked_iterator_assign_from_base(_Checked_iterator_base_type _Base) { _Myptr = const_cast<_Tptr>(_Base); }

    reference operator*() const
    {
        _SCL_SECURE_VALIDATE(this->_Has_container());
        _SCL_SECURE_VALIDATE_RANGE(_Myptr < _Vec(this->_Getmycont())->_Mylast);
        return *_Myptr;
    }
    pointer operator->() const { return &**this; }
    _Myt &operator++()
    {
        _SCL_SECURE_VALIDATE(this->_Has_container());
        _SCL_SECURE_VALIDATE_RANGE(_Myptr < _Vec(this->_Getmycont())->_Mylast);
        ++_Myptr;
        return *this;
    }
    _Myt operator++(int) { _Myt _Tmp = *this; ++*this; return _Tmp; }
    _Myt &operator--()
    {
        _SCL_SECURE_VALIDATE(this->_Has_container());
        _SCL_SECURE_VALIDATE_RANGE(_Myptr > _Vec(this->_Getmycont())->_Myfirst);
        --_Myptr;
        return *this;
    }
    _Myt operator--(int) { _Myt _Tmp = *this; --*this; return _Tmp; }
    _Myt &operator+=(difference_type _Off)
    {
        _SCL_SECURE_VALIDATE(this->_Has_container());
        _SCL_SECURE_VALIDATE_RANGE(_Myptr + _Off <= _Vec(this->_Getmycont())->_Mylast &&
                                   _Myptr + _Off >= _Vec(this->_Getmycont())->_Myfirst);
        _Myptr += _Off;
        return *this;
    }
    _Myt operator+(difference_type _Off) const { _Myt _Tmp = *this; return _Tmp += _Off; }
    _Myt &operator-=(difference_type _Off) { return *this += -_Off; }
    _Myt operator-(difference_type _Off) const { _Myt _Tmp = *this; return _Tmp -= _Off; }
    difference_type operator-(const _Myt &_Right) const { _Compat(_Right); return _Myptr - _Right._Myptr; }
    reference operator[](difference_type _Off) const { return *(*this + _Off); }
    bool operator==(const _Myt &_Right) const { _Compat(_Right); return _Myptr == _Right._Myptr; }
    bool operator!=(const _Myt &_Right) const { return !(*this == _Right); }
    bool operator<(const _Myt &_Right) const { _Compat(_Right); return _Myptr < _Right._Myptr; }
    bool operator>(const _Myt &_Right) const { return _Right < *this; }
    bool operator<=(const _Myt &_Right) const { return !(_Right < *this); }
    bool operator>=(const _Myt &_Right) const { return !(*this < _Right); }
    friend _Myt operator+(difference_type _Off, const _Myt &_Right) { return _Right + _Off; }

    void _Compat(const _Myt &_Right) const
    {
        _SCL_SECURE_VALIDATE(this->_Has_container() && this->_Same_container(_Right));
    }
    static const _Myvec *_Vec(const _Container_base_aux *_Pcont) { return static_cast<const _Myvec *>(_Pcont); }

    _Tptr _Myptr;
};

template <class _Ty, class _Alloc> class _Vector_iterator : public _Vector_const_iterator<_Ty, _Alloc> {
public:
    typedef _Vector_iterator<_Ty, _Alloc> _Myt;
    typedef _Vector_const_iterator<_Ty, _Alloc> _Mybase;
    typedef _Ty *pointer;
    typedef _Ty &reference;
    typedef typename _Mybase::difference_type difference_type;
    typedef pointer _Checked_iterator_base_type;

    _Vector_iterator() {}
    _Vector_iterator(pointer _Ptr, const _Container_base_aux *_Pvector) : _Mybase(_Ptr, _Pvector) {}
    _Checked_iterator_base_type _Checked_iterator_base() const { return this->_Myptr; }
    void _Checked_iterator_assign_from_base(_Checked_iterator_base_type _Base) { this->_Myptr = _Base; }

    reference operator*() const { return (reference)**(const _Mybase *)this; }
    pointer operator->() const { return &**this; }
    _Myt &operator++() { ++*(_Mybase *)this; return *this; }
    _Myt operator++(int) { _Myt _Tmp = *this; ++*this; return _Tmp; }
    _Myt &operator--() { --*(_Mybase *)this; return *this; }
    _Myt operator--(int) { _Myt _Tmp = *this; --*this; return _Tmp; }
    _Myt &operator+=(difference_type _Off) { (*(_Mybase *)this) += _Off; return *this; }
    _Myt operator+(difference_type _Off) const { _Myt _Tmp = *this; return _Tmp += _Off; }
    _Myt &operator-=(difference_type _Off) { return *this += -_Off; }
    _Myt operator-(difference_type _Off) const { _Myt _Tmp = *this; return _Tmp -= _Off; }
    difference_type operator-(const _Mybase &_Right) const { return *(const _Mybase *)this - _Right; }
    reference operator[](difference_type _Off) const { return *(*this + _Off); }
    friend _Myt operator+(difference_type _Off, const _Myt &_Right) { return _Right + _Off; }
};

/* _CHECKED_BASE of a vector iterator is its element pointer; _Ptr_cat looks at the element pointer (_Inner_type) */
template <class _Ty, class _Alloc> inline const _Ty *_Checked_base(const _Vector_const_iterator<_Ty, _Alloc> &_It)
{
    return _It._Myptr;
}
template <class _Ty, class _Alloc> inline _Ty *_Checked_base(const _Vector_iterator<_Ty, _Alloc> &_It)
{
    return _It._Myptr;
}
template <class _Ty, class _Alloc, class _T2> struct _Ptr_cat_helper<_Vector_const_iterator<_Ty, _Alloc>, _T2> {
    typedef typename _Ptr_cat_helper<_Ty *, _T2>::_Ptr_cat _Ptr_cat;
};
template <class _Ty, class _Alloc, class _T2> struct _Ptr_cat_helper<_Vector_iterator<_Ty, _Alloc>, _T2> {
    typedef typename _Ptr_cat_helper<_Ty *, _T2>::_Ptr_cat _Ptr_cat;
};

/* <xmemory>: destroy [_First, _Last) through the allocator; nothing to do for scalars */
template <class _Ty, class _Alloc>
inline void _Destroy_range(_Ty *_First, _Ty *_Last, _Alloc &_Al, _Nonscalar_ptr_iterator_tag)
{
    for (; _First != _Last; ++_First) allocator_traits<_Alloc>::destroy(_Al, _First);
}
template <class _Ty, class _Alloc> inline void _Destroy_range(_Ty *, _Ty *, _Alloc &, _Scalar_ptr_iterator_tag) {}
template <class _Ty, class _Alloc> inline void _Destroy_range(_Ty *_First, _Ty *_Last, _Alloc &_Al)
{
    _Destroy_range(_First, _Last, _Al, _Ptr_cat(_First, _Last));
}

/* <memory>: copy-construct [_First, _Last) into raw storage at _Dest; scalars are moved as bytes (memmove_s) */
template <class _InIt, class _FwdIt, class _Alloc>
inline _FwdIt _Uninit_copy(_InIt _First, _InIt _Last, _FwdIt _Dest, _Alloc &_Al, _Nonscalar_ptr_iterator_tag,
                           _Range_checked_iterator_tag)
{
    _FwdIt _Next = _Dest;
    try {
        for (; _First != _Last; ++_Dest, ++_First) allocator_traits<_Alloc>::construct(_Al, &*_Dest, *_First);
    } catch (...) {
        for (; _Next != _Dest; ++_Next) allocator_traits<_Alloc>::destroy(_Al, &*_Next);
        throw;
    }
    return _Dest;
}
template <class _Ty1, class _Ty2, class _Alloc>
inline _Ty2 _Uninit_copy(_Ty1 _First, _Ty1 _Last, _Ty2 _Dest, _Alloc &, _Scalar_ptr_iterator_tag,
                         _Range_checked_iterator_tag)
{
    size_t _Count = (size_t)(_Last - _First);
    _Ty2 _Result = _Dest + _Count;
    if (_Count > 0) memmove(&*_Dest, &*_First, _Count * sizeof(*_First));
    return _Result;
}
} /* namespace std */

namespace stdext {
template <class _InIt, class _FwdIt, class _Alloc>
inline _FwdIt unchecked_uninitialized_copy(_InIt _First, _InIt _Last, _FwdIt _Dest, _Alloc &_Al)
{
    return std::_Uninit_copy(std::_Checked_base(_First), std::_Checked_base(_Last), _Dest, _Al,
                             std::_Ptr_cat(_First, _Dest), std::_Range_checked_iterator_tag());
}
} /* namespace stdext */

#ifdef __ILP32__   /* the x86 layout the decomp's own copy of this vector has (blk_00b20000_od_ob1_rtm: AsVector) */
typedef std::_Vector_val_layout<void *, std::allocator<void *> > fnvnx_stl1_vector_layout;
typedef std::_Vector_const_iterator<void *, std::allocator<void *> > fnvnx_stl1_vector_iterator;
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Winvalid-offsetof"
static_assert(sizeof(std::_Container_base_aux_alloc_real<std::allocator<void *> >) == 8, "STL1: container base");
static_assert(__builtin_offsetof(fnvnx_stl1_vector_layout, _Alval) == 8 &&
              __builtin_offsetof(fnvnx_stl1_vector_layout, _Myfirst) == 12 &&
              __builtin_offsetof(fnvnx_stl1_vector_layout, _Mylast) == 16 &&
              __builtin_offsetof(fnvnx_stl1_vector_layout, _Myend) == 20, "STL1: MSVC vector layout");
static_assert(sizeof(fnvnx_stl1_vector_iterator) == 8 && sizeof(std::_Vector_iterator<void *, std::allocator<void *> >) == 8 &&
              __builtin_offsetof(fnvnx_stl1_vector_iterator, _Myptr) == 4, "STL1: iterator = _Myaux, _Myptr");
#pragma GCC diagnostic pop
#endif
#endif
