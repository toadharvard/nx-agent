// NxObj: the COM object model of our D3D9 classes (rules and sources: README.md "Object model").
#pragma once
#if !defined(_WIN32) && !defined(WINAPI)
#define WINAPI  /* shim: one calling convention (as gen/d9wrap_port.h) */
#endif
#include <d3d9.h>
#include <new>
#include <stddef.h>
#include <stdint.h>
#ifndef E_POINTER
#define E_POINTER ((HRESULT)0x80004003L)
#endif
// The allocation contract of the whole layer (README "Allocation failure"): memory is a RESULT, never a trap. nxd3d_alloc returns NULL when the platform has no memory left (the guest's address window is reserved
// whole, so this is an ordinary, reachable state); every caller returns the failure upward as the D3D9 HRESULT of the call that needed the memory, with nothing partial left behind.
// The compiler enforces completeness: the result is warn_unused_result (-Werror=unused-result), the allocators are `malloc` attributed WITHOUT returns_nonnull, so GCC's -fanalyzer tracks "possibly NULL" through
// every use (tests/l6/oom: gate N; build flags in tests/l6/oom/test_oom.py). Clang has no such attribute: the analyzer gate is GCC's.
#if defined(__GNUC__) && !defined(__clang__)
#define NX_ALLOCATOR(dealloc) __attribute__((malloc(dealloc, 1), warn_unused_result))
#else
#define NX_ALLOCATOR(dealloc) __attribute__((warn_unused_result))
#endif
#define NX_MUST_USE __attribute__((warn_unused_result))
#define NX_USE_MAKE __attribute__((deprecated("use nx_make<T>() / nx_make_plain<T>(): the allocation is a result (obj.h, README Allocation failure)")))
void nxd3d_free(void* p);
void* nxd3d_alloc(size_t n) NX_ALLOCATOR(nxd3d_free);  // the only CPU allocator the layer uses (obj.cpp: malloc/free); replaceable per platform; NULL = out of memory
enum { NX_FACE_SIZE = 8 };  // face.h
void nx_free_face(void* object);                               // the block of an object made by nx_alloc_face (NX_FACE_SIZE bytes in front of it)
void* nx_alloc_face(size_t n) NX_ALLOCATOR(nx_free_face);      // n bytes behind a zeroed guest face; the pointer is the OBJECT's address; NULL = out of memory
// IUnknown objects of the application kept as private data (D3DSPD_IUNKNOWN): AddRef / Release on them are calls into the application.
// app_unk_guest.cpp (lifted guest: fnvnx_guest_invoke) or app_unk_native.cpp (native hosts: tests, the replayer) defines them; exactly one is linked.
ULONG nxd3d_app_addref(IUnknown* u); ULONG nxd3d_app_release(IUnknown* u);
enum : uint32_t { NX_SPD_NATIVE = 0x40000000u };  // setPrivateData flag of the layer's own callers: the IUnknown is a native object of the layer

struct NxObj;
struct NxClassInfo {  // generated (gen_impl.py): IID lattice = this interface then its bases up to IUnknown
    const char* name; const GUID* iids; uint8_t n_iids; uint8_t rtype /* D3DRESOURCETYPE, 0 = not a resource */; void (*destroy)(NxObj*);
};
struct NxPriv { GUID g; uint32_t size, flags; NxPriv* next; union { IUnknown* u; uint8_t* d; }; };
extern const GUID IID_NxObj;  // private: QueryInterface(IID_NxObj) yields the NxObj* of one of OUR objects, without AddRef

