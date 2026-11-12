/* fnvnx_vc9.h - the MSVC 2008 (VC9, Dinkumware) containers in their x86 release layout, for the few decomp units
 * that use the real <string> / <vector> / <map> and program against their internals (_Mybase, _Buy, _Myptr,
 * _Vector_val, _String_val, _Mynode, stdext::unchecked_copy ...). prepare.py STL2 spells `std` as `fnvnx_vc9` in
 * those units and includes this header in place of the STL headers.
 *
 * Layouts (ILP32 as on x86; static_asserted below, checked against FalloutNV.exe 1.4.0.525):
 *   allocator<T>  empty (1 byte as a member)
 *   string        _Alval 0, _Bx 4 (16: buffer, or pointer when _Myres >= 16), _Mysize 0x14, _Myres 0x18 = 28
 *                 (the exe: `cmp [r+0x18], 0x10` in 128 places; a string by value takes `sub esp, 0x1c`)
 *   vector<T>     _Myownedaux 0, _Alaux 4, _Alval 8, _Myfirst 0xc, _Mylast 0x10, _Myend 0x14 = 24
 *                 (0x00b31270 vector<int>(): the proxy allocated and stored at +0; 0x00abfa90 _Insert_n: +0xc/0x10/0x14)
 *   map / _Tree   _Myownedaux 0, _Alaux 4, comp 8, _Alnod 0xc, _Alptr 0x10, _Alval 0x14, _Myhead 0x18, _Mysize 0x1c
 *                 = 32; node _Left 0, _Parent 4, _Right 8, _Myval 0xc, then _Color, _Isnil
 *                 (0x00acb200 _Tree::find: _Myhead at +0x18, the key at node+0xc)
 *   iterators     vector / map: proxy pointer, element or node pointer (8); string: container pointer, pointer (8)
 *
 * The algorithms are VC9's (growth by half, _Grow / _Copy / _Tidy of the string, the red-black tree of <xtree>), the
 * checks the release forms of _SECURE_SCL=1: a failed check calls the CRT's invalid-parameter handler
 * (_invalid_parameter_noinfo, platform/crt). Elements of vector / string / map type move by swap (VC9's
 * _Swap_move_tag). Every other name of the namespace std (pair, less, the exceptions, the algorithms) is libstdc++'s,
 * through the using-directive: the standard exceptions stay the classes the rest of the port throws and catches, and
 * a VC9 string converts to std::string where one is wanted (std::runtime_error(s)). size_type is unsigned int, as
 * MSVC x86 spells it (the decomp's explicit specializations name it that way). */
#ifndef FNVNX_VC9_H
#define FNVNX_VC9_H
#ifdef __cplusplus

#include <cstddef>
#include <cstring>
#include <cwchar>
#include <new>
#include <iterator>
#include <utility>
#include <functional>
#include <memory>
#include <stdexcept>
#include <algorithm>
#include <string>
#include <type_traits>

#include "fnvnx_crt.h"   /* _invalid_parameter_noinfo */

#ifndef FNVNX_MSVC_BASE_END   /* fnvnx_compat.h: a derived class's members start at sizeof(base), as under MSVC */
template <unsigned A> struct fnvnx_int_of_align;
template <> struct fnvnx_int_of_align<1> { typedef unsigned char type; };
template <> struct fnvnx_int_of_align<2> { typedef unsigned short type; };
template <> struct fnvnx_int_of_align<4> { typedef unsigned int type; };
template <> struct fnvnx_int_of_align<8> { typedef unsigned long long type; };
#define FNVNX_MSVC_BASE_END(...) typename fnvnx_int_of_align<alignof(__VA_ARGS__)>::type : 0;
#endif

/* <yvals.h> / <xstddef> spellings the decomp's own copies of library code use */
#ifndef _TRY_BEGIN
#define _TRY_BEGIN try {
#define _CATCH(x) } catch (x) {
#define _CATCH_ALL } catch (...) {
#define _CATCH_END }
#define _RAISE(x) throw x
#define _RERAISE throw
#endif
#ifndef _STD
#define _STD ::fnvnx_vc9::
#endif
#ifndef _STDEXT
#define _STDEXT ::fnvnx_vc9::stdext::
#endif
#ifndef _VEC_ITER_BASE
#define _VEC_ITER_BASE(_It) (_It)._Myptr
#endif
#define FNVNX_VC9_VALIDATE(cond) do { if (!(cond)) _invalid_parameter_noinfo(); } while (0)

