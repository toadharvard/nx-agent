typedef int _LOCK_T;
typedef struct { _LOCK_T lock; unsigned thread_tag; unsigned counter; } _LOCK_RECURSIVE_T;