struct NxObj {
    // ---- identity
    const NxClassInfo* ci = nullptr; IUnknown* self = nullptr;
    NxObj* dev = nullptr;        // the device this object belongs to (GetDevice); null for the device and IDirect3D9
    NxObj* owner = nullptr;      // holds one PUBLIC reference of `owner` while this object's public count > 0 (device children -> device, device -> IDirect3D9)
    NxObj* container = nullptr;  // GetContainer / refcount forwarding target
    // ---- counts
    uint32_t pub = 0;            // public refcount (what AddRef/Release return)
    uint32_t binds = 0;          // internal bind references (stages, streams, targets, state blocks): keep alive, invisible to the app
    uint32_t priority = 0;
    uint8_t fwd = 0;             // AddRef/Release forward to `container` (texture level surfaces, volume levels, cube faces)
    uint8_t owned = 0;           // lifetime belongs to owner/container (implicit back buffer, auto depth, implicit swap chain): never destroyed by counts
    uint8_t pool = 0xFF;         // D3DPOOL of a resource (0xFF = not a pool resource); MANAGED: SetPriority takes effect
    NxPriv* priv = nullptr;
    NxObj* rnext = nullptr; NxObj* rprev = nullptr; NxObj* rhead = nullptr; uint8_t rlinked = 0;   // device resource registry: a device's rhead lists its live Create* resources (pool != 0xFF); Reset / eviction / memory accounting walk it
    // every object is allocated behind its guest face (face.h): NX_FACE_SIZE bytes in front of the complete object
    // Objects are made by nx_make<T>() (below), never by a `new` expression: operator new is deprecated, so every `new T` of a layer class is a compiler diagnostic (the gates build with -Werror=deprecated-declarations).
    // It stays defined (noexcept, nullptr on exhaustion) for code that has not moved yet.
    void* operator new(size_t n) noexcept NX_MUST_USE NX_USE_MAKE { return nx_alloc_face(n); }
    void operator delete(void* p) { nx_free_face(p); }
    ~NxObj();                    // frees private data (Releases D3DSPD_IUNKNOWN entries)
    // ---- construction (the creating code calls exactly one of these after `new`)
    void asRoot(uint32_t refs = 1);                                                  // IDirect3D9: public count `refs`
    void asDevice(NxObj* d3d);                                                       // device: public 1, holds a reference on d3d
    void asChild(NxObj* device, uint32_t resourcePool /* D3DPOOL or ~0u */);         // Create*: public 1, holds a reference on device
    void asImplicit(NxObj* device, NxObj* cont);                                     // back buffer / auto depth / implicit chain: owned, public 0, container only for GetContainer
    void asSubResource(NxObj* device, NxObj* cont);                                  // level / face: refcount IS the container's
    void asDeferred(NxObj* device);                                                  // recording state block: public 0, one bind reference held by the creator
    ULONG publish();                                                                 // hand a deferred object to the application: drops that bind reference, public 0 -> 1 (takes the device reference)
    bool is(const GUID& iid) const;                                                  // iid is in this class's IID lattice
    bool managed() const { return pool == (uint8_t)D3DPOOL_MANAGED; }
    // ---- IUnknown (generated forwarders call these)
    HRESULT qi(REFIID riid, void** pp); ULONG addref(); ULONG release();
    // ---- bind references: object pointers in device state / state blocks
    void bindRef();              // sub-resources forward to the container
    void unbindRef();            // last reference of any kind while public == 0 destroys
    // ---- common D3D9 methods
    HRESULT getDevice(IDirect3DDevice9** pp); HRESULT getContainer(REFIID riid, void** pp); HRESULT getDirect3D(IDirect3D9** pp);
    HRESULT setPrivateData(REFGUID g, const void* data, DWORD size, DWORD flags); HRESULT getPrivateData(REFGUID g, void* data, DWORD* size);
    HRESULT freePrivateData(REFGUID g);
    DWORD setPriority(DWORD p); DWORD getPriority(); void preLoad(); D3DRESOURCETYPE getType();
private:
    void destroyNow();
};
// THE allocation of a layer object: constructed in memory from the layer's allocator, nullptr when there is none (no constructor ran). nx_make: an NxObj-derived object, behind its guest face (face.h);
// nx_make_plain: any other class of the layer (image records, caches, lifecycle states). Both are warn_unused_result and the analyzer follows the null; `new T` of these classes is deprecated (NX_USE_MAKE).
template <class T> NX_MUST_USE inline T* nx_make() { void* p = nx_alloc_face(sizeof(T)); return p ? ::new (p) T() : nullptr; }
template <class T> NX_MUST_USE inline T* nx_make_plain() { void* p = nxd3d_alloc(sizeof(T)); return p ? ::new (p) T() : nullptr; }
// An object that was made by nx_make and never handed out (no counts, no device reference): destroyed through its class (the generated destroy), exactly as its last release would.
inline void nx_destroy(NxObj* o) { o->ci->destroy(o); }
// The one way to drop an NX_MUST_USE result on purpose (GCC ignores a (void) cast for warn_unused_result): every use states, at the call, why the result cannot be acted on.
template <class T> inline void nx_ignore(const T&) {}
// null-safe: NxObj of one of our objects, or null for null / foreign pointers
NxObj* nx_obj_of(IUnknown* p);
constexpr inline void nx_obj_init(NxObj* o, IUnknown* self, const NxClassInfo* ci) { o->self = self; o->ci = ci; }
// Backend hook for IDirect3DResource9::PreLoad of a managed resource (upload to the GPU). Defined by the resource module; undefined = link error.
void nxd3d_preload(NxObj* resource);