namespace fnvnx_vc9 {
using namespace ::std;
using ::std::swap;               /* beside the swaps below: fnvnx_vc9::swap(a, b) of any other type */

typedef unsigned int _Sizet;      /* MSVC x86 size_t */
typedef int _Dift;                /* ptrdiff_t */

/* ---------------------------------------------------------------------------------------------- <xmemory> */
template <class _Ty> inline _Ty *_Allocate(_Sizet _Count, _Ty *)
{
    if (_Count <= 0) _Count = 0;
    else if (((_Sizet)(-1) / _Count) < sizeof(_Ty)) throw ::std::bad_alloc();
    return (_Ty *)::operator new(_Count * sizeof(_Ty));
}
template <class _T1, class _T2> inline void _Construct(_T1 *_Ptr, const _T2 &_Val)
{
    void *_Vptr = _Ptr;
    ::new (_Vptr) _T1(_Val);
}
template <class _Ty> inline void _Destroy(_Ty *_Ptr) { _Ptr->~_Ty(); }
template <> inline void _Destroy(char *) {}
template <> inline void _Destroy(wchar_t *) {}

template <class _Ty> class allocator {
public:
    typedef _Ty value_type;
    typedef value_type *pointer;
    typedef value_type &reference;
    typedef const value_type *const_pointer;
    typedef const value_type &const_reference;
    typedef _Sizet size_type;
    typedef _Dift difference_type;
    template <class _Other> struct rebind { typedef allocator<_Other> other; };
    pointer address(reference _Val) const { return &_Val; }
    const_pointer address(const_reference _Val) const { return &_Val; }
    allocator() throw() {}
    allocator(const allocator<_Ty> &) throw() {}
    template <class _Other> allocator(const allocator<_Other> &) throw() {}
    template <class _Other> allocator<_Ty> &operator=(const allocator<_Other> &) { return *this; }
    void deallocate(pointer _Ptr, size_type) { ::operator delete(_Ptr); }
    pointer allocate(size_type _Count) { return ::fnvnx_vc9::_Allocate(_Count, (pointer)0); }
    pointer allocate(size_type _Count, const void *) { return allocate(_Count); }
    void construct(pointer _Ptr, const _Ty &_Val) { ::fnvnx_vc9::_Construct(_Ptr, _Val); }
    void destroy(pointer _Ptr) { ::fnvnx_vc9::_Destroy(_Ptr); }
    _Sizet max_size() const throw()
    {
        _Sizet _Count = (_Sizet)(-1) / sizeof(_Ty);
        return 0 < _Count ? _Count : 1;
    }
};
template <> class allocator<void> {
public:
    typedef void _Ty;
    typedef _Ty *pointer;
    typedef const _Ty *const_pointer;
    typedef _Ty value_type;
    template <class _Other> struct rebind { typedef allocator<_Other> other; };
    allocator() throw() {}
    allocator(const allocator<_Ty> &) throw() {}
    template <class _Other> allocator(const allocator<_Other> &) throw() {}
};
template <class _Ty, class _Other> inline bool operator==(const allocator<_Ty> &, const allocator<_Other> &) throw()
{
    return true;
}
template <class _Ty, class _Other> inline bool operator!=(const allocator<_Ty> &, const allocator<_Other> &) throw()
{
    return false;
}

/* <xutility>: the pointer categories that pick memmove for scalars */
struct _Nonscalar_ptr_iterator_tag {};
struct _Scalar_ptr_iterator_tag {};
template <class _T1, class _T2> struct _Ptr_cat_helper { typedef _Nonscalar_ptr_iterator_tag _Ptr_cat; };
template <class _Ty> struct _Ptr_cat_helper<_Ty *, _Ty *> {
    typedef typename conditional<is_scalar<_Ty>::value, _Scalar_ptr_iterator_tag, _Nonscalar_ptr_iterator_tag>::type
        _Ptr_cat;
};
template <class _Ty> struct _Ptr_cat_helper<const _Ty *, _Ty *> {
    typedef typename _Ptr_cat_helper<_Ty *, _Ty *>::_Ptr_cat _Ptr_cat;
};
template <class _T1, class _T2> inline typename _Ptr_cat_helper<_T1, _T2>::_Ptr_cat _Ptr_cat(const _T1 &, const _T2 &)
{
    return typename _Ptr_cat_helper<_T1, _T2>::_Ptr_cat();
}

/* VC9 moves its own containers by swapping (default-construct, then swap) */
struct _Undefined_move_tag {};
struct _Swap_move_tag {};
template <class _Ty> struct _Move_operation_category { typedef _Undefined_move_tag _Move_cat; };

template <class _Ty, class _Alloc>
inline void _Destroy_range(_Ty *_First, _Ty *_Last, _Alloc &_Al, _Nonscalar_ptr_iterator_tag)
{
    for (; _First != _Last; ++_First) _Al.destroy(_First);
}
template <class _Ty, class _Alloc> inline void _Destroy_range(_Ty *, _Ty *, _Alloc &, _Scalar_ptr_iterator_tag) {}
template <class _Ty, class _Alloc> inline void _Destroy_range(_Ty *_First, _Ty *_Last, _Alloc &_Al)
{
    ::fnvnx_vc9::_Destroy_range(_First, _Last, _Al, ::fnvnx_vc9::_Ptr_cat(_First, _Last));
}

namespace stdext {
using namespace ::fnvnx_vc9;
/* copy / copy_backward / fill on unchecked iterators; scalars by memmove as <xutility> */
template <class _InIt, class _OutIt> inline _OutIt _Copy_opt(_InIt _First, _InIt _Last, _OutIt _Dest, _Nonscalar_ptr_iterator_tag)
{
    for (; _First != _Last; ++_Dest, ++_First) *_Dest = *_First;
    return _Dest;
}
template <class _InIt, class _OutIt> inline _OutIt _Copy_opt(_InIt _First, _InIt _Last, _OutIt _Dest, _Scalar_ptr_iterator_tag)
{
    ptrdiff_t _Off = _Last - _First;
    if (_Off > 0) ::memmove(&*_Dest, &*_First, _Off * sizeof(*_First));
    return _Dest + _Off;
}
template <class _InIt, class _OutIt> inline _OutIt unchecked_copy(_InIt _First, _InIt _Last, _OutIt _Dest)
{
    return _Copy_opt(_First, _Last, _Dest, _Ptr_cat(_First, _Dest));
}
template <class _BidIt1, class _BidIt2>
inline _BidIt2 _Copy_backward_opt(_BidIt1 _First, _BidIt1 _Last, _BidIt2 _Dest, _Nonscalar_ptr_iterator_tag)
{
    while (_First != _Last) *--_Dest = *--_Last;
    return _Dest;
}
template <class _BidIt1, class _BidIt2>
inline _BidIt2 _Copy_backward_opt(_BidIt1 _First, _BidIt1 _Last, _BidIt2 _Dest, _Scalar_ptr_iterator_tag)
{
    ptrdiff_t _Off = _Last - _First;
    _BidIt2 _Result = _Dest - _Off;
    if (_Off > 0) ::memmove(&*_Result, &*_First, _Off * sizeof(*_First));
    return _Result;
}
template <class _BidIt1, class _BidIt2> inline _BidIt2 unchecked_copy_backward(_BidIt1 _First, _BidIt1 _Last, _BidIt2 _Dest)
{
    return _Copy_backward_opt(_First, _Last, _Dest, _Ptr_cat(_First, _Dest));
}
/* moves within one array: by swap for VC9 containers, else by assignment */
template <class _BidIt1, class _BidIt2>
inline _BidIt2 _Move_backward_cat(_BidIt1 _First, _BidIt1 _Last, _BidIt2 _Dest, _Swap_move_tag)
{
    while (_First != _Last) swap(*--_Dest, *--_Last);
    return _Dest;
}
template <class _BidIt1, class _BidIt2>
inline _BidIt2 _Move_backward_cat(_BidIt1 _First, _BidIt1 _Last, _BidIt2 _Dest, _Undefined_move_tag)
{
    return unchecked_copy_backward(_First, _Last, _Dest);
}
template <class _BidIt1, class _BidIt2> inline _BidIt2 _Unchecked_move_backward(_BidIt1 _First, _BidIt1 _Last, _BidIt2 _Dest)
{
    return _Move_backward_cat(_First, _Last, _Dest,
                              typename _Move_operation_category<typename iterator_traits<_BidIt1>::value_type>::_Move_cat());
}
template <class _OutIt, class _Diff, class _Ty> inline void unchecked_fill_n(_OutIt _First, _Diff _Count, const _Ty &_Val)
{
    for (; 0 < _Count; --_Count, ++_First) *_First = _Val;
}
/* <memory>: construct into raw storage */
template <class _InIt, class _FwdIt, class _Alloc>
inline _FwdIt _Uninit_copy(_InIt _First, _InIt _Last, _FwdIt _Dest, _Alloc &_Al, _Nonscalar_ptr_iterator_tag)
{
    _FwdIt _Next = _Dest;
    _TRY_BEGIN
    for (; _First != _Last; ++_Dest, ++_First) _Al.construct(&*_Dest, *_First);
    _CATCH_ALL
    for (; _Next != _Dest; ++_Next) _Al.destroy(&*_Next);
    _RERAISE;
    _CATCH_END
    return _Dest;
}
template <class _InIt, class _FwdIt, class _Alloc>
inline _FwdIt _Uninit_copy(_InIt _First, _InIt _Last, _FwdIt _Dest, _Alloc &, _Scalar_ptr_iterator_tag)
{
    ptrdiff_t _Off = _Last - _First;
    if (_Off > 0) ::memmove(&*_Dest, &*_First, _Off * sizeof(*_First));
    return _Dest + _Off;
}
template <class _InIt, class _FwdIt, class _Alloc>
inline _FwdIt unchecked_uninitialized_copy(_InIt _First, _InIt _Last, _FwdIt _Dest, _Alloc &_Al)
{
    return _Uninit_copy(_First, _Last, _Dest, _Al, _Ptr_cat(_First, _Dest));
}
template <class _InIt, class _FwdIt, class _Alloc>
inline _FwdIt _Uninit_move_cat(_InIt _First, _InIt _Last, _FwdIt _Dest, _Alloc &_Al, _Swap_move_tag)
{
    typedef typename iterator_traits<_InIt>::value_type _Valty;
    _FwdIt _Next = _Dest;
    _Valty _Val;
    _TRY_BEGIN
    for (; _First != _Last; ++_Dest, ++_First)
    {
        _Al.construct(&*_Dest, _Val);
        swap(*_Dest, *_First);
    }
    _CATCH_ALL
    for (; _Next != _Dest; ++_Next) _Al.destroy(&*_Next);
    _RERAISE;
    _CATCH_END
    return _Dest;
}
template <class _InIt, class _FwdIt, class _Alloc>
inline _FwdIt _Uninit_move_cat(_InIt _First, _InIt _Last, _FwdIt _Dest, _Alloc &_Al, _Undefined_move_tag)
{
    return unchecked_uninitialized_copy(_First, _Last, _Dest, _Al);
}
template <class _InIt, class _FwdIt, class _Alloc>
inline _FwdIt _Unchecked_uninitialized_move(_InIt _First, _InIt _Last, _FwdIt _Dest, _Alloc &_Al)
{
    return _Uninit_move_cat(_First, _Last, _Dest, _Al,
                            typename _Move_operation_category<typename iterator_traits<_InIt>::value_type>::_Move_cat());
}
template <class _FwdIt, class _Diff, class _Tval, class _Alloc>
inline void _Uninit_fill_n(_FwdIt _First, _Diff _Count, const _Tval &_Val, _Alloc &_Al, _Nonscalar_ptr_iterator_tag)
{
    _FwdIt _Next = _First;
    _TRY_BEGIN
    for (; 0 < _Count; --_Count, ++_First) _Al.construct(&*_First, _Val);
    _CATCH_ALL
    for (; _Next != _First; ++_Next) _Al.destroy(&*_Next);
    _RERAISE;
    _CATCH_END
}
template <class _FwdIt, class _Diff, class _Tval, class _Alloc>
inline void _Uninit_fill_n(_FwdIt _First, _Diff _Count, const _Tval &_Val, _Alloc &, _Scalar_ptr_iterator_tag)
{
    for (; 0 < _Count; --_Count, ++_First) *_First = _Val;
}
template <class _FwdIt, class _Diff, class _Tval, class _Alloc>
inline void unchecked_uninitialized_fill_n(_FwdIt _First, _Diff _Count, const _Tval &_Val, _Alloc &_Al)
{
    _Uninit_fill_n(_First, _Count, _Val, _Al, _Ptr_cat(_First, _First));
}
} /* namespace stdext */

/* ------------------------------------------------------------------------- <xutility>: the container bases */
class _Container_base {};                 /* _Container_base_secure: empty in the release library */
class _Iterator_base {                    /* _Iterator_base_secure: the container a (string) iterator belongs to */
public:
    _Iterator_base() : _Mycont(0) {}
    const _Container_base *_Mycont;
};
class _Container_base_aux;
class _Aux_cont {                         /* the container's proxy: iterators point at it, it points at the container */
public:
    explicit _Aux_cont(const _Container_base_aux *const _Pcont) : _Mycontainer(_Pcont) {}
    const _Container_base_aux *_Getcont() const { return _Mycontainer; }
private:
    const _Container_base_aux *_Mycontainer;
};
class _Container_base_aux {
public:
    _Container_base_aux() {}
    _Container_base_aux(const _Container_base_aux &) {}
    _Container_base_aux &operator=(const _Container_base_aux &) { return *this; }
    void _Swap_aux(_Container_base_aux &_Right)
    {
        ::std::swap(_Myownedaux, _Right._Myownedaux);
        ::std::swap(*_Myownedaux, *_Right._Myownedaux);
    }
    ~_Container_base_aux() {}
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
    typename _Alloc::template rebind<_Aux_cont>::other _Alaux;   /* allocates the proxy */
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

/* ----------------------------------------------------------------------------------------------- <vector> */
template <class _Ty, class _Alloc> class _Vector_val : public _Container_base_aux_alloc_real<_Alloc> {
    FNVNX_MSVC_BASE_END(_Container_base_aux_alloc_real<_Alloc>)
protected:
    _Vector_val(_Alloc _Al = _Alloc()) : _Container_base_aux_alloc_real<_Alloc>(_Al), _Alval(_Al) {}
    typedef typename _Alloc::template rebind<_Ty>::other _Alty;
    _Alty _Alval;
};
/* what the iterators read of a vector (its layout: _Vector_val, then the three pointers) */
template <class _Ty, class _Alloc> struct _Vector_layout : _Container_base_aux_alloc_real<_Alloc> {
    FNVNX_MSVC_BASE_END(_Container_base_aux_alloc_real<_Alloc>)
    typename _Alloc::template rebind<_Ty>::other _Alval;
    _Ty *_Myfirst, *_Mylast, *_Myend;
};

template <class _Ty, class _Alloc> class _Vector_const_iterator : public _Iterator_base_aux {
public:
    typedef _Vector_const_iterator<_Ty, _Alloc> _Myt;
    typedef _Vector_layout<_Ty, _Alloc> _Myvec;
    typedef _Ty *_Tptr;
    typedef random_access_iterator_tag iterator_category;
    typedef _Ty value_type;
    typedef _Dift difference_type;
    typedef const _Ty *pointer;
    typedef const _Ty &reference;
    _Vector_const_iterator() { _Myptr = 0; }
    _Vector_const_iterator(_Tptr _Ptr, const _Container_base_aux *_Pvector)
    {
        FNVNX_VC9_VALIDATE(_Pvector == 0 || (_Vec(_Pvector)->_Myfirst <= _Ptr && _Ptr <= _Vec(_Pvector)->_Mylast));
        this->_Set_container(_Pvector);
        _Myptr = _Ptr;
    }
    reference operator*() const
    {
        FNVNX_VC9_VALIDATE(this->_Has_container());
        FNVNX_VC9_VALIDATE(_Myptr < _Vec(this->_Getmycont())->_Mylast);
        return *_Myptr;
    }
    pointer operator->() const { return &**this; }
    _Myt &operator++()
    {
        FNVNX_VC9_VALIDATE(this->_Has_container());
        FNVNX_VC9_VALIDATE(_Myptr < _Vec(this->_Getmycont())->_Mylast);
        ++_Myptr;
        return *this;
    }
    _Myt operator++(int) { _Myt _Tmp = *this; ++*this; return _Tmp; }
    _Myt &operator--()
    {
        FNVNX_VC9_VALIDATE(this->_Has_container());
        FNVNX_VC9_VALIDATE(_Myptr > _Vec(this->_Getmycont())->_Myfirst);
        --_Myptr;
        return *this;
    }
    _Myt operator--(int) { _Myt _Tmp = *this; --*this; return _Tmp; }
    _Myt &operator+=(difference_type _Off)
    {
        FNVNX_VC9_VALIDATE(this->_Has_container());
        FNVNX_VC9_VALIDATE(_Myptr + _Off <= _Vec(this->_Getmycont())->_Mylast &&
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
    void _Compat(const _Myt &_Right) const { FNVNX_VC9_VALIDATE(this->_Has_container() && this->_Same_container(_Right)); }
    static const _Myvec *_Vec(const _Container_base_aux *_Pcont) { return static_cast<const _Myvec *>(_Pcont); }
    _Tptr _Myptr;
};
template <class _Ty, class _Alloc> class _Vector_iterator : public _Vector_const_iterator<_Ty, _Alloc> {
public:
    typedef _Vector_iterator<_Ty, _Alloc> _Myt;
    typedef _Vector_const_iterator<_Ty, _Alloc> _Mybase;
    typedef random_access_iterator_tag iterator_category;
    typedef _Ty value_type;
    typedef _Dift difference_type;
    typedef _Ty *pointer;
    typedef _Ty &reference;
    _Vector_iterator() {}
    _Vector_iterator(pointer _Ptr, const _Container_base_aux *_Pvector) : _Mybase(_Ptr, _Pvector) {}
    reference operator*() const { return (reference) * *(const _Mybase *)this; }
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

template <class _Ty, class _Ax = allocator<_Ty> > class vector : public _Vector_val<_Ty, _Ax> {
    FNVNX_MSVC_BASE_END(_Vector_val<_Ty, _Ax>)
public:
    typedef vector<_Ty, _Ax> _Myt;
    typedef _Vector_val<_Ty, _Ax> _Mybase;
    typedef typename _Mybase::_Alty _Alloc;
    typedef _Alloc allocator_type;
    typedef typename _Alloc::size_type size_type;
    typedef typename _Alloc::difference_type _Dift;
    typedef _Dift difference_type;
    typedef typename _Alloc::pointer _Tptr;
    typedef typename _Alloc::const_pointer _Ctptr;
    typedef _Tptr pointer;
    typedef _Ctptr const_pointer;
    typedef typename _Alloc::reference _Reft;
    typedef _Reft reference;
    typedef typename _Alloc::const_reference const_reference;
    typedef typename _Alloc::value_type value_type;
    typedef _Vector_iterator<_Ty, _Alloc> iterator;
    typedef _Vector_const_iterator<_Ty, _Alloc> const_iterator;
    typedef ::std::reverse_iterator<iterator> reverse_iterator;
    typedef ::std::reverse_iterator<const_iterator> const_reverse_iterator;

    vector() : _Mybase() { _Buy(0); }
    explicit vector(const _Alloc &_Al) : _Mybase(_Al) { _Buy(0); }
    explicit vector(size_type _Count) : _Mybase() { _Construct_n(_Count, _Ty()); }
    vector(size_type _Count, const _Ty &_Val) : _Mybase() { _Construct_n(_Count, _Val); }
    vector(size_type _Count, const _Ty &_Val, const _Alloc &_Al) : _Mybase(_Al) { _Construct_n(_Count, _Val); }
    vector(const _Myt &_Right) : _Mybase(_Right._Alval)
    {
        if (_Buy(_Right.size()))
        {
            _TRY_BEGIN
            _Mylast = _Ucopy(_Right.begin(), _Right.end(), _Myfirst);
            _CATCH_ALL
            _Tidy();
            _RERAISE;
            _CATCH_END
        }
    }
    template <class _Iter> vector(_Iter _First, _Iter _Last) : _Mybase() { _Construct(_First, _Last, is_integral<_Iter>()); }
    template <class _Iter> vector(_Iter _First, _Iter _Last, const _Alloc &_Al) : _Mybase(_Al)
    {
        _Construct(_First, _Last, is_integral<_Iter>());
    }
    ~vector() { _Tidy(); }

    _Myt &operator=(const _Myt &_Right)
    {
        if (this != &_Right)
        {
            if (_Right.size() == 0)
                clear();
            else if (_Right.size() <= size())
            {
                pointer _Ptr = stdext::unchecked_copy(_Right._Myfirst, _Right._Mylast, _Myfirst);
                _Destroy(_Ptr, _Mylast);
                _Mylast = _Myfirst + _Right.size();
            }
            else if (_Right.size() <= capacity())
            {
                pointer _Ptr = _Right._Myfirst + size();
                stdext::unchecked_copy(_Right._Myfirst, _Ptr, _Myfirst);
                _Mylast = _Ucopy(_Ptr, _Right._Mylast, _Mylast);
            }
            else
            {
                if (_Myfirst != 0)
                {
                    _Destroy(_Myfirst, _Mylast);
                    this->_Alval.deallocate(_Myfirst, _Myend - _Myfirst);
                }
                if (_Buy(_Right.size())) _Mylast = _Ucopy(_Right._Myfirst, _Right._Mylast, _Myfirst);
            }
        }
        return *this;
    }

    void reserve(size_type _Count)
    {
        if (max_size() < _Count)
            _Xlen();
        else if (capacity() < _Count)
        {
            pointer _Ptr = this->_Alval.allocate(_Count);
            _TRY_BEGIN
            _Umove(begin(), end(), _Ptr);
            _CATCH_ALL
            this->_Alval.deallocate(_Ptr, _Count);
            _RERAISE;
            _CATCH_END
            size_type _Size = size();
            if (_Myfirst != 0)
            {
                _Destroy(_Myfirst, _Mylast);
                this->_Alval.deallocate(_Myfirst, _Myend - _Myfirst);
            }
            _Myend = _Ptr + _Count;
            _Mylast = _Ptr + _Size;
            _Myfirst = _Ptr;
        }
    }
    size_type capacity() const { return _Myfirst == 0 ? 0 : _Myend - _Myfirst; }
    iterator begin() { return iterator(_Myfirst, this); }
    const_iterator begin() const { return const_iterator(_Myfirst, this); }
    iterator end() { return iterator(_Mylast, this); }
    const_iterator end() const { return const_iterator(_Mylast, this); }
    reverse_iterator rbegin() { return reverse_iterator(end()); }
    const_reverse_iterator rbegin() const { return const_reverse_iterator(end()); }
    reverse_iterator rend() { return reverse_iterator(begin()); }
    const_reverse_iterator rend() const { return const_reverse_iterator(begin()); }
    void resize(size_type _Newsize) { resize(_Newsize, _Ty()); }
    void resize(size_type _Newsize, _Ty _Val)
    {
        if (size() < _Newsize)
            _Insert_n(end(), _Newsize - size(), _Val);
        else if (_Newsize < size())
            erase(begin() + _Newsize, end());
    }
    size_type size() const { return _Myfirst == 0 ? 0 : _Mylast - _Myfirst; }
    size_type max_size() const { return this->_Alval.max_size(); }
    bool empty() const { return size() == 0; }
    _Alloc get_allocator() const { return this->_Alval; }
    const_reference at(size_type _Pos) const
    {
        if (size() <= _Pos) _Xran();
        return *(begin() + _Pos);
    }
    reference at(size_type _Pos)
    {
        if (size() <= _Pos) _Xran();
        return *(begin() + _Pos);
    }
    const_reference operator[](size_type _Pos) const
    {
        FNVNX_VC9_VALIDATE(_Pos < size());
        return *(_Myfirst + _Pos);
    }
    reference operator[](size_type _Pos)
    {
        FNVNX_VC9_VALIDATE(_Pos < size());
        return *(_Myfirst + _Pos);
    }
    reference front() { return *begin(); }
    const_reference front() const { return *begin(); }
    reference back() { return *(end() - 1); }
    const_reference back() const { return *(end() - 1); }
    void push_back(const _Ty &_Val)
    {
        if (size() < capacity())
            _Mylast = _Ufill(_Mylast, 1, _Val);
        else
            insert(end(), _Val);
    }
    void pop_back()
    {
        if (!empty())
        {
            _Destroy(_Mylast - 1, _Mylast);
            --_Mylast;
        }
    }
    template <class _Iter> void assign(_Iter _First, _Iter _Last) { _Assign(_First, _Last, is_integral<_Iter>()); }
    void assign(size_type _Count, const _Ty &_Val) { _Assign_n(_Count, _Val); }
    iterator insert(const_iterator _Where, const _Ty &_Val)
    {
        size_type _Off = size() == 0 ? 0 : _Where - begin();
        _Insert_n(_Where, (size_type)1, _Val);
        return begin() + _Off;
    }
    void insert(const_iterator _Where, size_type _Count, const _Ty &_Val) { _Insert_n(_Where, _Count, _Val); }
    template <class _Iter> void insert(const_iterator _Where, _Iter _First, _Iter _Last)
    {
        _Insert(_Where, _First, _Last, is_integral<_Iter>());
    }
    iterator erase(const_iterator _Where)
    {
        stdext::unchecked_copy(_VEC_ITER_BASE(_Where) + 1, _Mylast, _VEC_ITER_BASE(_Where));
        _Destroy(_Mylast - 1, _Mylast);
        --_Mylast;
        return _Make_iter(_Where);
    }
    iterator erase(const_iterator _First_arg, const_iterator _Last_arg)
    {
        iterator _First = _Make_iter(_First_arg);
        iterator _Last = _Make_iter(_Last_arg);
        if (_First != _Last)
        {
            pointer _Ptr = stdext::unchecked_copy(_VEC_ITER_BASE(_Last), _Mylast, _VEC_ITER_BASE(_First));
            _Destroy(_Ptr, _Mylast);
            _Mylast = _Ptr;
        }
        return _Make_iter(_First);
    }
    void clear() { erase(begin(), end()); }
    void swap(_Myt &_Right)
    {
        if (this == &_Right)
            ;
        else if (this->_Alval == _Right._Alval)
        {
            this->_Swap_aux(_Right);
            ::std::swap(_Myfirst, _Right._Myfirst);
            ::std::swap(_Mylast, _Right._Mylast);
            ::std::swap(_Myend, _Right._Myend);
        }
        else
        {
            _Myt _Ts = *this;
            *this = _Right;
            _Right = _Ts;
        }
    }

protected:
    template <class _Iter> void _Construct(_Iter _Count, _Iter _Val, true_type) { _Construct_n((size_type)_Count, (_Ty)_Val); }
    template <class _Iter> void _Construct(_Iter _First, _Iter _Last, false_type)
    {
        _Buy(0);
        _TRY_BEGIN
        insert(begin(), _First, _Last);
        _CATCH_ALL
        _Tidy();
        _RERAISE;
        _CATCH_END
    }
    void _Construct_n(size_type _Count, const _Ty &_Val)
    {
        if (_Buy(_Count))
        {
            _TRY_BEGIN
            _Mylast = _Ufill(_Myfirst, _Count, _Val);
            _CATCH_ALL
            _Tidy();
            _RERAISE;
            _CATCH_END
        }
    }
    template <class _Iter> void _Assign(_Iter _Count, _Iter _Val, true_type) { _Assign_n((size_type)_Count, (_Ty)_Val); }
    template <class _Iter> void _Assign(_Iter _First, _Iter _Last, false_type)
    {
        erase(begin(), end());
        insert(begin(), _First, _Last);
    }
    void _Assign_n(size_type _Count, const _Ty &_Val)
    {
        _Ty _Tmp = _Val;
        erase(begin(), end());
        insert(begin(), _Count, _Tmp);
    }
    template <class _Iter> void _Insert(const_iterator _Where, _Iter _First, _Iter _Last, true_type)
    {
        _Insert_n(_Where, (size_type)_First, (_Ty)_Last);
    }
    template <class _Iter> void _Insert(const_iterator _Where, _Iter _First, _Iter _Last, false_type)
    {
        _Insert_range(_Where, _First, _Last, typename iterator_traits<_Iter>::iterator_category());
    }
    template <class _Iter> void _Insert_range(const_iterator _Where, _Iter _First, _Iter _Last, input_iterator_tag)
    {
        size_type _Off = _VEC_ITER_BASE(_Where) - _Myfirst;
        for (; _First != _Last; ++_First, ++_Off) insert(begin() + _Off, *_First);
    }
    template <class _Iter> void _Insert_range(const_iterator _Where, _Iter _First, _Iter _Last, forward_iterator_tag)
    {
        size_type _Count = (size_type)::std::distance(_First, _Last);
        size_type _Capacity = capacity();
        if (_Count == 0)
            ;
        else if (max_size() - size() < _Count)
            _Xlen();
        else if (_Capacity < size() + _Count)
        {
            _Capacity = max_size() - _Capacity / 2 < _Capacity ? 0 : _Capacity + _Capacity / 2;
            if (_Capacity < size() + _Count) _Capacity = size() + _Count;
            pointer _Newvec = this->_Alval.allocate(_Capacity);
            pointer _Ptr = _Newvec;
            _TRY_BEGIN
            _Ptr = _Umove(_Myfirst, _VEC_ITER_BASE(_Where), _Newvec);
            _Ptr = _Ucopy(_First, _Last, _Ptr);
            _Umove(_VEC_ITER_BASE(_Where), _Mylast, _Ptr);
            _CATCH_ALL
            _Destroy(_Newvec, _Ptr);
            this->_Alval.deallocate(_Newvec, _Capacity);
            _RERAISE;
            _CATCH_END
            _Count += size();
            if (_Myfirst != 0)
            {
                _Destroy(_Myfirst, _Mylast);
                this->_Alval.deallocate(_Myfirst, _Myend - _Myfirst);
            }
            _Myend = _Newvec + _Capacity;
            _Mylast = _Newvec + _Count;
            _Myfirst = _Newvec;
        }
        else
        {   /* fits: append, then rotate into place */
            _Ucopy(_First, _Last, _Mylast);
            ::std::rotate(_VEC_ITER_BASE(_Where), _Mylast, _Mylast + _Count);
            _Mylast += _Count;
        }
    }
    bool _Buy(size_type _Capacity)
    {
        _Myfirst = 0, _Mylast = 0, _Myend = 0;
        if (_Capacity == 0)
            return false;
        else if (max_size() < _Capacity)
            _Xlen();
        else
        {
            _Myfirst = this->_Alval.allocate(_Capacity);
            _Mylast = _Myfirst;
            _Myend = _Myfirst + _Capacity;
        }
        return true;
    }
    void _Destroy(pointer _First, pointer _Last) { ::fnvnx_vc9::_Destroy_range(_First, _Last, this->_Alval); }
    void _Tidy()
    {
        if (_Myfirst != 0)
        {
            _Destroy(_Myfirst, _Mylast);
            this->_Alval.deallocate(_Myfirst, _Myend - _Myfirst);
        }
        _Myfirst = 0, _Mylast = 0, _Myend = 0;
    }
    template <class _Iter> pointer _Ucopy(_Iter _First, _Iter _Last, pointer _Ptr)
    {
        return stdext::unchecked_uninitialized_copy(_Unchecked(_First), _Unchecked(_Last), _Ptr, this->_Alval);
    }
    template <class _Iter> pointer _Umove(_Iter _First, _Iter _Last, pointer _Ptr)
    {
        return stdext::_Unchecked_uninitialized_move(_Unchecked(_First), _Unchecked(_Last), _Ptr, this->_Alval);
    }
    void _Insert_n(const_iterator _Where, size_type _Count, const _Ty &_Val)   /* as 0x00abfa90 */
    {
        size_type _Capacity = capacity();
        if (_Count == 0)
            ;
        else if (max_size() - size() < _Count)
            _Xlen();
        else if (_Capacity < size() + _Count)
        {
            _Capacity = max_size() - _Capacity / 2 < _Capacity ? 0 : _Capacity + _Capacity / 2;
            if (_Capacity < size() + _Count) _Capacity = size() + _Count;
            pointer _Newvec = this->_Alval.allocate(_Capacity);
            pointer _Ptr = _Newvec;
            _TRY_BEGIN
            _Ptr = _Umove(_Myfirst, _VEC_ITER_BASE(_Where), _Newvec);
            _Ptr = _Ufill(_Ptr, _Count, _Val);
            _Umove(_VEC_ITER_BASE(_Where), _Mylast, _Ptr);
            _CATCH_ALL
            _Destroy(_Newvec, _Ptr);
            this->_Alval.deallocate(_Newvec, _Capacity);
            _RERAISE;
            _CATCH_END
            _Count += size();
            if (_Myfirst != 0)
            {
                _Destroy(_Myfirst, _Mylast);
                this->_Alval.deallocate(_Myfirst, _Myend - _Myfirst);
            }
            _Myend = _Newvec + _Capacity;
            _Mylast = _Newvec + _Count;
            _Myfirst = _Newvec;
        }
        else if ((size_type)(_Mylast - _VEC_ITER_BASE(_Where)) < _Count)
        {
            _Ty _Tmp = _Val;
            _Umove(_VEC_ITER_BASE(_Where), _Mylast, _VEC_ITER_BASE(_Where) + _Count);
            _TRY_BEGIN
            _Ufill(_Mylast, _Count - (_Mylast - _VEC_ITER_BASE(_Where)), _Tmp);
            _CATCH_ALL
            _Destroy(_VEC_ITER_BASE(_Where) + _Count, _Mylast + _Count);
            _RERAISE;
            _CATCH_END
            _Mylast += _Count;
            ::std::fill(_VEC_ITER_BASE(_Where), _Mylast - _Count, _Tmp);
        }
        else
        {
            _Ty _Tmp = _Val;
            pointer _Oldend = _Mylast;
            _Mylast = _Umove(_Oldend - _Count, _Oldend, _Mylast);
            stdext::_Unchecked_move_backward(_VEC_ITER_BASE(_Where), _Oldend - _Count, _Oldend);
            ::std::fill(_VEC_ITER_BASE(_Where), _VEC_ITER_BASE(_Where) + _Count, _Tmp);
        }
    }
    pointer _Ufill(pointer _Ptr, size_type _Count, const _Ty &_Val)
    {
        stdext::unchecked_uninitialized_fill_n(_Ptr, _Count, _Val, this->_Alval);
        return _Ptr + _Count;
    }
    static void _Xlen() { throw ::std::length_error("vector<T> too long"); }
    static void _Xran() { throw ::std::out_of_range("invalid vector<T> subscript"); }
    iterator _Make_iter(const_iterator _Where) const { return iterator(_Where._Myptr, this); }
    /* the element pointer of one of this vector's iterators; other iterators as they are */
    static pointer _Unchecked(const const_iterator &_It) { return _It._Myptr; }
    static pointer _Unchecked(const iterator &_It) { return _It._Myptr; }
    template <class _Iter> static _Iter _Unchecked(_Iter _It) { return _It; }

public:
    pointer _Myfirst;   /* VC9: protected; public here for the decomp's hand-written members */
    pointer _Mylast;
    pointer _Myend;
};
template <class _Ty, class _Alloc> struct _Move_operation_category<vector<_Ty, _Alloc> > { typedef _Swap_move_tag _Move_cat; };
template <class _Ty, class _Alloc> inline void swap(vector<_Ty, _Alloc> &_Left, vector<_Ty, _Alloc> &_Right) { _Left.swap(_Right); }
template <class _Ty, class _Alloc> inline bool operator==(const vector<_Ty, _Alloc> &_Left, const vector<_Ty, _Alloc> &_Right)
{
    return _Left.size() == _Right.size() && ::std::equal(_Left._Myfirst, _Left._Mylast, _Right._Myfirst);
}
template <class _Ty, class _Alloc> inline bool operator!=(const vector<_Ty, _Alloc> &_Left, const vector<_Ty, _Alloc> &_Right)
{
    return !(_Left == _Right);
}
template <class _Ty, class _Alloc> inline bool operator<(const vector<_Ty, _Alloc> &_Left, const vector<_Ty, _Alloc> &_Right)
{
    return ::std::lexicographical_compare(_Left._Myfirst, _Left._Mylast, _Right._Myfirst, _Right._Mylast);
}

/* ----------------------------------------------------------------------------------------------- <xstring> */
template <class _Elem, class _Traits, class _Alloc> class basic_string;

template <class _Elem, class _Traits, class _Alloc> class _String_const_iterator : public _Iterator_base {
public:
    typedef _String_const_iterator<_Elem, _Traits, _Alloc> _Myt;
    typedef basic_string<_Elem, _Traits, _Alloc> _Mystr;
    typedef random_access_iterator_tag iterator_category;
    typedef _Elem value_type;
    typedef _Dift difference_type;
    typedef const _Elem *pointer;
    typedef const _Elem &reference;
    _String_const_iterator() { _Myptr = 0; }
    _String_const_iterator(pointer _Ptr, const _Container_base *_Pstring)
    {
        FNVNX_VC9_VALIDATE(_Pstring != 0 && _Ptr != 0 && _Str(_Pstring)->_Myptr() <= _Ptr &&
                           _Ptr <= _Str(_Pstring)->_Myptr() + _Str(_Pstring)->_Mysize);
        this->_Mycont = _Pstring;
        _Myptr = _Ptr;
    }
    reference operator*() const
    {
        FNVNX_VC9_VALIDATE(this->_Mycont != 0);
        FNVNX_VC9_VALIDATE(_Myptr < _Str(this->_Mycont)->_Myptr() + _Str(this->_Mycont)->_Mysize);
        return *_Myptr;
    }
    pointer operator->() const { return &**this; }
    _Myt &operator++()
    {
        FNVNX_VC9_VALIDATE(this->_Mycont != 0);
        FNVNX_VC9_VALIDATE(_Myptr < _Str(this->_Mycont)->_Myptr() + _Str(this->_Mycont)->_Mysize);
        ++_Myptr;
        return *this;
    }
    _Myt operator++(int) { _Myt _Tmp = *this; ++*this; return _Tmp; }
    _Myt &operator--()
    {
        FNVNX_VC9_VALIDATE(this->_Mycont != 0);
        FNVNX_VC9_VALIDATE(_Myptr > _Str(this->_Mycont)->_Myptr());
        --_Myptr;
        return *this;
    }
    _Myt operator--(int) { _Myt _Tmp = *this; --*this; return _Tmp; }
    _Myt &operator+=(difference_type _Off)
    {
        FNVNX_VC9_VALIDATE(this->_Mycont != 0);
        FNVNX_VC9_VALIDATE(_Myptr + _Off <= _Str(this->_Mycont)->_Myptr() + _Str(this->_Mycont)->_Mysize &&
                           _Myptr + _Off >= _Str(this->_Mycont)->_Myptr());
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
    void _Compat(const _Myt &_Right) const { FNVNX_VC9_VALIDATE(this->_Mycont != 0 && this->_Mycont == _Right._Mycont); }
    static const _Mystr *_Str(const _Container_base *_Pcont) { return static_cast<const _Mystr *>(_Pcont); }
    pointer _Myptr;
};
template <class _Elem, class _Traits, class _Alloc>
class _String_iterator : public _String_const_iterator<_Elem, _Traits, _Alloc> {
public:
    typedef _String_iterator<_Elem, _Traits, _Alloc> _Myt;
    typedef _String_const_iterator<_Elem, _Traits, _Alloc> _Mybase;
    typedef random_access_iterator_tag iterator_category;
    typedef _Elem value_type;
    typedef _Dift difference_type;
    typedef _Elem *pointer;
    typedef _Elem &reference;
    _String_iterator() {}
    _String_iterator(pointer _Ptr, const _Container_base *_Pstring) : _Mybase(_Ptr, _Pstring) {}
    reference operator*() const { return (reference) * *(const _Mybase *)this; }
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

template <class _Ty, class _Alloc> class _String_val : public _Container_base {
protected:
    typedef typename _Alloc::template rebind<_Ty>::other _Alty;
    _String_val(_Alty _Al = _Alty()) : _Alval(_Al) {}
public:
    _String_val(const _String_val &_Right) : _Alval(_Right._Alval) {}
    _Alty _Alval;
};

template <class _Elem, class _Traits = char_traits<_Elem>, class _Ax = allocator<_Elem> >
class basic_string : public _String_val<_Elem, _Ax> {
    FNVNX_MSVC_BASE_END(_String_val<_Elem, _Ax>)
public:
    typedef basic_string<_Elem, _Traits, _Ax> _Myt;
    typedef _String_val<_Elem, _Ax> _Mybase;
    typedef typename _Mybase::_Alty _Alloc;
    typedef _Traits traits_type;
    typedef _Alloc allocator_type;
    typedef typename _Alloc::size_type size_type;
    typedef typename _Alloc::difference_type _Dift;
    typedef _Dift difference_type;
    typedef typename _Alloc::pointer _Tptr;
    typedef typename _Alloc::const_pointer _Ctptr;
    typedef _Tptr pointer;
    typedef _Ctptr const_pointer;
    typedef typename _Alloc::reference _Reft;
    typedef _Reft reference;
    typedef typename _Alloc::const_reference const_reference;
    typedef typename _Alloc::value_type value_type;
    typedef _String_iterator<_Elem, _Traits, _Alloc> iterator;
    typedef _String_const_iterator<_Elem, _Traits, _Alloc> const_iterator;
    typedef ::std::reverse_iterator<iterator> reverse_iterator;
    typedef ::std::reverse_iterator<const_iterator> const_reverse_iterator;

    static const size_type npos = (size_type)(-1);
    enum { _BUF_SIZE = 16 / sizeof(_Elem) < 1 ? 1 : 16 / sizeof(_Elem) };
    enum { _ALLOC_MASK = sizeof(_Elem) <= 1 ? 15 : sizeof(_Elem) <= 2 ? 7 : sizeof(_Elem) <= 4 ? 3 : sizeof(_Elem) <= 8 ? 1 : 0 };

    basic_string() : _Mybase() { _Tidy(); }
    explicit basic_string(const _Alloc &_Al) : _Mybase(_Al) { _Tidy(); }
    basic_string(const _Myt &_Right) : _Mybase(_Right._Alval)
    {
        _Tidy();
        assign(_Right, 0, npos);
    }
    basic_string(const _Myt &_Right, size_type _Roff, size_type _Count = npos) : _Mybase()
    {
        _Tidy();
        assign(_Right, _Roff, _Count);
    }
    basic_string(const _Myt &_Right, size_type _Roff, size_type _Count, const _Alloc &_Al) : _Mybase(_Al)
    {
        _Tidy();
        assign(_Right, _Roff, _Count);
    }
    basic_string(const _Elem *_Ptr, size_type _Count) : _Mybase()
    {
        _Tidy();
        assign(_Ptr, _Count);
    }
    basic_string(const _Elem *_Ptr, size_type _Count, const _Alloc &_Al) : _Mybase(_Al)
    {
        _Tidy();
        assign(_Ptr, _Count);
    }
    basic_string(const _Elem *_Ptr) : _Mybase()
    {
        _Tidy();
        assign(_Ptr);
    }
    basic_string(const _Elem *_Ptr, const _Alloc &_Al) : _Mybase(_Al)
    {
        _Tidy();
        assign(_Ptr);
    }
    basic_string(size_type _Count, _Elem _Ch) : _Mybase()
    {
        _Tidy();
        assign(_Count, _Ch);
    }
    basic_string(size_type _Count, _Elem _Ch, const _Alloc &_Al) : _Mybase(_Al)
    {
        _Tidy();
        assign(_Count, _Ch);
    }
    template <class _It> basic_string(_It _First, _It _Last) : _Mybase()
    {
        _Tidy();
        _Construct(_First, _Last, is_integral<_It>());
    }
    template <class _It> basic_string(_It _First, _It _Last, const _Alloc &_Al) : _Mybase(_Al)
    {
        _Tidy();
        _Construct(_First, _Last, is_integral<_It>());
    }
    basic_string(const_iterator _First, const_iterator _Last) : _Mybase()
    {
        _Tidy();
        if (_First != _Last) assign(&*_First, _Last - _First);
    }
    ~basic_string() { _Tidy(true); }

    /* a libstdc++ string where the rest of the port wants one (std::runtime_error(s), ...) */
    operator ::std::basic_string<_Elem>() const { return ::std::basic_string<_Elem>(_Myptr(), _Mysize); }

    _Myt &operator=(const _Myt &_Right) { return assign(_Right); }
    _Myt &operator=(const _Elem *_Ptr) { return assign(_Ptr); }
    _Myt &operator=(_Elem _Ch) { return assign(1, _Ch); }
    _Myt &operator+=(const _Myt &_Right) { return append(_Right); }
    _Myt &operator+=(const _Elem *_Ptr) { return append(_Ptr); }
    _Myt &operator+=(_Elem _Ch) { return append((size_type)1, _Ch); }

    _Myt &append(const _Myt &_Right) { return append(_Right, 0, npos); }
    _Myt &append(const _Myt &_Right, size_type _Roff, size_type _Count)
    {
        if (_Right.size() < _Roff) _Xran();
        size_type _Num = _Right.size() - _Roff;
        if (_Num < _Count) _Count = _Num;
        if (npos - _Mysize <= _Count || _Mysize + _Count < _Mysize) _Xlen();
        if (0 < _Count && _Grow(_Num = _Mysize + _Count))
        {
            _Traits::copy(_Myptr() + _Mysize, _Right._Myptr() + _Roff, _Count);
            _Eos(_Num);
        }
        return *this;
    }
    _Myt &append(const _Elem *_Ptr, size_type _Count)
    {
        if (_Inside(_Ptr)) return append(*this, _Ptr - _Myptr(), _Count);
        if (npos - _Mysize <= _Count || _Mysize + _Count < _Mysize) _Xlen();
        size_type _Num;
        if (0 < _Count && _Grow(_Num = _Mysize + _Count))
        {
            _Traits::copy(_Myptr() + _Mysize, _Ptr, _Count);
            _Eos(_Num);
        }
        return *this;
    }
    _Myt &append(const _Elem *_Ptr) { return append(_Ptr, (size_type)_Traits::length(_Ptr)); }
    _Myt &append(size_type _Count, _Elem _Ch)
    {
        if (npos - _Mysize <= _Count) _Xlen();
        size_type _Num;
        if (0 < _Count && _Grow(_Num = _Mysize + _Count))
        {
            _Chassign(_Mysize, _Count, _Ch);
            _Eos(_Num);
        }
        return *this;
    }
    template <class _It> _Myt &append(_It _First, _It _Last) { return _Append(_First, _Last, is_integral<_It>()); }

    _Myt &assign(const _Myt &_Right) { return assign(_Right, 0, npos); }
    _Myt &assign(const _Myt &_Right, size_type _Roff, size_type _Count)
    {
        if (_Right.size() < _Roff) _Xran();
        size_type _Num = _Right.size() - _Roff;
        if (_Count < _Num) _Num = _Count;
        if (this == &_Right)
            erase((size_type)(_Roff + _Num)), erase(0, _Roff);
        else if (_Grow(_Num))
        {
            _Traits::copy(_Myptr(), _Right._Myptr() + _Roff, _Num);
            _Eos(_Num);
        }
        return *this;
    }
    _Myt &assign(const _Elem *_Ptr, size_type _Count)
    {
        if (_Inside(_Ptr)) return assign(*this, _Ptr - _Myptr(), _Count);
        if (_Grow(_Count))
        {
            _Traits::copy(_Myptr(), _Ptr, _Count);
            _Eos(_Count);
        }
        return *this;
    }
    _Myt &assign(const _Elem *_Ptr) { return assign(_Ptr, (size_type)_Traits::length(_Ptr)); }
    _Myt &assign(size_type _Count, _Elem _Ch)
    {
        if (_Count == npos) _Xlen();
        if (_Grow(_Count))
        {
            _Chassign(0, _Count, _Ch);
            _Eos(_Count);
        }
        return *this;
    }
    template <class _It> _Myt &assign(_It _First, _It _Last) { return _Assign(_First, _Last, is_integral<_It>()); }

    _Myt &insert(size_type _Off, const _Myt &_Right) { return insert(_Off, _Right, 0, npos); }
    _Myt &insert(size_type _Off, const _Myt &_Right, size_type _Roff, size_type _Count)
    {
        if (_Mysize < _Off || _Right.size() < _Roff) _Xran();
        size_type _Num = _Right.size() - _Roff;
        if (_Num < _Count) _Count = _Num;
        if (npos - _Mysize <= _Count) _Xlen();
        if (0 < _Count && _Grow(_Num = _Mysize + _Count))
        {
            _Traits::move(_Myptr() + _Off + _Count, _Myptr() + _Off, _Mysize - _Off);
            if (this == &_Right)
                _Traits::move(_Myptr() + _Off, _Myptr() + (_Off < _Roff ? _Roff + _Count : _Roff), _Count);
            else
                _Traits::copy(_Myptr() + _Off, _Right._Myptr() + _Roff, _Count);
            _Eos(_Num);
        }
        return *this;
    }
    _Myt &insert(size_type _Off, const _Elem *_Ptr, size_type _Count)
    {
        if (_Inside(_Ptr)) return insert(_Off, *this, _Ptr - _Myptr(), _Count);
        if (_Mysize < _Off) _Xran();
        if (npos - _Mysize <= _Count) _Xlen();
        size_type _Num;
        if (0 < _Count && _Grow(_Num = _Mysize + _Count))
        {
            _Traits::move(_Myptr() + _Off + _Count, _Myptr() + _Off, _Mysize - _Off);
            _Traits::copy(_Myptr() + _Off, _Ptr, _Count);
            _Eos(_Num);
        }
        return *this;
    }
    _Myt &insert(size_type _Off, const _Elem *_Ptr) { return insert(_Off, _Ptr, (size_type)_Traits::length(_Ptr)); }
    _Myt &insert(size_type _Off, size_type _Count, _Elem _Ch)
    {
        if (_Mysize < _Off) _Xran();
        if (npos - _Mysize <= _Count) _Xlen();
        size_type _Num;
        if (0 < _Count && _Grow(_Num = _Mysize + _Count))
        {
            _Traits::move(_Myptr() + _Off + _Count, _Myptr() + _Off, _Mysize - _Off);
            _Chassign(_Off, _Count, _Ch);
            _Eos(_Num);
        }
        return *this;
    }
    iterator insert(const_iterator _Where) { return insert(_Where, _Elem()); }
    iterator insert(const_iterator _Where, _Elem _Ch)
    {
        size_type _Off = _Pdif(_Where, begin());
        insert(_Off, 1, _Ch);
        return begin() + _Off;
    }
    void insert(const_iterator _Where, size_type _Count, _Elem _Ch)
    {
        size_type _Off = _Pdif(_Where, begin());
        insert(_Off, _Count, _Ch);
    }

    _Myt &erase(size_type _Off = 0, size_type _Count = npos)
    {
        if (_Mysize < _Off) _Xran();
        if (_Mysize - _Off < _Count) _Count = _Mysize - _Off;
        if (0 < _Count)
        {
            _Traits::move(_Myptr() + _Off, _Myptr() + _Off + _Count, _Mysize - _Off - _Count);
            size_type _Newsize = _Mysize - _Count;
            _Eos(_Newsize);
        }
        return *this;
    }
    iterator erase(const_iterator _Where)
    {
        size_type _Count = _Pdif(_Where, begin());
        erase(_Count, 1);
        return iterator(_Myptr() + _Count, this);
    }
    iterator erase(const_iterator _First, const_iterator _Last)
    {
        size_type _Count = _Pdif(_First, begin());
        erase(_Count, _Pdif(_Last, _First));
        return iterator(_Myptr() + _Count, this);
    }
    void clear() { erase(begin(), end()); }

    _Myt &replace(size_type _Off, size_type _N0, const _Elem *_Ptr, size_type _Count)
    {
        if (_Inside(_Ptr))
        {   /* the source is inside this string: replace from a copy (same result) */
            _Myt _Tmp(_Ptr, _Count);
            return replace(_Off, _N0, _Tmp._Myptr(), _Count);
        }
        if (_Mysize < _Off) _Xran();
        if (_Mysize - _Off < _N0) _N0 = _Mysize - _Off;
        if (npos - _Count <= _Mysize - _N0) _Xlen();
        size_type _Nm = _Mysize - _N0 - _Off;
        if (_Count < _N0) _Traits::move(_Myptr() + _Off + _Count, _Myptr() + _Off + _N0, _Nm);
        size_type _Num;
        if ((0 < _Count || 0 < _N0) && _Grow(_Num = _Mysize + _Count - _N0))
        {
            if (_N0 < _Count) _Traits::move(_Myptr() + _Off + _Count, _Myptr() + _Off + _N0, _Nm);
            _Traits::copy(_Myptr() + _Off, _Ptr, _Count);
            _Eos(_Num);
        }
        return *this;
    }
    _Myt &replace(size_type _Off, size_type _N0, const _Elem *_Ptr) { return replace(_Off, _N0, _Ptr, (size_type)_Traits::length(_Ptr)); }
    _Myt &replace(size_type _Off, size_type _N0, const _Myt &_Right)
    {
        if (this == &_Right)
        {
            _Myt _Tmp(_Right);
            return replace(_Off, _N0, _Tmp._Myptr(), _Tmp.size());
        }
        return replace(_Off, _N0, _Right._Myptr(), _Right.size());
    }

    iterator begin() { return iterator(_Myptr(), this); }
    const_iterator begin() const { return const_iterator(_Myptr(), this); }
    iterator end() { return iterator(_Myptr() + _Mysize, this); }
    const_iterator end() const { return const_iterator(_Myptr() + _Mysize, this); }
    reverse_iterator rbegin() { return reverse_iterator(end()); }
    const_reverse_iterator rbegin() const { return const_reverse_iterator(end()); }
    reverse_iterator rend() { return reverse_iterator(begin()); }
    const_reverse_iterator rend() const { return const_reverse_iterator(begin()); }

    reference at(size_type _Off)
    {
        if (_Mysize <= _Off) _Xran();
        return _Myptr()[_Off];
    }
    const_reference at(size_type _Off) const
    {
        if (_Mysize <= _Off) _Xran();
        return _Myptr()[_Off];
    }
    reference operator[](size_type _Off)
    {
        FNVNX_VC9_VALIDATE(_Off < _Mysize);
        return _Myptr()[_Off];
    }
    const_reference operator[](size_type _Off) const
    {
        FNVNX_VC9_VALIDATE(_Off <= _Mysize);
        return _Myptr()[_Off];
    }
    void push_back(_Elem _Ch) { insert(end(), _Ch); }
    const _Elem *c_str() const { return _Myptr(); }
    const _Elem *data() const { return c_str(); }
    size_type length() const { return _Mysize; }
    size_type size() const { return _Mysize; }
    size_type max_size() const
    {
        size_type _Num = this->_Alval.max_size();
        return _Num <= 1 ? 1 : _Num - 1;
    }
    void resize(size_type _Newsize) { resize(_Newsize, _Elem()); }
    void resize(size_type _Newsize, _Elem _Ch)
    {
        if (_Newsize <= _Mysize)
            erase(_Newsize);
        else
            append(_Newsize - _Mysize, _Ch);
    }
    size_type capacity() const { return _Myres; }
    void reserve(size_type _Newcap = 0)
    {
        if (_Mysize <= _Newcap && _Myres != _Newcap)
        {
            size_type _Size = _Mysize;
            if (_Grow(_Newcap, true)) _Eos(_Size);
        }
    }
    bool empty() const { return _Mysize == 0; }
    size_type copy(_Elem *_Ptr, size_type _Count, size_type _Off = 0) const
    {
        if (_Mysize < _Off) _Xran();
        if (_Mysize - _Off < _Count) _Count = _Mysize - _Off;
        _Traits::copy(_Ptr, _Myptr() + _Off, _Count);
        return _Count;
    }
    void swap(_Myt &_Right)
    {
        if (this == &_Right)
            ;
        else if (this->_Alval == _Right._Alval)
        {
            _Bxty _Tbx = _Bx; _Bx = _Right._Bx, _Right._Bx = _Tbx;
            size_type _Tlen = _Mysize; _Mysize = _Right._Mysize, _Right._Mysize = _Tlen;
            size_type _Tres = _Myres; _Myres = _Right._Myres, _Right._Myres = _Tres;
        }
        else
        {
            _Myt _Tmp = *this;
            *this = _Right, _Right = _Tmp;
        }
    }

    size_type find(const _Myt &_Right, size_type _Off = 0) const { return find(_Right._Myptr(), _Off, _Right.size()); }
    size_type find(const _Elem *_Ptr, size_type _Off, size_type _Count) const
    {
        if (_Count == 0 && _Off <= _Mysize) return _Off;
        size_type _Nm;
        if (_Off < _Mysize && _Count <= (_Nm = _Mysize - _Off))
        {
            const _Elem *_Uptr, *_Vptr;
            for (_Nm -= _Count - 1, _Vptr = _Myptr() + _Off; (_Uptr = _Traits::find(_Vptr, _Nm, *_Ptr)) != 0;
                 _Nm -= _Uptr - _Vptr + 1, _Vptr = _Uptr + 1)
                if (_Traits::compare(_Uptr, _Ptr, _Count) == 0) return _Uptr - _Myptr();
        }
        return npos;
    }
    size_type find(const _Elem *_Ptr, size_type _Off = 0) const { return find(_Ptr, _Off, (size_type)_Traits::length(_Ptr)); }
    size_type find(_Elem _Ch, size_type _Off = 0) const { return find(&_Ch, _Off, 1); }
    size_type rfind(const _Myt &_Right, size_type _Off = npos) const { return rfind(_Right._Myptr(), _Off, _Right.size()); }
    size_type rfind(const _Elem *_Ptr, size_type _Off, size_type _Count) const
    {
        if (_Count == 0) return _Off < _Mysize ? _Off : _Mysize;
        if (_Count <= _Mysize)
        {
            const _Elem *_Uptr = _Myptr() + (_Off < _Mysize - _Count ? _Off : _Mysize - _Count);
            for (;; --_Uptr)
                if (_Traits::eq(*_Uptr, *_Ptr) && _Traits::compare(_Uptr, _Ptr, _Count) == 0)
                    return _Uptr - _Myptr();
                else if (_Uptr == _Myptr())
                    break;
        }
        return npos;
    }
    size_type rfind(const _Elem *_Ptr, size_type _Off = npos) const { return rfind(_Ptr, _Off, (size_type)_Traits::length(_Ptr)); }
    size_type rfind(_Elem _Ch, size_type _Off = npos) const { return rfind(&_Ch, _Off, 1); }
    size_type find_first_of(const _Myt &_Right, size_type _Off = 0) const { return find_first_of(_Right._Myptr(), _Off, _Right.size()); }
    size_type find_first_of(const _Elem *_Ptr, size_type _Off, size_type _Count) const
    {
        if (0 < _Count && _Off < _Mysize)
        {
            const _Elem *const _Vptr = _Myptr() + _Mysize;
            for (const _Elem *_Uptr = _Myptr() + _Off; _Uptr < _Vptr; ++_Uptr)
                if (_Traits::find(_Ptr, _Count, *_Uptr) != 0) return _Uptr - _Myptr();
        }
        return npos;
    }
    size_type find_first_of(const _Elem *_Ptr, size_type _Off = 0) const { return find_first_of(_Ptr, _Off, (size_type)_Traits::length(_Ptr)); }
    size_type find_first_of(_Elem _Ch, size_type _Off = 0) const { return find(&_Ch, _Off, 1); }
    size_type find_last_of(const _Myt &_Right, size_type _Off = npos) const { return find_last_of(_Right._Myptr(), _Off, _Right.size()); }
    size_type find_last_of(const _Elem *_Ptr, size_type _Off, size_type _Count) const
    {
        if (0 < _Count && 0 < _Mysize)
            for (const _Elem *_Uptr = _Myptr() + (_Off < _Mysize ? _Off : _Mysize - 1);; --_Uptr)
                if (_Traits::find(_Ptr, _Count, *_Uptr) != 0)
                    return _Uptr - _Myptr();
                else if (_Uptr == _Myptr())
                    break;
        return npos;
    }
    size_type find_last_of(const _Elem *_Ptr, size_type _Off = npos) const { return find_last_of(_Ptr, _Off, (size_type)_Traits::length(_Ptr)); }
    size_type find_last_of(_Elem _Ch, size_type _Off = npos) const { return rfind(&_Ch, _Off, 1); }
    size_type find_first_not_of(const _Myt &_Right, size_type _Off = 0) const { return find_first_not_of(_Right._Myptr(), _Off, _Right.size()); }
    size_type find_first_not_of(const _Elem *_Ptr, size_type _Off, size_type _Count) const
    {
        if (_Off < _Mysize)
        {
            const _Elem *const _Vptr = _Myptr() + _Mysize;
            for (const _Elem *_Uptr = _Myptr() + _Off; _Uptr < _Vptr; ++_Uptr)
                if (_Traits::find(_Ptr, _Count, *_Uptr) == 0) return _Uptr - _Myptr();
        }
        return npos;
    }
    size_type find_first_not_of(const _Elem *_Ptr, size_type _Off = 0) const { return find_first_not_of(_Ptr, _Off, (size_type)_Traits::length(_Ptr)); }
    size_type find_first_not_of(_Elem _Ch, size_type _Off = 0) const { return find_first_not_of(&_Ch, _Off, 1); }
    size_type find_last_not_of(const _Myt &_Right, size_type _Off = npos) const { return find_last_not_of(_Right._Myptr(), _Off, _Right.size()); }
    size_type find_last_not_of(const _Elem *_Ptr, size_type _Off, size_type _Count) const
    {
        if (0 < _Mysize)
            for (const _Elem *_Uptr = _Myptr() + (_Off < _Mysize ? _Off : _Mysize - 1);; --_Uptr)
                if (_Traits::find(_Ptr, _Count, *_Uptr) == 0)
                    return _Uptr - _Myptr();
                else if (_Uptr == _Myptr())
                    break;
        return npos;
    }
    size_type find_last_not_of(const _Elem *_Ptr, size_type _Off = npos) const { return find_last_not_of(_Ptr, _Off, (size_type)_Traits::length(_Ptr)); }
    size_type find_last_not_of(_Elem _Ch, size_type _Off = npos) const { return find_last_not_of(&_Ch, _Off, 1); }
    _Myt substr(size_type _Off = 0, size_type _Count = npos) const { return _Myt(*this, _Off, _Count); }

    int compare(const _Myt &_Right) const { return compare(0, _Mysize, _Right._Myptr(), _Right.size()); }
    int compare(size_type _Off, size_type _N0, const _Myt &_Right) const { return compare(_Off, _N0, _Right, 0, npos); }
    int compare(size_type _Off, size_type _N0, const _Myt &_Right, size_type _Roff, size_type _Count) const
    {
        if (_Right.size() < _Roff) _Xran();
        if (_Right._Mysize - _Roff < _Count) _Count = _Right._Mysize - _Roff;
        return compare(_Off, _N0, _Right._Myptr() + _Roff, _Count);
    }
    int compare(const _Elem *_Ptr) const { return compare(0, _Mysize, _Ptr, (size_type)_Traits::length(_Ptr)); }
    int compare(size_type _Off, size_type _N0, const _Elem *_Ptr) const { return compare(_Off, _N0, _Ptr, (size_type)_Traits::length(_Ptr)); }
    int compare(size_type _Off, size_type _N0, const _Elem *_Ptr, size_type _Count) const
    {
        if (_Mysize < _Off) _Xran();
        if (_Mysize - _Off < _N0) _N0 = _Mysize - _Off;
        size_type _Ans = (size_type)_Traits::compare(_Myptr() + _Off, _Ptr, _N0 < _Count ? _N0 : _Count);
        return _Ans != 0 ? (int)_Ans : _N0 < _Count ? -1 : _N0 == _Count ? 0 : +1;
    }
    allocator_type get_allocator() const { return this->_Alval; }

    /* <xstring> internals */
    template <class _It> void _Construct(_It _Count, _It _Ch, true_type) { assign((size_type)_Count, (_Elem)_Ch); }
    template <class _It> void _Construct(_It _First, _It _Last, false_type)
    {
        _TRY_BEGIN
        for (; _First != _Last; ++_First) append((size_type)1, (_Elem)*_First);
        _CATCH_ALL
        _Tidy(true);
        _RERAISE;
        _CATCH_END
    }
    template <class _It> _Myt &_Append(_It _Count, _It _Ch, true_type) { return append((size_type)_Count, (_Elem)_Ch); }
    template <class _It> _Myt &_Append(_It _First, _It _Last, false_type)
    {
        _Myt _Tmp(_First, _Last);
        return append(_Tmp);
    }
    template <class _It> _Myt &_Assign(_It _Count, _It _Ch, true_type) { return assign((size_type)_Count, (_Elem)_Ch); }
    template <class _It> _Myt &_Assign(_It _First, _It _Last, false_type)
    {
        _Myt _Tmp(_First, _Last);
        return assign(_Tmp);
    }
    void _Chassign(size_type _Off, size_type _Count, _Elem _Ch)
    {
        if (_Count == 1)
            _Traits::assign(*(_Myptr() + _Off), _Ch);
        else
            _Traits::assign(_Myptr() + _Off, _Count, _Ch);
    }
    void _Copy(size_type _Newsize, size_type _Oldlen)
    {
        size_type _Newres = _Newsize | _ALLOC_MASK;
        if (max_size() < _Newres)
            _Newres = _Newsize;
        else if (_Newres / 3 < _Myres / 2 && _Myres <= max_size() - _Myres / 2)
            _Newres = _Myres + _Myres / 2;
        _Elem *_Ptr = 0;
        _TRY_BEGIN
        _Ptr = this->_Alval.allocate(_Newres + 1);
        _CATCH_ALL
        _Newres = _Newsize;
        _TRY_BEGIN
        _Ptr = this->_Alval.allocate(_Newres + 1);
        _CATCH_ALL
        _Tidy(true);
        _RERAISE;
        _CATCH_END
        _CATCH_END
        if (0 < _Oldlen) _Traits::copy(_Ptr, _Myptr(), _Oldlen);
        _Tidy(true);
        _Bx._Ptr = _Ptr;
        _Myres = _Newres;
        _Eos(_Oldlen);
    }
    void _Eos(size_type _Newsize) { _Traits::assign(_Myptr()[_Mysize = _Newsize], _Elem()); }
    bool _Grow(size_type _Newsize, bool _Trim = false)
    {
        if (max_size() < _Newsize) _Xlen();
        if (_Myres < _Newsize)
            _Copy(_Newsize, _Mysize);
        else if (_Trim && _Newsize < _BUF_SIZE)
            _Tidy(true, _Newsize < _Mysize ? _Newsize : _Mysize);
        else if (_Newsize == 0)
            _Eos(0);
        return 0 < _Newsize;
    }
    bool _Inside(const _Elem *_Ptr) const { return !(_Ptr == 0 || _Ptr < _Myptr() || _Myptr() + _Mysize <= _Ptr); }
    static size_type _Pdif(const_iterator _P2, const_iterator _P1) { return _P2._Myptr == 0 ? 0 : _P2 - _P1; }
    void _Tidy(bool _Built = false, size_type _Newsize = 0)
    {
        if (!_Built)
            ;
        else if (_BUF_SIZE <= _Myres)
        {
            _Elem *_Ptr = _Bx._Ptr;
            if (0 < _Newsize) _Traits::copy(_Bx._Buf, _Ptr, _Newsize);
            this->_Alval.deallocate(_Ptr, _Myres + 1);
        }
        _Myres = _BUF_SIZE - 1;
        _Eos(_Newsize);
    }
    _Elem *_Myptr() { return _BUF_SIZE <= _Myres ? _Bx._Ptr : _Bx._Buf; }
    const _Elem *_Myptr() const { return _BUF_SIZE <= _Myres ? _Bx._Ptr : _Bx._Buf; }
    static void _Xlen() { throw ::std::length_error("string too long"); }
    static void _Xran() { throw ::std::out_of_range("invalid string position"); }

    union _Bxty {
        _Elem _Buf[_BUF_SIZE];
        _Elem *_Ptr;
    } _Bx;
    size_type _Mysize;   /* current length */
    size_type _Myres;    /* current storage reserved for the string */
};
template <class _Elem, class _Traits, class _Alloc> const typename basic_string<_Elem, _Traits, _Alloc>::size_type
    basic_string<_Elem, _Traits, _Alloc>::npos;
template <class _Elem, class _Traits, class _Alloc>
struct _Move_operation_category<basic_string<_Elem, _Traits, _Alloc> > { typedef _Swap_move_tag _Move_cat; };

typedef basic_string<char, char_traits<char>, allocator<char> > string;
typedef basic_string<wchar_t, char_traits<wchar_t>, allocator<wchar_t> > wstring;

#define FNVNX_VC9_STR template <class _Elem, class _Traits, class _Alloc> inline
#define FNVNX_VC9_S basic_string<_Elem, _Traits, _Alloc>
FNVNX_VC9_STR FNVNX_VC9_S operator+(const FNVNX_VC9_S &_Left, const FNVNX_VC9_S &_Right) { return FNVNX_VC9_S(_Left) += _Right; }
FNVNX_VC9_STR FNVNX_VC9_S operator+(const _Elem *_Left, const FNVNX_VC9_S &_Right) { return FNVNX_VC9_S(_Left) += _Right; }
FNVNX_VC9_STR FNVNX_VC9_S operator+(const _Elem _Left, const FNVNX_VC9_S &_Right) { return FNVNX_VC9_S(1, _Left) += _Right; }
FNVNX_VC9_STR FNVNX_VC9_S operator+(const FNVNX_VC9_S &_Left, const _Elem *_Right) { return FNVNX_VC9_S(_Left) += _Right; }
FNVNX_VC9_STR FNVNX_VC9_S operator+(const FNVNX_VC9_S &_Left, const _Elem _Right) { return FNVNX_VC9_S(_Left) += _Right; }
FNVNX_VC9_STR bool operator==(const FNVNX_VC9_S &_Left, const FNVNX_VC9_S &_Right) { return _Left.compare(_Right) == 0; }
FNVNX_VC9_STR bool operator==(const _Elem *_Left, const FNVNX_VC9_S &_Right) { return _Right.compare(_Left) == 0; }
FNVNX_VC9_STR bool operator==(const FNVNX_VC9_S &_Left, const _Elem *_Right) { return _Left.compare(_Right) == 0; }
FNVNX_VC9_STR bool operator!=(const FNVNX_VC9_S &_Left, const FNVNX_VC9_S &_Right) { return !(_Left == _Right); }
FNVNX_VC9_STR bool operator!=(const _Elem *_Left, const FNVNX_VC9_S &_Right) { return !(_Left == _Right); }
FNVNX_VC9_STR bool operator!=(const FNVNX_VC9_S &_Left, const _Elem *_Right) { return !(_Left == _Right); }
FNVNX_VC9_STR bool operator<(const FNVNX_VC9_S &_Left, const FNVNX_VC9_S &_Right) { return _Left.compare(_Right) < 0; }
FNVNX_VC9_STR bool operator<(const _Elem *_Left, const FNVNX_VC9_S &_Right) { return _Right.compare(_Left) > 0; }
FNVNX_VC9_STR bool operator<(const FNVNX_VC9_S &_Left, const _Elem *_Right) { return _Left.compare(_Right) < 0; }
FNVNX_VC9_STR bool operator>(const FNVNX_VC9_S &_Left, const FNVNX_VC9_S &_Right) { return _Right < _Left; }
FNVNX_VC9_STR bool operator>(const _Elem *_Left, const FNVNX_VC9_S &_Right) { return _Right < _Left; }
FNVNX_VC9_STR bool operator>(const FNVNX_VC9_S &_Left, const _Elem *_Right) { return _Right < _Left; }
FNVNX_VC9_STR bool operator<=(const FNVNX_VC9_S &_Left, const FNVNX_VC9_S &_Right) { return !(_Right < _Left); }
FNVNX_VC9_STR bool operator<=(const _Elem *_Left, const FNVNX_VC9_S &_Right) { return !(_Right < _Left); }
FNVNX_VC9_STR bool operator<=(const FNVNX_VC9_S &_Left, const _Elem *_Right) { return !(_Right < _Left); }
FNVNX_VC9_STR bool operator>=(const FNVNX_VC9_S &_Left, const FNVNX_VC9_S &_Right) { return !(_Left < _Right); }
FNVNX_VC9_STR bool operator>=(const _Elem *_Left, const FNVNX_VC9_S &_Right) { return !(_Left < _Right); }
FNVNX_VC9_STR bool operator>=(const FNVNX_VC9_S &_Left, const _Elem *_Right) { return !(_Left < _Right); }
FNVNX_VC9_STR void swap(FNVNX_VC9_S &_Left, FNVNX_VC9_S &_Right) { _Left.swap(_Right); }
#undef FNVNX_VC9_S
#undef FNVNX_VC9_STR

/* ------------------------------------------------------------------------------------------ <xtree> / <map> */
template <class _Kty, class _Ty, class _Pr, class _Alloc, bool _Mfl>
class _Tmap_traits : public _Container_base_aux_alloc_real<_Alloc> {
    FNVNX_MSVC_BASE_END(_Container_base_aux_alloc_real<_Alloc>)
public:
    typedef _Kty key_type;
    typedef pair<const _Kty, _Ty> value_type;
    typedef _Pr key_compare;
    typedef typename _Alloc::template rebind<value_type>::other allocator_type;
    enum { _Multi = _Mfl };
    _Tmap_traits(_Pr _Parg, _Alloc _Al) : _Container_base_aux_alloc_real<_Alloc>(_Al), comp(_Parg) {}
    class value_compare {
    public:
        typedef value_type first_argument_type;
        typedef value_type second_argument_type;
        typedef bool result_type;
        bool operator()(const value_type &_Left, const value_type &_Right) const { return comp(_Left.first, _Right.first); }
        value_compare(key_compare _Pred) : comp(_Pred) {}
    protected:
        key_compare comp;
    };
    struct _Kfn {
        const _Kty &operator()(const value_type &_Val) const { return _Val.first; }
    };
    _Pr comp;
};

template <class _Traits> class _Tree_nod : public _Traits {
    FNVNX_MSVC_BASE_END(_Traits)
protected:
    typedef typename _Traits::allocator_type allocator_type;
    typedef typename _Traits::key_compare key_compare;
    typedef typename _Traits::value_type value_type;
    typedef typename allocator_type::template rebind<void>::other::pointer _Genptr;
    struct _Node;
    friend struct _Node;
    struct _Node {
        _Genptr _Left;      /* left subtree, or smallest element if head */
        _Genptr _Parent;    /* parent, or root of tree if head */
        _Genptr _Right;     /* right subtree, or largest element if head */
        value_type _Myval;  /* the stored value, unused if head */
        char _Color;        /* _Red or _Black, _Black if head */
        char _Isnil;        /* true only if head (also nil) node */
    private:
        _Node &operator=(const _Node &);
    };
    _Tree_nod(const key_compare &_Parg, allocator_type _Al) : _Traits(_Parg, _Al), _Alnod(_Al) {}
    typename allocator_type::template rebind<_Node>::other _Alnod;
};
template <class _Traits> class _Tree_ptr : public _Tree_nod<_Traits> {
    FNVNX_MSVC_BASE_END(_Tree_nod<_Traits>)
protected:
    typedef typename _Tree_nod<_Traits>::_Node _Node;
    typedef typename _Traits::allocator_type allocator_type;
    typedef typename _Traits::key_compare key_compare;
    typedef typename allocator_type::template rebind<_Node>::other::pointer _Nodeptr;
    _Tree_ptr(const key_compare &_Parg, allocator_type _Al) : _Tree_nod<_Traits>(_Parg, _Al), _Alptr(_Al) {}
    typename allocator_type::template rebind<_Nodeptr>::other _Alptr;
};
template <class _Traits> class _Tree_val : public _Tree_ptr<_Traits> {
    FNVNX_MSVC_BASE_END(_Tree_ptr<_Traits>)
protected:
    typedef typename _Traits::allocator_type allocator_type;
    typedef typename _Traits::key_compare key_compare;
    _Tree_val(const key_compare &_Parg, allocator_type _Al) : _Tree_ptr<_Traits>(_Parg, _Al), _Alval(_Al) {}
    allocator_type _Alval;
};

template <class _Traits> class _Tree : public _Tree_val<_Traits> {
    FNVNX_MSVC_BASE_END(_Tree_val<_Traits>)
public:
    typedef _Tree<_Traits> _Myt;
    typedef _Tree_val<_Traits> _Mybase;
    typedef typename _Traits::key_type key_type;
    typedef typename _Traits::key_compare key_compare;
    typedef typename _Traits::value_compare value_compare;
    typedef typename _Traits::value_type value_type;
    typedef typename _Traits::allocator_type allocator_type;
    typedef typename _Traits::_Kfn _Kfn;
    typedef typename _Tree_nod<_Traits>::_Node _Node;
    typedef typename _Tree_ptr<_Traits>::_Nodeptr _Nodeptr;
    typedef _Nodeptr &_Nodepref;
    typedef const key_type &_Keyref;
    typedef char &_Charref;
    typedef value_type &_Vref;
    typedef typename allocator_type::size_type size_type;
    typedef typename allocator_type::difference_type _Dift;
    typedef _Dift difference_type;
    typedef typename allocator_type::pointer _Tptr;
    typedef typename allocator_type::const_pointer _Ctptr;
    typedef typename allocator_type::reference _Reft;
    typedef _Tptr pointer;
    typedef _Ctptr const_pointer;
    typedef _Reft reference;
    typedef typename allocator_type::const_reference const_reference;
    enum _Redbl { _Red, _Black };

    static char &_Color(_Nodeptr _Pnode) { return (char &)(*_Pnode)._Color; }
    static char &_Isnil(_Nodeptr _Pnode) { return (char &)(*_Pnode)._Isnil; }
    static _Keyref _Key(_Nodeptr _Pnode) { return (_Keyref)_Kfn()(_Myval(_Pnode)); }
    static _Nodepref _Left(_Nodeptr _Pnode) { return (_Nodepref)(*_Pnode)._Left; }
    static _Nodepref _Parent(_Nodeptr _Pnode) { return (_Nodepref)(*_Pnode)._Parent; }
    static _Nodepref _Right(_Nodeptr _Pnode) { return (_Nodepref)(*_Pnode)._Right; }
    static _Vref _Myval(_Nodeptr _Pnode) { return (_Vref)(*_Pnode)._Myval; }

    class const_iterator : public _Iterator_base_aux {
    public:
        typedef bidirectional_iterator_tag iterator_category;
        typedef _Dift difference_type;
        typedef typename _Tree::value_type value_type;
        typedef _Ctptr pointer;
        typedef const_reference reference;
        const_iterator() : _Ptr(0) {}
        const_iterator(_Nodeptr _Pnode, const _Myt *_Plist) : _Ptr(_Pnode)
        {
            FNVNX_VC9_VALIDATE(_Plist != 0);
            this->_Set_container(_Plist);
        }
        const_reference operator*() const
        {
            FNVNX_VC9_VALIDATE(this->_Has_container());
            FNVNX_VC9_VALIDATE(_Ptr != ((const _Myt *)this->_Getmycont())->_Myhead);
            return _Myval(_Ptr);
        }
        _Ctptr operator->() const { return &**this; }
        const_iterator &operator++() { _Inc(); return *this; }
        const_iterator operator++(int) { const_iterator _Tmp = *this; ++*this; return _Tmp; }
        const_iterator &operator--() { _Dec(); return *this; }
        const_iterator operator--(int) { const_iterator _Tmp = *this; --*this; return _Tmp; }
        bool operator==(const const_iterator &_Right) const
        {
            FNVNX_VC9_VALIDATE(this->_Has_container() && this->_Same_container(_Right));
            return _Ptr == _Right._Ptr;
        }
        bool operator!=(const const_iterator &_Right) const { return !(*this == _Right); }
        void _Dec()
        {
            FNVNX_VC9_VALIDATE(this->_Has_container());
            if (_Isnil(_Ptr))
                _Ptr = _Right(_Ptr);             /* end() ==> rightmost */
            else if (!_Isnil(_Left(_Ptr)))
                _Ptr = _Max(_Left(_Ptr));
            else
            {
                _Nodeptr _Pnode;
                while (!_Isnil(_Pnode = _Parent(_Ptr)) && _Ptr == _Left(_Pnode)) _Ptr = _Pnode;
                if (_Isnil(_Ptr))
                    ;
                else
                    _Ptr = _Pnode;
            }
        }
        void _Inc()
        {
            FNVNX_VC9_VALIDATE(this->_Has_container());
            if (_Isnil(_Ptr))
                _invalid_parameter_noinfo();     /* end() incremented */
            else if (!_Isnil(_Right(_Ptr)))
                _Ptr = _Min(_Right(_Ptr));
            else
            {
                _Nodeptr _Pnode;
                while (!_Isnil(_Pnode = _Parent(_Ptr)) && _Ptr == _Right(_Pnode)) _Ptr = _Pnode;
                _Ptr = _Pnode;
            }
        }
        _Nodeptr _Mynode() const { return _Ptr; }
        _Nodeptr _Ptr;
    };
    class iterator : public const_iterator {
    public:
        typedef bidirectional_iterator_tag iterator_category;
        typedef _Dift difference_type;
        typedef typename _Tree::value_type value_type;
        typedef _Tptr pointer;
        typedef _Reft reference;
        iterator() {}
        iterator(_Nodeptr _Pnode, const _Myt *_Plist) : const_iterator(_Pnode, _Plist) {}
        reference operator*() const { return (reference) * *(const const_iterator *)this; }
        pointer operator->() const { return &**this; }
        iterator &operator++() { ++*(const_iterator *)this; return *this; }
        iterator operator++(int) { iterator _Tmp = *this; ++*this; return _Tmp; }
        iterator &operator--() { --*(const_iterator *)this; return *this; }
        iterator operator--(int) { iterator _Tmp = *this; --*this; return _Tmp; }
    };
    typedef ::std::reverse_iterator<iterator> reverse_iterator;
    typedef ::std::reverse_iterator<const_iterator> const_reverse_iterator;
    typedef pair<iterator, bool> _Pairib;
    typedef pair<iterator, iterator> _Pairii;
    typedef pair<const_iterator, const_iterator> _Paircc;

    explicit _Tree(const key_compare &_Parg, const allocator_type &_Al) : _Mybase(_Parg, _Al) { _Init(); }
    _Tree(const _Myt &_Right) : _Mybase(_Right.key_comp(), _Right.get_allocator())
    {
        _Init();
        _TRY_BEGIN
        _Copy(_Right);
        _CATCH_ALL
        _Tidy();
        _RERAISE;
        _CATCH_END
    }
    ~_Tree() { _Tidy(); }
    _Myt &operator=(const _Myt &_Right)
    {
        if (this != &_Right)
        {
            erase(begin(), end());
            this->comp = _Right.comp;
            _Copy(_Right);
        }
        return *this;
    }
    iterator begin() { return iterator(_Lmost(), this); }
    const_iterator begin() const { return const_iterator(_Lmost(), this); }
    iterator end() { return iterator(_Myhead, this); }
    const_iterator end() const { return const_iterator(_Myhead, this); }
    reverse_iterator rbegin() { return reverse_iterator(end()); }
    const_reverse_iterator rbegin() const { return const_reverse_iterator(end()); }
    reverse_iterator rend() { return reverse_iterator(begin()); }
    const_reverse_iterator rend() const { return const_reverse_iterator(begin()); }
    size_type size() const { return _Mysize; }
    size_type max_size() const { return this->_Alval.max_size(); }
    bool empty() const { return size() == 0; }
    allocator_type get_allocator() const { return this->_Alval; }
    key_compare key_comp() const { return this->comp; }
    value_compare value_comp() const { return value_compare(key_comp()); }

    _Pairib insert(const value_type &_Val)
    {
        _Nodeptr _Trynode = _Root();
        _Nodeptr _Wherenode = _Myhead;
        bool _Addleft = true;
        while (!_Isnil(_Trynode))
        {
            _Wherenode = _Trynode;
            _Addleft = this->comp(_Kfn()(_Val), _Key(_Trynode));
            _Trynode = _Addleft ? _Left(_Trynode) : _Right(_Trynode);
        }
        if (this->_Multi) return _Pairib(_Insert(_Addleft, _Wherenode, _Val), true);
        iterator _Where = iterator(_Wherenode, this);
        if (!_Addleft)
            ;
        else if (_Where == begin())
            return _Pairib(_Insert(true, _Wherenode, _Val), true);
        else
            --_Where;
        if (this->comp(_Key(_Where._Mynode()), _Kfn()(_Val))) return _Pairib(_Insert(_Addleft, _Wherenode, _Val), true);
        return _Pairib(_Where, false);
    }
    iterator insert(const_iterator _Where, const value_type &_Val)
    {
        const_iterator _Next;
        if (size() == 0)
            return _Insert(true, _Myhead, _Val);
        else if (this->_Multi)
            return insert(_Val).first;
        else if (_Where == begin())
        {
            if (this->comp(_Kfn()(_Val), _Key(_Where._Mynode()))) return _Insert(true, _Where._Mynode(), _Val);
        }
        else if (_Where == end())
        {
            if (this->comp(_Key(_Rmost()), _Kfn()(_Val))) return _Insert(false, _Rmost(), _Val);
        }
        else if (this->comp(_Key((--(_Next = _Where))._Mynode()), _Kfn()(_Val)) &&
                 this->comp(_Kfn()(_Val), _Key(_Where._Mynode())))
        {
            if (_Isnil(_Right(_Next._Mynode())))
                return _Insert(false, _Next._Mynode(), _Val);
            else
                return _Insert(true, _Where._Mynode(), _Val);
        }
        return insert(_Val).first;
    }
    template <class _Iter> void insert(_Iter _First, _Iter _Last)
    {
        for (; _First != _Last; ++_First) insert(*_First);
    }
    iterator erase(const_iterator _Where)
    {
        if (_Isnil(_Where._Mynode())) throw ::std::out_of_range("invalid map/set<T> iterator");
        _Nodeptr _Fixnode;
        _Nodeptr _Fixnodeparent;
        _Nodeptr _Erasednode = _Where._Mynode();
        _Nodeptr _Pnode = _Erasednode;
        ++_Where;
        if (_Isnil(_Left(_Pnode)))
            _Fixnode = _Right(_Pnode);
        else if (_Isnil(_Right(_Pnode)))
            _Fixnode = _Left(_Pnode);
        else
        {
            _Pnode = _Where._Mynode();
            _Fixnode = _Right(_Pnode);
        }
        if (_Pnode == _Erasednode)
        {
            _Fixnodeparent = _Parent(_Erasednode);
            if (!_Isnil(_Fixnode)) _Parent(_Fixnode) = _Fixnodeparent;
            if (_Root() == _Erasednode)
                _Root() = _Fixnode;
            else if (_Left(_Fixnodeparent) == _Erasednode)
                _Left(_Fixnodeparent) = _Fixnode;
            else
                _Right(_Fixnodeparent) = _Fixnode;
            if (_Lmost() == _Erasednode) _Lmost() = _Isnil(_Fixnode) ? _Fixnodeparent : _Min(_Fixnode);
            if (_Rmost() == _Erasednode) _Rmost() = _Isnil(_Fixnode) ? _Fixnodeparent : _Max(_Fixnode);
        }
        else
        {
            _Parent(_Left(_Erasednode)) = _Pnode;
            _Left(_Pnode) = _Left(_Erasednode);
            if (_Pnode == _Right(_Erasednode))
                _Fixnodeparent = _Pnode;
            else
            {
                _Fixnodeparent = _Parent(_Pnode);
                if (!_Isnil(_Fixnode)) _Parent(_Fixnode) = _Fixnodeparent;
                _Left(_Fixnodeparent) = _Fixnode;
                _Right(_Pnode) = _Right(_Erasednode);
                _Parent(_Right(_Erasednode)) = _Pnode;
            }
            if (_Root() == _Erasednode)
                _Root() = _Pnode;
            else if (_Left(_Parent(_Erasednode)) == _Erasednode)
                _Left(_Parent(_Erasednode)) = _Pnode;
            else
                _Right(_Parent(_Erasednode)) = _Pnode;
            _Parent(_Pnode) = _Parent(_Erasednode);
            ::std::swap(_Color(_Pnode), _Color(_Erasednode));
        }
        if (_Color(_Erasednode) == _Black)
        {
            for (; _Fixnode != _Root() && _Color(_Fixnode) == _Black; _Fixnodeparent = _Parent(_Fixnode))
                if (_Fixnode == _Left(_Fixnodeparent))
                {
                    _Pnode = _Right(_Fixnodeparent);
                    if (_Color(_Pnode) == _Red)
                    {
                        _Color(_Pnode) = _Black;
                        _Color(_Fixnodeparent) = _Red;
                        _Lrotate(_Fixnodeparent);
                        _Pnode = _Right(_Fixnodeparent);
                    }
                    if (_Isnil(_Pnode))
                        _Fixnode = _Fixnodeparent;
                    else if (_Color(_Left(_Pnode)) == _Black && _Color(_Right(_Pnode)) == _Black)
                    {
                        _Color(_Pnode) = _Red;
                        _Fixnode = _Fixnodeparent;
                    }
                    else
                    {
                        if (_Color(_Right(_Pnode)) == _Black)
                        {
                            _Color(_Left(_Pnode)) = _Black;
                            _Color(_Pnode) = _Red;
                            _Rrotate(_Pnode);
                            _Pnode = _Right(_Fixnodeparent);
                        }
                        _Color(_Pnode) = _Color(_Fixnodeparent);
                        _Color(_Fixnodeparent) = _Black;
                        _Color(_Right(_Pnode)) = _Black;
                        _Lrotate(_Fixnodeparent);
                        break;
                    }
                }
                else
                {
                    _Pnode = _Left(_Fixnodeparent);
                    if (_Color(_Pnode) == _Red)
                    {
                        _Color(_Pnode) = _Black;
                        _Color(_Fixnodeparent) = _Red;
                        _Rrotate(_Fixnodeparent);
                        _Pnode = _Left(_Fixnodeparent);
                    }
                    if (_Isnil(_Pnode))
                        _Fixnode = _Fixnodeparent;
                    else if (_Color(_Right(_Pnode)) == _Black && _Color(_Left(_Pnode)) == _Black)
                    {
                        _Color(_Pnode) = _Red;
                        _Fixnode = _Fixnodeparent;
                    }
                    else
                    {
                        if (_Color(_Left(_Pnode)) == _Black)
                        {
                            _Color(_Right(_Pnode)) = _Black;
                            _Color(_Pnode) = _Red;
                            _Lrotate(_Pnode);
                            _Pnode = _Left(_Fixnodeparent);
                        }
                        _Color(_Pnode) = _Color(_Fixnodeparent);
                        _Color(_Fixnodeparent) = _Black;
                        _Color(_Left(_Pnode)) = _Black;
                        _Rrotate(_Fixnodeparent);
                        break;
                    }
                }
            _Color(_Fixnode) = _Black;
        }
        this->_Alval.destroy(&_Myval(_Erasednode));
        this->_Alnod.deallocate(_Erasednode, 1);
        if (0 < _Mysize) --_Mysize;
        return iterator(_Where._Mynode(), this);
    }
    iterator erase(const_iterator _First, const_iterator _Last)
    {
        if (_First == begin() && _Last == end())
        {
            clear();
            return begin();
        }
        while (_First != _Last) erase(_First++);
        return iterator(_First._Mynode(), this);
    }
    size_type erase(const key_type &_Keyval)
    {
        _Pairii _Where = equal_range(_Keyval);
        size_type _Num = 0;
        for (iterator _It = _Where.first; _It != _Where.second; ++_It) ++_Num;
        erase(_Where.first, _Where.second);
        return _Num;
    }
    void clear()
    {
        _Erase(_Root());
        _Root() = _Myhead, _Lmost() = _Myhead, _Rmost() = _Myhead;
        _Mysize = 0;
    }
    iterator find(const key_type &_Keyval)
    {
        iterator _Where = lower_bound(_Keyval);
        return _Where == end() || this->comp(_Keyval, _Key(_Where._Mynode())) ? end() : _Where;
    }
    const_iterator find(const key_type &_Keyval) const
    {
        const_iterator _Where = lower_bound(_Keyval);
        return _Where == end() || this->comp(_Keyval, _Key(_Where._Mynode())) ? end() : _Where;
    }
    size_type count(const key_type &_Keyval) const
    {
        _Paircc _Ans = equal_range(_Keyval);
        size_type _Num = 0;
        for (const_iterator _It = _Ans.first; _It != _Ans.second; ++_It) ++_Num;
        return _Num;
    }
    iterator lower_bound(const key_type &_Keyval) { return iterator(_Lbound(_Keyval), this); }
    const_iterator lower_bound(const key_type &_Keyval) const { return const_iterator(_Lbound(_Keyval), this); }
    iterator upper_bound(const key_type &_Keyval) { return iterator(_Ubound(_Keyval), this); }
    const_iterator upper_bound(const key_type &_Keyval) const { return const_iterator(_Ubound(_Keyval), this); }
    _Pairii equal_range(const key_type &_Keyval) { return _Pairii(lower_bound(_Keyval), upper_bound(_Keyval)); }
    _Paircc equal_range(const key_type &_Keyval) const { return _Paircc(lower_bound(_Keyval), upper_bound(_Keyval)); }
    void swap(_Myt &_Right)
    {
        if (this == &_Right)
            ;
        else if (get_allocator() == _Right.get_allocator())
        {
            this->_Swap_aux(_Right);
            ::std::swap(this->comp, _Right.comp);
            ::std::swap(_Myhead, _Right._Myhead);
            ::std::swap(_Mysize, _Right._Mysize);
        }
        else
        {
            _Myt _Tmp = _Right;
            _Right = *this, *this = _Tmp;
        }
    }

protected:
    void _Copy(const _Myt &_Right)
    {
        _Root() = _Copy(_Right._Root(), _Myhead);
        _Mysize = _Right.size();
        if (!_Isnil(_Root()))
        {
            _Lmost() = _Min(_Root());
            _Rmost() = _Max(_Root());
        }
        else
            _Lmost() = _Myhead, _Rmost() = _Myhead;
    }
    _Nodeptr _Copy(_Nodeptr _Rootnode, _Nodeptr _Wherenode)
    {
        _Nodeptr _Newroot = _Myhead;
        if (!_Isnil(_Rootnode))
        {
            _Nodeptr _Pnode = _Buynode(_Myhead, _Wherenode, _Myhead, _Myval(_Rootnode), _Color(_Rootnode));
            if (_Isnil(_Newroot)) _Newroot = _Pnode;
            _TRY_BEGIN
            _Left(_Pnode) = _Copy(_Left(_Rootnode), _Pnode);
            _Right(_Pnode) = _Copy(_Right(_Rootnode), _Pnode);
            _CATCH_ALL
            _Erase(_Newroot);
            _RERAISE;
            _CATCH_END
        }
        return _Newroot;
    }
    void _Erase(_Nodeptr _Rootnode)
    {
        for (_Nodeptr _Pnode = _Rootnode; !_Isnil(_Pnode); _Rootnode = _Pnode)
        {
            _Erase(_Right(_Pnode));
            _Pnode = _Left(_Pnode);
            this->_Alval.destroy(&_Myval(_Rootnode));
            this->_Alnod.deallocate(_Rootnode, 1);
        }
    }
    void _Init()
    {
        _Myhead = _Buynode();
        _Isnil(_Myhead) = true;
        _Root() = _Myhead;
        _Lmost() = _Myhead, _Rmost() = _Myhead;
        _Mysize = 0;
    }
    iterator _Insert(bool _Addleft, _Nodeptr _Wherenode, const value_type &_Val)
    {
        if (max_size() - 1 <= _Mysize) throw ::std::length_error("map/set<T> too long");
        _Nodeptr _Newnode = _Buynode(_Myhead, _Wherenode, _Myhead, _Val, _Red);
        ++_Mysize;
        if (_Wherenode == _Myhead)
        {
            _Root() = _Newnode;
            _Lmost() = _Newnode, _Rmost() = _Newnode;
        }
        else if (_Addleft)
        {
            _Left(_Wherenode) = _Newnode;
            if (_Wherenode == _Lmost()) _Lmost() = _Newnode;
        }
        else
        {
            _Right(_Wherenode) = _Newnode;
            if (_Wherenode == _Rmost()) _Rmost() = _Newnode;
        }
        for (_Nodeptr _Pnode = _Newnode; _Color(_Parent(_Pnode)) == _Red;)
            if (_Parent(_Pnode) == _Left(_Parent(_Parent(_Pnode))))
            {
                _Wherenode = _Right(_Parent(_Parent(_Pnode)));
                if (_Color(_Wherenode) == _Red)
                {
                    _Color(_Parent(_Pnode)) = _Black;
                    _Color(_Wherenode) = _Black;
                    _Color(_Parent(_Parent(_Pnode))) = _Red;
                    _Pnode = _Parent(_Parent(_Pnode));
                }
                else
                {
                    if (_Pnode == _Right(_Parent(_Pnode)))
                    {
                        _Pnode = _Parent(_Pnode);
                        _Lrotate(_Pnode);
                    }
                    _Color(_Parent(_Pnode)) = _Black;
                    _Color(_Parent(_Parent(_Pnode))) = _Red;
                    _Rrotate(_Parent(_Parent(_Pnode)));
                }
            }
            else
            {
                _Wherenode = _Left(_Parent(_Parent(_Pnode)));
                if (_Color(_Wherenode) == _Red)
                {
                    _Color(_Parent(_Pnode)) = _Black;
                    _Color(_Wherenode) = _Black;
                    _Color(_Parent(_Parent(_Pnode))) = _Red;
                    _Pnode = _Parent(_Parent(_Pnode));
                }
                else
                {
                    if (_Pnode == _Left(_Parent(_Pnode)))
                    {
                        _Pnode = _Parent(_Pnode);
                        _Rrotate(_Pnode);
                    }
                    _Color(_Parent(_Pnode)) = _Black;
                    _Color(_Parent(_Parent(_Pnode))) = _Red;
                    _Lrotate(_Parent(_Parent(_Pnode)));
                }
            }
        _Color(_Root()) = _Black;
        return iterator(_Newnode, this);
    }
    _Nodeptr _Lbound(const key_type &_Keyval) const
    {
        _Nodeptr _Pnode = _Root();
        _Nodeptr _Wherenode = _Myhead;
        while (!_Isnil(_Pnode))
            if (this->comp(_Key(_Pnode), _Keyval))
                _Pnode = _Right(_Pnode);
            else
            {
                _Wherenode = _Pnode;
                _Pnode = _Left(_Pnode);
            }
        return _Wherenode;
    }
    _Nodeptr _Ubound(const key_type &_Keyval) const
    {
        _Nodeptr _Pnode = _Root();
        _Nodeptr _Wherenode = _Myhead;
        while (!_Isnil(_Pnode))
            if (this->comp(_Keyval, _Key(_Pnode)))
            {
                _Wherenode = _Pnode;
                _Pnode = _Left(_Pnode);
            }
            else
                _Pnode = _Right(_Pnode);
        return _Wherenode;
    }
    _Nodeptr &_Lmost() const { return _Left(_Myhead); }
    _Nodeptr &_Rmost() const { return _Right(_Myhead); }
    _Nodeptr &_Root() const { return _Parent(_Myhead); }
    void _Lrotate(_Nodeptr _Wherenode)
    {
        _Nodeptr _Pnode = _Right(_Wherenode);
        _Right(_Wherenode) = _Left(_Pnode);
        if (!_Isnil(_Left(_Pnode))) _Parent(_Left(_Pnode)) = _Wherenode;
        _Parent(_Pnode) = _Parent(_Wherenode);
        if (_Wherenode == _Root())
            _Root() = _Pnode;
        else if (_Wherenode == _Left(_Parent(_Wherenode)))
            _Left(_Parent(_Wherenode)) = _Pnode;
        else
            _Right(_Parent(_Wherenode)) = _Pnode;
        _Left(_Pnode) = _Wherenode;
        _Parent(_Wherenode) = _Pnode;
    }
    void _Rrotate(_Nodeptr _Wherenode)
    {
        _Nodeptr _Pnode = _Left(_Wherenode);
        _Left(_Wherenode) = _Right(_Pnode);
        if (!_Isnil(_Right(_Pnode))) _Parent(_Right(_Pnode)) = _Wherenode;
        _Parent(_Pnode) = _Parent(_Wherenode);
        if (_Wherenode == _Root())
            _Root() = _Pnode;
        else if (_Wherenode == _Right(_Parent(_Wherenode)))
            _Right(_Parent(_Wherenode)) = _Pnode;
        else
            _Left(_Parent(_Wherenode)) = _Pnode;
        _Right(_Pnode) = _Wherenode;
        _Parent(_Wherenode) = _Pnode;
    }
    static _Nodeptr _Max(_Nodeptr _Pnode)
    {
        while (!_Isnil(_Right(_Pnode))) _Pnode = _Right(_Pnode);
        return _Pnode;
    }
    static _Nodeptr _Min(_Nodeptr _Pnode)
    {
        while (!_Isnil(_Left(_Pnode))) _Pnode = _Left(_Pnode);
        return _Pnode;
    }
    _Nodeptr _Buynode()
    {
        _Nodeptr _Wherenode = this->_Alnod.allocate(1);
        _Left(_Wherenode) = 0;
        _Parent(_Wherenode) = 0;
        _Right(_Wherenode) = 0;
        _Color(_Wherenode) = _Black;
        _Isnil(_Wherenode) = false;
        return _Wherenode;
    }
    _Nodeptr _Buynode(_Nodeptr _Larg, _Nodeptr _Parg, _Nodeptr _Rarg, const value_type &_Val, char _Carg)
    {
        _Nodeptr _Wherenode = this->_Alnod.allocate(1);
        _TRY_BEGIN
        ::new ((void *)&_Myval(_Wherenode)) value_type(_Val);
        _CATCH_ALL
        this->_Alnod.deallocate(_Wherenode, 1);
        _RERAISE;
        _CATCH_END
        _Left(_Wherenode) = _Larg;
        _Parent(_Wherenode) = _Parg;
        _Right(_Wherenode) = _Rarg;
        _Color(_Wherenode) = _Carg;
        _Isnil(_Wherenode) = false;
        return _Wherenode;
    }
    void _Tidy()
    {
        erase(begin(), end());
        this->_Alnod.deallocate(_Myhead, 1);
        _Myhead = 0, _Mysize = 0;
    }

public:
    _Nodeptr _Myhead;   /* pointer to head node */
    size_type _Mysize;  /* number of elements */
};
template <class _Traits> struct _Move_operation_category<_Tree<_Traits> > { typedef _Swap_move_tag _Move_cat; };

template <class _Kty, class _Ty, class _Pr = less<_Kty>, class _Alloc = allocator<pair<const _Kty, _Ty> > >
class map : public _Tree<_Tmap_traits<_Kty, _Ty, _Pr, _Alloc, false> > {
public:
    typedef map<_Kty, _Ty, _Pr, _Alloc> _Myt;
    typedef _Tree<_Tmap_traits<_Kty, _Ty, _Pr, _Alloc, false> > _Mybase;
    typedef _Kty key_type;
    typedef _Ty mapped_type;
    typedef _Ty referent_type;
    typedef _Pr key_compare;
    typedef typename _Mybase::value_compare value_compare;
    typedef typename _Mybase::allocator_type allocator_type;
    typedef typename _Mybase::size_type size_type;
    typedef typename _Mybase::difference_type difference_type;
    typedef typename _Mybase::pointer pointer;
    typedef typename _Mybase::const_pointer const_pointer;
    typedef typename _Mybase::reference reference;
    typedef typename _Mybase::const_reference const_reference;
    typedef typename _Mybase::iterator iterator;
    typedef typename _Mybase::const_iterator const_iterator;
    typedef typename _Mybase::reverse_iterator reverse_iterator;
    typedef typename _Mybase::const_reverse_iterator const_reverse_iterator;
    typedef typename _Mybase::value_type value_type;
    map() : _Mybase(key_compare(), allocator_type()) {}
    explicit map(const key_compare &_Pred) : _Mybase(_Pred, allocator_type()) {}
    map(const key_compare &_Pred, const allocator_type &_Al) : _Mybase(_Pred, _Al) {}
    template <class _Iter> map(_Iter _First, _Iter _Last) : _Mybase(key_compare(), allocator_type())
    {
        for (; _First != _Last; ++_First) this->insert(*_First);
    }
    mapped_type &operator[](const key_type &_Keyval)
    {
        iterator _Where = this->lower_bound(_Keyval);
        if (_Where == this->end() || this->comp(_Keyval, this->_Key(_Where._Mynode())))
            _Where = this->insert(_Where, value_type(_Keyval, mapped_type()));
        return (*_Where).second;
    }
};
template <class _Kty, class _Ty, class _Pr, class _Alloc>
struct _Move_operation_category<map<_Kty, _Ty, _Pr, _Alloc> > { typedef _Swap_move_tag _Move_cat; };
template <class _Kty, class _Ty, class _Pr, class _Alloc>
inline void swap(map<_Kty, _Ty, _Pr, _Alloc> &_Left, map<_Kty, _Ty, _Pr, _Alloc> &_Right) { _Left.swap(_Right); }

/* the layouts MSVC gives these classes on x86 (see the top of the file) */
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Winvalid-offsetof"
typedef vector<int> _Layout_vector;
typedef map<string, string> _Layout_map;
static_assert(sizeof(string) == 28 && offsetof(string, _Bx) == 4 && offsetof(string, _Mysize) == 0x14 &&
              offsetof(string, _Myres) == 0x18, "fnvnx_vc9: string layout");
static_assert(sizeof(_Layout_vector) == 24 && offsetof(_Layout_vector, _Myfirst) == 0xc &&
              offsetof(_Layout_vector, _Mylast) == 0x10 && offsetof(_Layout_vector, _Myend) == 0x14, "fnvnx_vc9: vector layout");
static_assert(sizeof(_Layout_map) == 32 && offsetof(_Layout_map, _Myhead) == 0x18 && offsetof(_Layout_map, _Mysize) == 0x1c,
              "fnvnx_vc9: map layout");
static_assert(sizeof(_Layout_vector::iterator) == 8 && sizeof(string::iterator) == 8 && sizeof(_Layout_map::iterator) == 8,
              "fnvnx_vc9: iterator layout");
#pragma GCC diagnostic pop
} /* namespace fnvnx_vc9 */

#endif /* __cplusplus */
#endif /* FNVNX_VC9_H */
