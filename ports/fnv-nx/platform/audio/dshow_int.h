/* dshow_int.h - DirectShow declarations shared by dshow.cpp (the filter graph, its interfaces, the filter mapper)
 * and dshow_filters.cpp (pins, the built-in filters, the renderer's engine) and by tests/s7/test_dshow_contract.cpp.
 *
 * Vtable orders are strmif.h / control.h / axextend.h / strmif.h's IFilterMapper. HRESULT is int32_t, LONG is long
 * and ULONG/DWORD are uint32_t (the game's ILP32; the host tests use the same declarations). GUID arguments passed
 * by value (IFilterMapper) are `Guid` by value, as the SDK declares them. Everything above DSH_IMPL is the public
 * (COM) surface; DSH_IMPL adds the classes the two files share. */
#ifndef FNVNX_DSHOW_INT_H
#define FNVNX_DSHOW_INT_H

#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include <fnvnx/declared.h>   /* fnvnx_wine_fixme(id): one log line, the body does what wine-9.0 does (verify/wine_fixme.tsv row `id`) */

namespace dsh {

typedef int32_t HR;
typedef double REFTIME;
typedef int64_t LL;
typedef uint16_t WCH;

struct Guid { uint32_t a; uint16_t b, c; uint8_t d[8]; };
inline bool geq(const void *x, const Guid &g) { return x && !memcmp(x, &g, sizeof g); }
inline bool geq(const Guid &x, const Guid &g) { return !memcmp(&x, &g, sizeof g); }
#define DSH_G(n, a, b, c, ...) inline const Guid n = {a, b, c, {__VA_ARGS__}}
#define DSH_L(n, a) DSH_G(n, a, 0x0ad4, 0x11ce, 0xb0, 0x3a, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70)   /* quartz interfaces */
#define DSH_E(n, a) DSH_G(n, a, 0x524f, 0x11ce, 0x9f, 0x53, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70)   /* quartz media types */
#define DSH_M(n, a) DSH_G(n, a, 0x0000, 0x0010, 0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71)   /* FOURCC-derived */
#define DSH_T(n, a) DSH_G(n, a, 0x8c82, 0x11cf, 0xbc, 0x0c, 0x00, 0xaa, 0x00, 0xac, 0x74, 0xf6)   /* time formats */
DSH_G(GUID_NULL_, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
DSH_G(IID_IUnknown_, 0x00000000, 0x0000, 0x0000, 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46);
DSH_G(IID_IDispatch_, 0x00020400, 0x0000, 0x0000, 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46);
DSH_G(IID_IPersist_, 0x0000010c, 0x0000, 0x0000, 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46);
DSH_G(IID_IObjectWithSite_, 0xFC4801A3, 0x2BA9, 0x11CF, 0xA2, 0x29, 0x00, 0xAA, 0x00, 0x3D, 0x73, 0x52);
DSH_L(IID_IPin_, 0x56a86891);
DSH_L(IID_IEnumPins_, 0x56a86892);
DSH_L(IID_IEnumFilters_, 0x56a86893);
DSH_L(IID_IBaseFilter_, 0x56a86895);
DSH_L(IID_IReferenceClock_, 0x56a86897);
DSH_L(IID_IMediaFilter_, 0x56a86899);
DSH_L(IID_IMediaSample_, 0x56a8689a);
DSH_L(IID_IMemAllocator_, 0x56a8689c);
DSH_L(IID_IGraphVersion_, 0x56a868ab);
DSH_L(IID_IMediaEventSink_, 0x56a868a2);
DSH_L(IID_IFilterMapper_, 0x56a868a3);
DSH_L(IID_IEnumRegFilters_, 0x56a868a4);
DSH_L(IID_IFileSourceFilter_, 0x56a868a6);
DSH_L(IID_IFilterGraph_, 0x56a8689f);
DSH_L(IID_IGraphBuilder_, 0x56a868a9);
DSH_L(IID_IAsyncReader_, 0x56a868aa);
DSH_L(IID_IMediaControl_, 0x56a868b1);
DSH_L(IID_IMediaPosition_, 0x56a868b2);
DSH_L(IID_IBasicAudio_, 0x56a868b3);
DSH_L(IID_IVideoWindow_, 0x56a868b4);
DSH_L(IID_IBasicVideo_, 0x56a868b5);
DSH_L(IID_IMediaEvent_, 0x56a868b6);
DSH_L(IID_IMediaEventEx_, 0x56a868c0);
DSH_G(IID_IEnumMediaTypes_, 0x89c31040, 0x846b, 0x11ce, 0x97, 0xd3, 0x00, 0xaa, 0x00, 0x55, 0x59, 0x5a);
DSH_G(IID_IMediaSeeking_, 0x36b73880, 0xc2c8, 0x11cf, 0x8b, 0x46, 0x00, 0x80, 0x5f, 0x6c, 0xef, 0x60);
DSH_G(IID_IMediaSample2_, 0x36b73884, 0xc2c8, 0x11cf, 0x8b, 0x46, 0x00, 0x80, 0x5f, 0x6c, 0xef, 0x60);
DSH_G(IID_IFilterGraph2_, 0x36b73882, 0xc2c8, 0x11cf, 0x8b, 0x46, 0x00, 0x80, 0x5f, 0x6c, 0xef, 0x60);
/* classes */
DSH_E(CLSID_FilterGraph_, 0xe436ebb3);
DSH_E(CLSID_FilterMapper_, 0xe436ebb2);
DSH_E(CLSID_AsyncReader_, 0xe436ebb5);
DSH_G(CLSID_MPEG1Splitter_, 0x336475d0, 0x942a, 0x11ce, 0xa8, 0x70, 0x00, 0xaa, 0x00, 0x2f, 0xea, 0xb5);
DSH_G(CLSID_MPEGAudioDecoder_, 0x4a2286e0, 0x7bef, 0x11ce, 0x9b, 0xd9, 0x00, 0x00, 0xe2, 0x02, 0x59, 0x9c);
DSH_G(CLSID_WAVEParser_, 0xd51bd5a1, 0x7548, 0x11cf, 0xa5, 0x20, 0x00, 0x80, 0xc7, 0x7e, 0xf5, 0x8a);
DSH_G(CLSID_DSoundRender_, 0x79376820, 0x07d0, 0x11cf, 0xa2, 0x4d, 0x00, 0x20, 0xaf, 0xd7, 0x97, 0x67);
/* media types */
DSH_E(MEDIATYPE_Stream_, 0xe436eb83);
DSH_M(MEDIATYPE_Audio_, 0x73647561);
DSH_E(MEDIASUBTYPE_MPEG1Audio_, 0xe436eb87);
DSH_E(MEDIASUBTYPE_WAVE_, 0xe436eb8b);
DSH_M(MEDIASUBTYPE_PCM_, 0x00000001);
DSH_M(MEDIASUBTYPE_MP3_, 0x00000055);
DSH_G(FORMAT_WaveFormatEx_, 0x05589f81, 0xc356, 0x11ce, 0xbf, 0x01, 0x00, 0xaa, 0x00, 0x55, 0x59, 0x5a);
DSH_G(FORMAT_None_, 0x0f6417d6, 0xc318, 0x11d0, 0xa4, 0x3f, 0x00, 0xa0, 0xc9, 0x22, 0x31, 0x96);
DSH_T(TIME_FORMAT_SAMPLE_, 0x7b785572);
DSH_T(TIME_FORMAT_MEDIA_TIME_, 0x7b785574);

const HR S_OK_ = 0, S_FALSE_ = 1, E_NOINTERFACE_ = (HR)0x80004002, E_POINTER_ = (HR)0x80004003,
         E_ABORT_ = (HR)0x80004004, E_FAIL_ = (HR)0x80004005, E_UNEXPECTED_ = (HR)0x8000FFFF, E_INVALIDARG_ = (HR)0x80070057,
         E_OUTOFMEMORY_ = (HR)0x8007000E, CLASS_E_NOAGGREGATION_ = (HR)0x80040110,
         VFW_E_INVALID_DIRECTION_ = (HR)0x80040208, VFW_E_NOT_CONNECTED_ = (HR)0x80040209, VFW_E_NO_ACCEPTABLE_TYPES_ = (HR)0x80040207,
         VFW_E_ALREADY_CONNECTED_ = (HR)0x80040204, VFW_E_NOT_FOUND_ = (HR)0x80040216, VFW_E_CANNOT_CONNECT_ = (HR)0x80040217,
         VFW_E_CANNOT_RENDER_ = (HR)0x80040218, VFW_E_NOT_STOPPED_ = (HR)0x80040224, VFW_E_TYPE_NOT_ACCEPTED_ = (HR)0x8004022A,
         VFW_E_DUPLICATE_NAME_ = (HR)0x8004022D, VFW_E_START_TIME_AFTER_END_ = (HR)0x80040228, VFW_E_ENUM_OUT_OF_SYNC_ = (HR)0x80040203,
         VFW_E_UNKNOWN_FILE_TYPE_ = (HR)0x80040240, VFW_E_NOT_IN_GRAPH_ = (HR)0x8004025F,
         VFW_E_UNSUPPORTED_AUDIO_ = (HR)0x8004025C,
         VFW_E_BUFFER_NOTSET_ = (HR)0x8004020C, VFW_E_BUFFER_OVERFLOW_ = (HR)0x8004020D, VFW_E_BADALIGN_ = (HR)0x8004020E,
         VFW_E_ALREADY_COMMITTED_ = (HR)0x8004020F, VFW_E_BUFFERS_OUTSTANDING_ = (HR)0x80040210, VFW_E_NOT_COMMITTED_ = (HR)0x80040211,
         VFW_E_SIZENOTSET_ = (HR)0x80040212, VFW_E_WRONG_STATE_ = (HR)0x80040227, VFW_E_TIMEOUT_ = (HR)0x8004022E,
         VFW_E_SAMPLE_TIME_NOT_SET_ = (HR)0x80040249, VFW_E_MEDIA_TIME_NOT_SET_ = (HR)0x80040251, VFW_S_NO_STOP_TIME_ = (HR)0x00040270,
         HR_ERROR_HANDLE_EOF_ = (HR)0x80070026, HR_ERROR_INVALID_HANDLE_ = (HR)0x80070006, VFW_E_NO_TRANSPORT_ = (HR)0x80040266,
         VFW_S_DUPLICATE_NAME_ = (HR)0x0004022D, VFW_S_STATE_INTERMEDIATE_ = (HR)0x00040237, VFW_S_PARTIAL_RENDER_ = (HR)0x00040242,
         VFW_S_CANT_CUE_ = (HR)0x00040268, VFW_S_NO_MORE_ITEMS_ = (HR)0x00040103,
         DISP_E_UNKNOWNINTERFACE_ = (HR)0x80020001, DISP_E_MEMBERNOTFOUND_ = (HR)0x80020003, DISP_E_TYPEMISMATCH_ = (HR)0x80020005,
         DISP_E_UNKNOWNNAME_ = (HR)0x80020006, DISP_E_EXCEPTION_ = (HR)0x80020009, DISP_E_BADINDEX_ = (HR)0x8002000B,
         DISP_E_BADPARAMCOUNT_ = (HR)0x8002000E, TYPE_E_CANTLOADLIBRARY_ = (HR)0x80029C4A;
enum { EC_COMPLETE_ = 1, EC_USERABORT_ = 2, EC_ERRORABORT_ = 3, EC_PAUSED_ = 0x0E, EC_DEVICE_LOST_ = 0x1F, EC_STATE_CHANGE_ = 0x32 };
enum { State_Stopped = 0, State_Paused = 1, State_Running = 2 };
enum { PINDIR_INPUT = 0, PINDIR_OUTPUT = 1 };
enum { SEEK_CanSeekAbsolute = 1, SEEK_CanSeekForwards = 2, SEEK_CanSeekBackwards = 4, SEEK_CanGetCurrentPos = 8,
       SEEK_CanGetStopPos = 0x10, SEEK_CanGetDuration = 0x20 };
enum { SEEK_NoPositioning = 0, SEEK_Absolute = 1, SEEK_Relative = 2, SEEK_Incremental = 3, SEEK_PosMask = 3,
       SEEK_KeyFrame = 4, SEEK_ReturnTime = 8, SEEK_Segment = 0x10, SEEK_NoFlush = 0x20 };
const uint32_t MERIT_NORMAL_ = 0x600000, MERIT_PREFERRED_ = 0x800000, MERIT_UNLIKELY_ = 0x400000, MERIT_DO_NOT_USE_ = 0x200000;

struct MT {   /* AM_MEDIA_TYPE */
    Guid major, sub;
    uint32_t fixed, temporal, sample_size;
    Guid format;
    void *punk;
    uint32_t cb;
    uint8_t *pb;
};
struct WFX { uint16_t tag, ch; uint32_t rate, avg; uint16_t block, bits, cb; } __attribute__((packed));
struct PinInfo { void *filter; int dir; WCH name[128]; };
struct FilterInfo { WCH name[128]; void *graph; };
struct RegFilter { Guid clsid; WCH *name; };

struct Unk {
    virtual HR QueryInterface(const void *iid, void **out) = 0;
    virtual unsigned long AddRef() = 0;
    virtual unsigned long Release() = 0;
};
struct IPersist_ : Unk { virtual HR GetClassID(Guid *) = 0; };
struct IReferenceClock_ : Unk {
    virtual HR GetTime(LL *) = 0;
    virtual HR AdviseTime(LL base, LL stream, uintptr_t evt, uintptr_t *cookie) = 0;
    virtual HR AdvisePeriodic(LL start, LL period, uintptr_t sem, uintptr_t *cookie) = 0;
    virtual HR Unadvise(uintptr_t cookie) = 0;
};
struct IMediaFilter_ : IPersist_ {
    virtual HR Stop() = 0;
    virtual HR Pause() = 0;
    virtual HR Run(LL start) = 0;
    virtual HR GetState(uint32_t ms, int *state) = 0;
    virtual HR SetSyncSource(IReferenceClock_ *) = 0;
    virtual HR GetSyncSource(IReferenceClock_ **) = 0;
};
struct IFilterGraph_;
struct IPin_;
struct IBaseFilter_ : IMediaFilter_ {
    virtual HR EnumPins(void **) = 0;
    virtual HR FindPin(const WCH *id, IPin_ **) = 0;
    virtual HR QueryFilterInfo(FilterInfo *) = 0;
    virtual HR JoinFilterGraph(IFilterGraph_ *, const WCH *name) = 0;
    virtual HR QueryVendorInfo(WCH **) = 0;
};
struct IPin_ : Unk {
    virtual HR Connect(IPin_ *receive, const MT *) = 0;
    virtual HR ReceiveConnection(IPin_ *connector, const MT *) = 0;
    virtual HR Disconnect() = 0;
    virtual HR ConnectedTo(IPin_ **) = 0;
    virtual HR ConnectionMediaType(MT *) = 0;
    virtual HR QueryPinInfo(PinInfo *) = 0;
    virtual HR QueryDirection(int *) = 0;
    virtual HR QueryId(WCH **) = 0;
    virtual HR QueryAccept(const MT *) = 0;
    virtual HR EnumMediaTypes(void **) = 0;
    virtual HR QueryInternalConnections(IPin_ **, uint32_t *) = 0;
    virtual HR EndOfStream() = 0;
    virtual HR BeginFlush() = 0;
    virtual HR EndFlush() = 0;
    virtual HR NewSegment(LL start, LL stop, double rate) = 0;
};
struct IEnum_ : Unk {   /* IEnumPins / IEnumFilters / IEnumMediaTypes / IEnumRegFilters: Next fills T* */
    virtual HR Next(uint32_t n, void **out, uint32_t *fetched) = 0;
    virtual HR Skip(uint32_t n) = 0;
    virtual HR Reset() = 0;
    virtual HR Clone(void **) = 0;
};
struct IFileSource_ : Unk {
    virtual HR Load(const WCH *file, const MT *) = 0;
    virtual HR GetCurFile(WCH **file, MT *) = 0;
};
struct AsyncReader : Unk {   /* IAsyncReader */
    virtual HR RequestAllocator(void *, void *, void **) = 0;
    virtual HR Request(void *, uintptr_t) = 0;
    virtual HR WaitForNext(uint32_t, void **, uintptr_t *) = 0;
    virtual HR SyncReadAligned(void *) = 0;
    virtual HR SyncRead(LL pos, long len, uint8_t *buf) = 0;
    virtual HR Length(LL *total, LL *available) = 0;
    virtual HR BeginFlush() = 0;
    virtual HR EndFlush() = 0;
};
struct AllocProps { int32_t cBuffers, cbBuffer, cbAlign, cbPrefix; };   /* ALLOCATOR_PROPERTIES */
struct IMediaSample_ : Unk {   /* IMediaSample2: strmif.h / axcore.idl order */
    virtual HR GetPointer(uint8_t **) = 0;
    virtual int32_t GetSize() = 0;
    virtual HR GetTime(LL *, LL *) = 0;
    virtual HR SetTime(LL *, LL *) = 0;
    virtual HR IsSyncPoint() = 0;
    virtual HR SetSyncPoint(int) = 0;
    virtual HR IsPreroll() = 0;
    virtual HR SetPreroll(int) = 0;
    virtual int32_t GetActualDataLength() = 0;
    virtual HR SetActualDataLength(int32_t) = 0;
    virtual HR GetMediaType(struct MT **) = 0;
    virtual HR SetMediaType(struct MT *) = 0;
    virtual HR IsDiscontinuity() = 0;
    virtual HR SetDiscontinuity(int) = 0;
    virtual HR GetMediaTime(LL *, LL *) = 0;
    virtual HR SetMediaTime(LL *, LL *) = 0;
    virtual HR GetProperties(uint32_t, uint8_t *) = 0;
    virtual HR SetProperties(uint32_t, const uint8_t *) = 0;
};
struct IMemAllocator_ : Unk {
    virtual HR SetProperties(AllocProps *request, AllocProps *actual) = 0;
    virtual HR GetProperties(AllocProps *) = 0;
    virtual HR Commit() = 0;
    virtual HR Decommit() = 0;
    virtual HR GetBuffer(IMediaSample_ **, LL *start, LL *end, uint32_t flags) = 0;
    virtual HR ReleaseBuffer(IMediaSample_ *) = 0;
};
IMemAllocator_ *make_mem_allocator();   /* the platform's CLSID_MemoryAllocator object (quartz memallocator), refcount 1; null: out of memory */
struct IFilterGraph_ : Unk {
    virtual HR AddFilter(IBaseFilter_ *filter, const WCH *name) = 0;
    virtual HR RemoveFilter(IBaseFilter_ *filter) = 0;
    virtual HR EnumFilters(void **) = 0;
    virtual HR FindFilterByName(const WCH *, IBaseFilter_ **) = 0;
    virtual HR ConnectDirect(IPin_ *, IPin_ *, const MT *) = 0;
    virtual HR Reconnect(IPin_ *) = 0;
    virtual HR Disconnect(IPin_ *) = 0;
    virtual HR SetDefaultSyncSource() = 0;
};
struct IGraphBuilder_ : IFilterGraph_ {
    virtual HR Connect(IPin_ *, IPin_ *) = 0;
    virtual HR Render(IPin_ *pin) = 0;
    virtual HR RenderFile(const WCH *, const WCH *) = 0;
    virtual HR AddSourceFilter(const WCH *, const WCH *, IBaseFilter_ **) = 0;
    virtual HR SetLogFile(uintptr_t) = 0;
    virtual HR Abort() = 0;
    virtual HR ShouldOperationContinue() = 0;
};
struct IFilterGraph2_ : IGraphBuilder_ {
    virtual HR AddSourceFilterForMoniker(void *moniker, void *bindctx, const WCH *name, IBaseFilter_ **) = 0;
    virtual HR ReconnectEx(IPin_ *, const MT *) = 0;
    virtual HR RenderEx(IPin_ *, uint32_t flags, uint32_t *context) = 0;
};
struct IDispatch_ : Unk {
    virtual HR GetTypeInfoCount(unsigned *) = 0;
    virtual HR GetTypeInfo(unsigned, uint32_t, void **) = 0;
    virtual HR GetIDsOfNames(const void *, WCH **, unsigned, uint32_t, long *) = 0;
    virtual HR Invoke(long, const void *, uint32_t, uint16_t, void *, void *, void *, unsigned *) = 0;
};
struct IMediaControl_ : IDispatch_ {
    virtual HR Run() = 0;
    virtual HR Pause() = 0;
    virtual HR Stop() = 0;
    virtual HR GetState(long ms, long *state) = 0;
    virtual HR RenderFile(const WCH *bstr) = 0;
    virtual HR AddSourceFilter(const WCH *bstr, void **) = 0;
    virtual HR get_FilterCollection(void **) = 0;
    virtual HR get_RegFilterCollection(void **) = 0;
    virtual HR StopWhenReady() = 0;
};
struct IMediaEvent_ : IDispatch_ {
    virtual HR GetEventHandle(uintptr_t *h) = 0;
    virtual HR GetEvent(long *code, intptr_t *p1, intptr_t *p2, long ms) = 0;
    virtual HR WaitForCompletion(long ms, long *code) = 0;
    virtual HR CancelDefaultHandling(long) = 0;
    virtual HR RestoreDefaultHandling(long) = 0;
    virtual HR FreeEventParams(long, intptr_t, intptr_t) = 0;
};
struct IMediaEventEx_ : IMediaEvent_ {
    virtual HR SetNotifyWindow(uintptr_t hwnd, long msg, intptr_t instance) = 0;
    virtual HR SetNotifyFlags(long) = 0;
    virtual HR GetNotifyFlags(long *) = 0;
};
struct IMediaEventSink_ : Unk { virtual HR Notify(long code, intptr_t p1, intptr_t p2) = 0; };
struct IMediaPosition_ : IDispatch_ {
    virtual HR get_Duration(REFTIME *) = 0;
    virtual HR put_CurrentPosition(REFTIME) = 0;
    virtual HR get_CurrentPosition(REFTIME *) = 0;
    virtual HR get_StopTime(REFTIME *) = 0;
    virtual HR put_StopTime(REFTIME) = 0;
    virtual HR get_PrerollTime(REFTIME *) = 0;
    virtual HR put_PrerollTime(REFTIME) = 0;
    virtual HR put_Rate(double) = 0;
    virtual HR get_Rate(double *) = 0;
    virtual HR CanSeekForward(long *) = 0;
    virtual HR CanSeekBackward(long *) = 0;
};
struct IMediaSeeking_ : Unk {
    virtual HR GetCapabilities(uint32_t *) = 0;
    virtual HR CheckCapabilities(uint32_t *) = 0;
    virtual HR IsFormatSupported(const Guid *) = 0;
    virtual HR QueryPreferredFormat(Guid *) = 0;
    virtual HR GetTimeFormat(Guid *) = 0;
    virtual HR IsUsingTimeFormat(const Guid *) = 0;
    virtual HR SetTimeFormat(const Guid *) = 0;
    virtual HR GetDuration(LL *) = 0;
    virtual HR GetStopPosition(LL *) = 0;
    virtual HR GetCurrentPosition(LL *) = 0;
    virtual HR ConvertTimeFormat(LL *target, const Guid *tfmt, LL source, const Guid *sfmt) = 0;
    virtual HR SetPositions(LL *cur, uint32_t curflags, LL *stop, uint32_t stopflags) = 0;
    virtual HR GetPositions(LL *cur, LL *stop) = 0;
    virtual HR GetAvailable(LL *early, LL *late) = 0;
    virtual HR SetRate(double) = 0;
    virtual HR GetRate(double *) = 0;
    virtual HR GetPreroll(LL *) = 0;
};
struct IBasicAudio_ : IDispatch_ {
    virtual HR put_Volume(long) = 0;
    virtual HR get_Volume(long *) = 0;
    virtual HR put_Balance(long) = 0;
    virtual HR get_Balance(long *) = 0;
};
/* IVideoWindow (39 methods after IDispatch) and IBasicVideo (32), control.h order; OAHWND is a pointer-sized integer. */
struct IVideoWindow_ : IDispatch_ {
    virtual HR put_Caption(WCH *) = 0;
    virtual HR get_Caption(WCH **) = 0;
    virtual HR put_WindowStyle(long) = 0;
    virtual HR get_WindowStyle(long *) = 0;
    virtual HR put_WindowStyleEx(long) = 0;
    virtual HR get_WindowStyleEx(long *) = 0;
    virtual HR put_AutoShow(long) = 0;
    virtual HR get_AutoShow(long *) = 0;
    virtual HR put_WindowState(long) = 0;
    virtual HR get_WindowState(long *) = 0;
    virtual HR put_BackgroundPalette(long) = 0;
    virtual HR get_BackgroundPalette(long *) = 0;
    virtual HR put_Visible(long) = 0;
    virtual HR get_Visible(long *) = 0;
    virtual HR put_Left(long) = 0;
    virtual HR get_Left(long *) = 0;
    virtual HR put_Width(long) = 0;
    virtual HR get_Width(long *) = 0;
    virtual HR put_Top(long) = 0;
    virtual HR get_Top(long *) = 0;
    virtual HR put_Height(long) = 0;
    virtual HR get_Height(long *) = 0;
    virtual HR put_Owner(intptr_t) = 0;
    virtual HR get_Owner(intptr_t *) = 0;
    virtual HR put_MessageDrain(intptr_t) = 0;
    virtual HR get_MessageDrain(intptr_t *) = 0;
    virtual HR get_BorderColor(long *) = 0;
    virtual HR put_BorderColor(long) = 0;
    virtual HR get_FullScreenMode(long *) = 0;
    virtual HR put_FullScreenMode(long) = 0;
    virtual HR SetWindowForeground(long) = 0;
    virtual HR NotifyOwnerMessage(intptr_t hwnd, long msg, intptr_t wparam, intptr_t lparam) = 0;
    virtual HR SetWindowPosition(long l, long t, long w, long h) = 0;
    virtual HR GetWindowPosition(long *l, long *t, long *w, long *h) = 0;
    virtual HR GetMinIdealImageSize(long *w, long *h) = 0;
    virtual HR GetMaxIdealImageSize(long *w, long *h) = 0;
    virtual HR GetRestorePosition(long *l, long *t, long *w, long *h) = 0;
    virtual HR HideCursor(long) = 0;
    virtual HR IsCursorHidden(long *) = 0;
};
struct IBasicVideo_ : IDispatch_ {
    virtual HR get_AvgTimePerFrame(REFTIME *) = 0;
    virtual HR get_BitRate(long *) = 0;
    virtual HR get_BitErrorRate(long *) = 0;
    virtual HR get_VideoWidth(long *) = 0;
    virtual HR get_VideoHeight(long *) = 0;
    virtual HR put_SourceLeft(long) = 0;
    virtual HR get_SourceLeft(long *) = 0;
    virtual HR put_SourceWidth(long) = 0;
    virtual HR get_SourceWidth(long *) = 0;
    virtual HR put_SourceTop(long) = 0;
    virtual HR get_SourceTop(long *) = 0;
    virtual HR put_SourceHeight(long) = 0;
    virtual HR get_SourceHeight(long *) = 0;
    virtual HR put_DestinationLeft(long) = 0;
    virtual HR get_DestinationLeft(long *) = 0;
    virtual HR put_DestinationWidth(long) = 0;
    virtual HR get_DestinationWidth(long *) = 0;
    virtual HR put_DestinationTop(long) = 0;
    virtual HR get_DestinationTop(long *) = 0;
    virtual HR put_DestinationHeight(long) = 0;
    virtual HR get_DestinationHeight(long *) = 0;
    virtual HR SetSourcePosition(long l, long t, long w, long h) = 0;
    virtual HR GetSourcePosition(long *l, long *t, long *w, long *h) = 0;
    virtual HR SetDefaultSourcePosition() = 0;
    virtual HR SetDestinationPosition(long l, long t, long w, long h) = 0;
    virtual HR GetDestinationPosition(long *l, long *t, long *w, long *h) = 0;
    virtual HR SetDefaultDestinationPosition() = 0;
    virtual HR GetVideoSize(long *w, long *h) = 0;
    virtual HR GetVideoPaletteEntries(long start, long count, long *retrieved, long *palette) = 0;
    virtual HR GetCurrentImage(long *size, long *dib) = 0;
    virtual HR IsUsingDefaultSource() = 0;
    virtual HR IsUsingDefaultDestination() = 0;
};
struct IObjectWithSite_ : Unk {
    virtual HR SetSite(Unk *) = 0;
    virtual HR GetSite(const void *iid, void **) = 0;
};
struct IGraphVersion_ : Unk {
    virtual HR QueryVersion(long *version) = 0;
};
struct IFilterMapper_ : Unk {
    virtual HR RegisterFilter(Guid clsid, const WCH *name, uint32_t merit) = 0;
    virtual HR RegisterFilterInstance(Guid clsid, const WCH *name, Guid *mrid) = 0;
    virtual HR RegisterPin(Guid filter, const WCH *name, int rendered, int output, int zero, int many, Guid connects_filter,
                           const WCH *connects_pin) = 0;
    virtual HR RegisterPinType(Guid filter, const WCH *pin, Guid major, Guid sub) = 0;
    virtual HR UnregisterFilter(Guid clsid) = 0;
    virtual HR UnregisterFilterInstance(Guid mrid) = 0;
    virtual HR UnregisterPin(Guid filter, const WCH *name) = 0;
    virtual HR EnumMatchingFilters(void **enumerator, uint32_t merit, int input_needed, Guid in_major, Guid in_sub, int render,
                                   int output_needed, Guid out_major, Guid out_sub) = 0;
};
struct IMoniker_ : Unk {   /* the slots up to BindToObject (IPersist, IPersistStream, IMoniker) */
    virtual HR GetClassID(Guid *) = 0;
    virtual HR IsDirty() = 0;
    virtual HR Load(void *) = 0;
    virtual HR Save(void *, int) = 0;
    virtual HR GetSizeMax(void *) = 0;
    virtual HR BindToObject(void *bindctx, void *left, const void *iid, void **out) = 0;
};

#ifdef DSH_IMPL
}   /* namespace dsh */
#include "mixer.h"
#include "../os/os.h"
#include "../include/fnvnx_clock.h"
#include "../include/fnvnx_win32_ext.h"
namespace dsh {

/* private IIDs: our own pin / filter behind an IPin / IBaseFilter (no RTTI: the game's toolchain has none) */
DSH_G(IID_DshPin_, 0x6e766e78, 0x7064, 0x7069, 0x6e, 0x70, 0x69, 0x6e, 0x00, 0x00, 0x00, 0x01);
DSH_G(IID_DshFilter_, 0x6e766e78, 0x7064, 0x7069, 0x66, 0x69, 0x6c, 0x74, 0x00, 0x00, 0x00, 0x02);

template <class T> struct Vec {
    T *p = nullptr;
    size_t n = 0, cap = 0;
    ~Vec() { free(p); }
    bool push(const T &v)
    {
        if (n == cap) {
            size_t c = cap ? cap * 2 : 8;
            T *q = (T *)realloc(p, c * sizeof(T));
            if (!q) return false;
            p = q;
            cap = c;
        }
        p[n++] = v;
        return true;
    }
    void erase(size_t i) { memmove(p + i, p + i + 1, (n - i - 1) * sizeof(T)), n--; }
    T &operator[](size_t i) { return p[i]; }
};
size_t wlen(const WCH *s);
bool weq_nocase(const WCH *a, const WCH *b);
void wcopy(WCH *dst, size_t cap, const WCH *src);
void wfrom_ascii(WCH *dst, size_t cap, const char *src);
WCH *wdup_co(const WCH *s);   /* CoTaskMemAlloc'ed copy */
HR mt_copy(MT *dst, const MT *src);   /* pb allocated with CoTaskMemAlloc */
void mt_free(MT *m);
void mt_wfx(MT *m, const Guid &sub, uint16_t tag, uint16_t ch, uint32_t rate, uint16_t bits);

struct Blob {   /* a shared byte buffer: the stream a source pin read, kept by every stage that decodes from it */
    int refs;
    size_t size;
    uint8_t data[1];
};
Blob *blob_new(size_t n);
Blob *blob_ref(Blob *b);
void blob_unref(Blob *b);

enum StreamKind { SK_UNKNOWN, SK_MP3, SK_WAVE };
StreamKind sniff_reader(AsyncReader *rd);   /* what the stream is, from its bytes (the graph's byte-pattern check) */
HR read_all(AsyncReader *rd, Blob **out, volatile int *aborted);   /* the whole stream through Length/SyncRead */

enum Kind { K_SPLITTER, K_PARSER, K_DECODER, K_RENDERER, K_FILESRC };
struct BFilter;

/* what the filters need of their graph; implemented by Graph (dshow.cpp) */
struct GraphCore {
    /* The graph lock guards the graph's own tables (filter list, state, clock, event queue, the renderers' streams) and nothing
     * else: NO call into an object that is not provably the platform's own type is made with it held (such an object may be
     * the game's, a call into the game may not return, and the game may re-enter the graph from inside it). Taken only through
     * GraphLock. */
    os_lock_t lock = {0};
    void lock_enter() { os_lock(&lock); }
    void lock_leave() { os_unlock(&lock); }
    volatile int aborted = 0;
    volatile int version = 0;   /* IGraphVersion: +1 on every AddFilter / RemoveFilter / pin connect / pin disconnect */
    void bump_version() { __atomic_add_fetch(&version, 1, __ATOMIC_ACQ_REL); }
    virtual bool event_locked(long code, intptr_t p1, intptr_t p2) = 0;   /* queue an event; true if a window post is due */
    virtual void completion_locked(BFilter *renderer) = 0;                /* a renderer played its last frame */
    virtual void start_pump() = 0;
    virtual void flush_window() = 0;                                      /* post what event_locked reported, lock not held */
};
GraphCore *core_of(IFilterGraph_ *g);
struct GraphLock {   /* the graph lock for a scope; a null graph (a filter outside any graph) takes nothing */
    GraphCore *g;
    explicit GraphLock(GraphCore *c) : g(c) { if (g) g->lock_enter(); }
    ~GraphLock() { if (g) g->lock_leave(); }
    GraphLock(const GraphLock &) = delete;
    GraphLock &operator=(const GraphLock &) = delete;
};

struct BPin : IPin_, AsyncReader {   /* IAsyncReader is served only by a file source's output pin */
    BFilter *owner;
    int dir;
    WCH name[32];
    Vec<MT> types;          /* accepted (input) / offered (output) types; pb is owned */
    IPin_ *peer = nullptr;  /* the connected pin, AddRef'd */
    MT cur = {};            /* the connection type (valid while peer) */
    LL seg_start = 0, seg_stop = 0;
    double seg_rate = 1.0;
    uintptr_t file = 0;     /* serves: the layer's file handle and size */
    LL fsize = 0;
    bool serves = false;
    /* serves: the overlapped half of IAsyncReader (filesource.c): requests are read when made and handed back by WaitForNext */
    struct Req { void *sample; uintptr_t cookie; LL pos; uint32_t size; };
    Vec<Req> reqs;
    bool flushing = false;
    os_lock_t rlock = {0};
    uintptr_t flush_evt = 0;   /* manual-reset event, set while flushing (wakes WaitForNext) */
    bool rd_at(LL pos, long len, uint8_t *buf, uint32_t *got, uint32_t *err);   /* a read at an offset: false + Win32 error */
    BPin(BFilter *o, int d, const char *n);
    virtual ~BPin();
    HR QueryInterface(const void *iid, void **out) override;
    unsigned long AddRef() override;
    unsigned long Release() override;
    HR Connect(IPin_ *, const MT *) override;
    HR ReceiveConnection(IPin_ *, const MT *) override;
    HR Disconnect() override;
    HR ConnectedTo(IPin_ **) override;
    HR ConnectionMediaType(MT *) override;
    HR QueryPinInfo(PinInfo *) override;
    HR QueryDirection(int *) override;
    HR QueryId(WCH **) override;
    HR QueryAccept(const MT *) override;
    HR EnumMediaTypes(void **) override;
    HR QueryInternalConnections(IPin_ **, uint32_t *) override;
    HR EndOfStream() override;
    HR BeginFlush() override;
    HR EndFlush() override;
    HR NewSegment(LL, LL, double) override;
    HR RequestAllocator(void *, void *, void **) override;
    HR Request(void *, uintptr_t) override;
    HR WaitForNext(uint32_t, void **, uintptr_t *) override;
    HR SyncReadAligned(void *) override;
    HR SyncRead(LL pos, long len, uint8_t *buf) override;
    HR Length(LL *total, LL *available) override;
    void add_type(const Guid &major, const Guid &sub);
    void set_type(const MT &m);   /* an output pin's one offered type, replacing the list */
};
BPin *internal_pin(IPin_ *p);   /* our own pin behind an IPin, else null */
BFilter *internal_filter(Unk *f);

struct BFilter : IBaseFilter_ {
    int refs = 1;
    Kind kind;
    GraphCore *graph = nullptr;
    IFilterGraph_ *graph_if = nullptr;
    WCH name[128] = {};
    int fstate = State_Stopped;
    Vec<BPin *> pins;
    IReferenceClock_ *sync = nullptr;
    const Guid *clsid;
    BFilter(Kind k, const Guid *c) : kind(k), clsid(c) {}
    virtual ~BFilter();
    BPin *add_pin(int dir, const char *name);
    HR QueryInterface(const void *iid, void **out) override;
    unsigned long AddRef() override { return __atomic_add_fetch(&refs, 1, __ATOMIC_ACQ_REL); }
    unsigned long Release() override;
    HR GetClassID(Guid *g) override { if (!g) return E_POINTER_; *g = *clsid; return S_OK_; }
    HR Stop() override { set_state(State_Stopped); return S_OK_; }
    HR Pause() override { set_state(State_Paused); return S_OK_; }
    HR Run(LL) override { set_state(State_Running); return S_OK_; }
    HR GetState(uint32_t ms, int *st) override;
    HR SetSyncSource(IReferenceClock_ *c) override;
    HR GetSyncSource(IReferenceClock_ **c) override;
    HR EnumPins(void **) override;
    HR FindPin(const WCH *, IPin_ **) override;
    HR QueryFilterInfo(FilterInfo *) override;
    HR JoinFilterGraph(IFilterGraph_ *, const WCH *) override;
    HR QueryVendorInfo(WCH **) override;   /* env_const.tsv: MSDN says E_NOTIMPL for a filter without vendor info */
    virtual bool accepts(BPin *p, const MT *mt);
    virtual void set_state(int st) { fstate = st; }
};

/* a filter with an input pin (every kind but the file source): its input connects and disconnects */
struct Stage : BFilter {
    using BFilter::BFilter;
    virtual HR input_connected(BPin *in, IPin_ *peer, const MT *mt) = 0;
    virtual void input_disconnected(BPin *in) = 0;
};
Stage *as_stage(BFilter *f);   /* null for the file source */

/* the pull path: stereo int16 frames out of an output pin (decoder, WAVE parser) */
struct PcmSource {
    virtual bool pcm_info(BPin *out, uint32_t *rate, uint64_t *frames) = 0;
    virtual bool pcm_seek(BPin *out, uint64_t frame) = 0;
    virtual size_t pcm_read(BPin *out, int16_t *dst, size_t frames) = 0;
};
PcmSource *as_pcm(BFilter *f);   /* null for a filter that offers no PCM */
/* the byte stream out of a splitter's output pin */
struct ByteSource {
    virtual Blob *bytes(BPin *out) = 0;
};
ByteSource *as_bytes(BFilter *f);   /* null for a filter that offers no byte stream */

/* a snapshot enumerator: pins and filters (AddRef'd Unk), media types (MT copies), registry filters (RegFilter copies) */
enum EnumKind { EK_UNK, EK_MT, EK_REG };
IEnum_ *make_enum(EnumKind k, void **items, size_t n);

BFilter *make_filter(const Guid &clsid);   /* an installed filter by class, null if not one */
HR make_file_source(const WCH *file, BFilter **out);   /* HRESULT of the failure: the file's error, VFW_E_UNKNOWN_FILE_TYPE */
IPin_ *make_reader_pin(AsyncReader *rd);
void reader_pin_orphan(IPin_ *p);          /* the borrowed reader is no longer valid */   /* a pin over a source that only exposes IAsyncReader (borrowed reader) */

struct PinDesc { const char *name; bool output, rendered; Guid type[2]; };
struct ClassDesc { const Guid *clsid; const char *name; uint32_t merit; Kind kind; PinDesc pins[2]; };
extern const ClassDesc g_classes[4];

/* the renderer's per-stream engine (graph lock held by the caller) */
struct Renderer : Stage, IReferenceClock_ {
    Renderer() : Stage(K_RENDERER, &CLSID_DSoundRender_) {}
    HR QueryInterface(const void *iid, void **out) override { return BFilter::QueryInterface(iid, out); }   /* one identity for both bases */
    unsigned long AddRef() override { return BFilter::AddRef(); }
    unsigned long Release() override { return BFilter::Release(); }
    virtual void tick_locked() = 0;   /* the pump, graph lock held */
    virtual bool connected() = 0;
    virtual uint64_t total_frames() = 0;
    virtual uint32_t src_rate() = 0;
    virtual uint64_t position_frames() = 0;
    virtual bool seek_frames(uint64_t frame, uint64_t stop) = 0;
    virtual HR set_play_rate(double r) = 0;
    virtual bool is_complete() = 0;
    virtual HR set_volume(long v) = 0;
    virtual HR set_balance(long b) = 0;
    virtual long get_volume() = 0;
    virtual long get_balance() = 0;
    virtual void rearm() = 0;   /* a new run starts: the completion latch of this stream is clear */
    virtual HR stream_event(int ev, double rate) = 0;   /* its pin's EndOfStream (0), BeginFlush (1), EndFlush (2), NewSegment (3) */
};
Renderer *as_renderer(BFilter *f);

#endif   /* DSH_IMPL */

}   // namespace dsh
#endif
