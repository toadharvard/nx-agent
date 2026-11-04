static inline void _ReadWriteBarrier(void) { __atomic_thread_fence(__ATOMIC_SEQ_CST); }
