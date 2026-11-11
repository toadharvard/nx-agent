// NxObj: the COM object model of our D3D9 classes (rules and sources: README.md "Object model").
#pragma once
#if !defined(_WIN32) && !defined(WINAPI)
#define WINAPI  /* shim: one calling convention (as gen/d9wrap_port.h) */
#endif
#include <d3d9.h>
#include <stddef.h>
#include <stdint.h>
#ifndef E_POINTER
#define E_POINTER ((HRESULT)0x80004003L)
#endif
void* nxd3d_alloc(size_t n);  // the only allocator the layer uses (obj.cpp: malloc/free); replaceable per platform
void nxd3d_free(void* p);
enum { NX_FACE_SIZE = 8 };  // face.h

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
    // every object is allocated behind its guest face (face.h): NX_FACE_SIZE bytes in front of the complete object
    void* operator new(size_t n) noexcept { uint8_t* r = (uint8_t*)nxd3d_alloc(n + NX_FACE_SIZE); if (!r) return nullptr; for (int i = 0; i < NX_FACE_SIZE; i++) r[i] = 0; return r + NX_FACE_SIZE; }
    void operator delete(void* p) { if (p) nxd3d_free((uint8_t*)p - NX_FACE_SIZE); }
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
// null-safe: NxObj of one of our objects, or null for null / foreign pointers
NxObj* nx_obj_of(IUnknown* p);
inline void nx_obj_init(NxObj* o, IUnknown* self, const NxClassInfo* ci) { o->self = self; o->ci = ci; }
// Backend hook for IDirect3DResource9::PreLoad of a managed resource (upload to the GPU). Defined by the resource module; undefined = link error.
void nxd3d_preload(NxObj* resource);
