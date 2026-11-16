# l2-w3: test_msvcmodel.py moved onto recipe roots and deleted

test_msvcmodel.py called $CXX directly (a second build path). Every case now runs on a recipe root; the file and its .cpp are deleted, and
verify/tests/test_guest.py no longer runs it. Disposition per case (none dropped: no recipe test covered any of them):

| case | now | why kept |
|---|---|---|
| size_t = unsigned int, ptrdiff_t = int, sizeof and pointer-difference types, `_Z5typesjiji` / cl `?types@@YAXIHIH@Z` | test_ldbl.py model(), tests/ldbl/model.cpp (static_assert + name rows) | the exe's data model; test_guest's `_Z2szj` covers only size_t of one unit |
| same for C | test_cc1.py "data model, C" | the C front end shares the patch |
| new[] byte counts n_d 16, n_a8 28, n_nm 60, n_nd 72, n_nv 88, n_n 40 | model(): constant before `_Znaj` / `heap_alloc` read from the object's relocations | MSVC cookie rule |
| sized delete[] element sizes d_nm 20, d_nd 24, d_nv 28, d_n 12 | model(): w1 before `heap_free` | MSVC delete[] size |
| no count for trivially destructible arrays | model(): no `#-4]` access | cookie only with a non-trivial destructor |
| count 4 bytes before the array, block starts there | model(): sub #4 then load from it (an ldar: the product order plugin), two sub #4 | cookie location |
| overflowing new[] size is the largest size, no bad_array_new_length | model(): csinv / mov 0xffffffff, symbol absent from undefined set | no throw before the call |

Assembly-text matching is replaced by `objdump -dr` of the recipe's object (relocation names the callee).
