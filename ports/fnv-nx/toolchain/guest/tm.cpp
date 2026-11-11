/* The transactional-memory entry points that libstdc++'s cow-stdexcept.o (std::logic_error, std::runtime_error, ...) names.
 * Its transactional clones of the exception classes call them from inside a `__transaction_atomic` region; they are weak references there
 * because libitm is optional in the toolchain, and the link stage admits no weak reference without a definition.
 *
 * Semantics (the libitm ABI, with the transaction manager reduced to the case this program can have): the guest links no
 * _ITM_beginTransaction, and no code of the program opens a transaction, so a transactional access is an access outside any
 * speculation: nothing to log, nothing to roll back, and every action registered with the transaction commits at once.  Each
 * function is therefore the plain operation it stands for.  (libitm in serial-irrevocable mode defines them the same way.)
 * Prototypes: gcc libitm/libitm.h (_ITM_RU1, _ITM_RU4, _ITM_memcpyRnWt, _ITM_memcpyRtWn, _ITM_addUserCommitAction) and the
 * transactional clones of ::operator new[] / ::operator delete (_ZGTtnam, _ZGTtdlPv). */
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <new>

extern "C" {
uint8_t _ITM_RU1(const uint8_t *p) { return *p; }
uint32_t _ITM_RU4(const uint32_t *p) { return *p; }
void _ITM_memcpyRnWt(void *dst, const void *src, size_t n) { memcpy(dst, src, n); }
void _ITM_memcpyRtWn(void *dst, const void *src, size_t n) { memcpy(dst, src, n); }
void _ITM_addUserCommitAction(void (*action)(void *), uint64_t resuming_id, void *arg) {
    (void)resuming_id;   /* names the transaction the action belongs to: the one that commits now */
    action(arg);
}
void *_ZGTtnam(size_t n) { return ::operator new[](n); }
void _ZGTtdlPv(void *p) { ::operator delete(p); }
}
