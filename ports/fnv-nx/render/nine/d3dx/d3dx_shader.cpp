// fnv-nx D3DX9 (stream S6c-nine, guest ILP32): shader bytecode helpers and the constant table.
//
// The retail game hands precompiled D3D9 bytecode (shaderpackage*.sdp) to CreateVertexShader/CreatePixelShader and
// reads the constant table (the CTAB comment block the compiler stores in the bytecode) to bind constants by name:
// D3DXGetShaderConstantTable(Ex) parses it into an ID3DXConstantTable (getters, and setters that write through
// IDirect3DDevice9::Set{Vertex,Pixel}ShaderConstant{F,I,B}). Runtime compilation (D3DXCompileShader*,
// D3DXAssembleShader*) is the developer path (docs/RENDER.md §1): it fails with an error buffer that says so.
#include <new>
#include <stdio.h>
#include "d3dx_internal.h"

namespace {

const DWORD kEnd = 0x0000FFFF;
inline bool is_shader_version(DWORD v) { return (v & 0xFFFE0000u) == 0xFFFE0000u; }   // vs 0xFFFE / ps 0xFFFF

// Tokens of the instruction at p (0 at the end token). Comments carry their length; SM2+ instructions too
// (bits 24..27); SM1 instructions are followed by parameter tokens (bit 31 set), def by its 4 raw constants.
unsigned insn_tokens(const DWORD* p, unsigned major) {
    DWORD t = *p;
    if (t == kEnd) return 0;
    if ((t & 0xFFFF) == 0xFFFE) return 1 + ((t >> 16) & 0x7FFF);
    if (major >= 2) return 1 + ((t >> 24) & 0xF);
    if ((t & 0xFFFF) == 0x51) return 6;   // def c#, x, y, z, w
    unsigned n = 1;
    while (p[n] & 0x80000000u) n++;
    return n;
}

const unsigned kMaxTokens = 1u << 20;

// ---- CTAB (D3DXSHADER_CONSTANTTABLE and friends; offsets are from the start of the table) ----
struct CtabHeader { uint32_t size, creator, version, constants, constant_info, flags, target; };
struct CtabConstant { uint32_t name; uint16_t set, reg_index, reg_count, reserved; uint32_t type_info, default_value; };
struct CtabType { uint16_t cls, type, rows, columns, elements, members; uint32_t member_info; };
struct CtabMember { uint32_t name, type_info; };
static_assert(sizeof(CtabHeader) == 28 && sizeof(CtabConstant) == 20 && sizeof(CtabType) == 16 && sizeof(CtabMember) == 8,
              "CTAB layout");

struct Node {
    D3DXCONSTANT_DESC desc;
    Node* sub;          // elements (desc.Elements > 1) or struct members
    unsigned nsub;
    bool elements;
};

// how values handed to a setter are laid out
enum Kind { SCALARS, VECTORS, MATRICES, MATRIX_POINTERS };
struct Input {
    Kind kind;
    int type;           // D3DXPT_FLOAT / INT / BOOL, or -1: the parameter's own type (SetValue)
    const void* p;
    UINT count;         // units: values, vectors or matrices
    bool transpose;
    UINT used;
};

class ConstTable final : public ID3DXConstantTable {
public:
    ULONG refs_ = 1;
    uint8_t* ctab_ = nullptr;
    UINT size_ = 0;
    bool vs_ = false;
    D3DXCONSTANTTABLE_DESC desc_ = {};
    Node* nodes_ = nullptr;   // all nodes: [0, ntop) the top-level constants, then the sub-constants
    unsigned ntop_ = 0, nnodes_ = 0, used_ = 0;

    ~ConstTable() { free(nodes_); free(ctab_); }

    // --- construction ---
    const char* str(uint32_t off) const {
        if (!off || off >= size_) return "";
        return memchr(ctab_ + off, 0, size_ - off) ? (const char*)ctab_ + off : "";
    }
    const CtabType* type_at(uint32_t off) const {
        return off && off + sizeof(CtabType) <= size_ ? (const CtabType*)(ctab_ + off) : nullptr;
    }
    const CtabMember* member_at(const CtabType* t, unsigned i) const {
        uint32_t off = t->member_info + i * sizeof(CtabMember);
        return t->member_info && off + sizeof(CtabMember) <= size_ ? (const CtabMember*)(ctab_ + off) : nullptr;
    }
    unsigned count_nodes(const CtabType* t, int depth) const {
        if (!t || depth > 16) return 1;
        unsigned members = 0;
        for (unsigned i = 0; i < t->members; i++) {
            const CtabMember* m = member_at(t, i);
            members += m ? count_nodes(type_at(m->type_info), depth + 1) : 1;
        }
        return t->elements > 1 ? 1 + t->elements * (1 + members) : 1 + members;
    }
    unsigned regs_one(const CtabType* t, D3DXREGISTER_SET set) const {
        if (!t) return 1;
        if (t->members) {
            unsigned n = 0;
            for (unsigned i = 0; i < t->members; i++) {
                const CtabMember* m = member_at(t, i);
                const CtabType* mt = m ? type_at(m->type_info) : nullptr;
                n += (mt && mt->elements > 1 ? mt->elements : 1) * regs_one(mt, set);
            }
            return n;
        }
        if (set == D3DXRS_BOOL) return t->rows * t->columns;
        if (t->cls == D3DXPC_MATRIX_ROWS) return t->rows;
        if (t->cls == D3DXPC_MATRIX_COLUMNS) return t->columns;
        return 1;
    }
    unsigned bytes_one(const CtabType* t) const {
        if (!t) return 4;
        if (t->members) {
            unsigned n = 0;
            for (unsigned i = 0; i < t->members; i++) {
                const CtabMember* m = member_at(t, i);
                const CtabType* mt = m ? type_at(m->type_info) : nullptr;
                n += (mt && mt->elements > 1 ? mt->elements : 1) * bytes_one(mt);
            }
            return n;
        }
        return 4u * t->rows * t->columns;
    }
    Node* take(unsigned n) {
        if (used_ + n > nnodes_) return nullptr;
        Node* r = nodes_ + used_;
        used_ += n;
        return r;
    }
    // one constant (or, with as_element, one element of an array) at registers [reg, reg + regs)
    void build(Node* n, const char* name, const CtabType* t, D3DXREGISTER_SET set, unsigned reg, unsigned regs,
               const uint8_t* def, bool as_element, int depth) {
        D3DXCONSTANT_DESC& d = n->desc;
        unsigned elements = !t || as_element || t->elements < 1 ? 1 : t->elements;
        d.Name = name;
        d.RegisterSet = set;
        d.RegisterIndex = reg;
        d.RegisterCount = regs;
        d.Class = t ? (D3DXPARAMETER_CLASS)t->cls : D3DXPC_SCALAR;
        d.Type = t ? (D3DXPARAMETER_TYPE)t->type : D3DXPT_FLOAT;
        d.Rows = t ? t->rows : 1;
        d.Columns = t ? t->columns : 1;
        d.Elements = elements;
        d.StructMembers = t ? t->members : 0;
        d.Bytes = elements * bytes_one(t);
        d.DefaultValue = def;
        n->sub = nullptr;
        n->nsub = 0;
        n->elements = false;
        if (!t || depth > 16) return;
        if (elements > 1) {
            n->elements = true;
            n->sub = take(elements);
            if (!n->sub) return;
            n->nsub = elements;
            unsigned er = regs_one(t, set), eb = bytes_one(t);
            for (unsigned i = 0; i < elements; i++) {
                unsigned r = reg + i * er, end = reg + regs;
                build(&n->sub[i], name, t, set, r, r < end ? (end - r < er ? end - r : er) : 0, def ? def + i * eb : nullptr,
                      true, depth + 1);
            }
        } else if (t->members) {
            n->sub = take(t->members);
            if (!n->sub) return;
            n->nsub = t->members;
            unsigned off_r = 0, off_b = 0;
            for (unsigned i = 0; i < t->members; i++) {
                const CtabMember* m = member_at(t, i);
                const CtabType* mt = m ? type_at(m->type_info) : nullptr;
                unsigned mr = (mt && mt->elements > 1 ? mt->elements : 1) * regs_one(mt, set);
                unsigned r = reg + off_r, end = reg + regs;
                build(&n->sub[i], m ? str(m->name) : "", mt, set, r, r < end ? (end - r < mr ? end - r : mr) : 0,
                      def ? def + off_b : nullptr, false, depth + 1);
                off_r += mr;
                off_b += (mt && mt->elements > 1 ? mt->elements : 1) * bytes_one(mt);
            }
        }
    }
    HRESULT init(const void* data, UINT size, DWORD version) {
        if (size < sizeof(CtabHeader)) return D3DXERR_INVALIDDATA;
        ctab_ = (uint8_t*)malloc(size);
        if (!ctab_) return E_OUTOFMEMORY;
        memcpy(ctab_, data, size);
        size_ = size;
        vs_ = (version & 0xFFFF0000u) == 0xFFFE0000u;
        CtabHeader h;
        memcpy(&h, ctab_, sizeof h);
        if (h.size != sizeof h || h.constant_info + (uint64_t)h.constants * sizeof(CtabConstant) > size)
            return D3DXERR_INVALIDDATA;
        desc_.Creator = str(h.creator);
        desc_.Version = h.version;
        desc_.Constants = h.constants;
        const CtabConstant* c = (const CtabConstant*)(ctab_ + h.constant_info);
        unsigned total = h.constants;
        for (unsigned i = 0; i < h.constants; i++) total += count_nodes(type_at(c[i].type_info), 0) - 1;
        nodes_ = (Node*)calloc(total ? total : 1, sizeof(Node));
        if (!nodes_) return E_OUTOFMEMORY;
        nnodes_ = total;
        ntop_ = h.constants;
        used_ = ntop_;
        for (unsigned i = 0; i < ntop_; i++) {
            const uint8_t* def = c[i].default_value && c[i].default_value < size ? ctab_ + c[i].default_value : nullptr;
            build(&nodes_[i], str(c[i].name), type_at(c[i].type_info), (D3DXREGISTER_SET)c[i].set, c[i].reg_index,
                  c[i].reg_count, def, false, 0);
        }
        return D3D_OK;
    }

    // --- handles: a node pointer, or (D3DX allows it) a constant's name ---
    Node* node(D3DXHANDLE h) {
        if (!h) return nullptr;
        const Node* p = (const Node*)(const void*)h;
        if (p >= nodes_ && p < nodes_ + nnodes_ && ((const char*)p - (const char*)nodes_) % sizeof(Node) == 0)
            return (Node*)p;
        return by_name(nullptr, h);
    }
    static D3DXHANDLE handle(const Node* n) { return (D3DXHANDLE)(const void*)n; }
    Node* child(Node* scope, const char* name, size_t len) {
        Node* list = scope ? scope->sub : nodes_;
        unsigned n = scope ? scope->nsub : ntop_;
        if (scope && scope->elements) {   // members of an array of structs: those of element 0
            if (!n || !list[0].nsub) return nullptr;
            scope = &list[0];
            list = scope->sub;
            n = scope->nsub;
        }
        for (unsigned i = 0; i < n; i++)
            if (!strncmp(list[i].desc.Name, name, len) && !list[i].desc.Name[len]) return &list[i];
        return nullptr;
    }
    // "name", "a.b", "a[3]", "a[1].b[2]" relative to scope (NULL: top level)
    Node* by_name(Node* scope, const char* s) {
        if (!s) return nullptr;
        Node* cur = scope;
        bool first = true;
        while (*s) {
            if (*s == '[') {
                char* end = nullptr;
                unsigned long i = strtoul(s + 1, &end, 10);
                if (!cur || !cur->elements || !end || *end != ']' || i >= cur->nsub) return nullptr;
                cur = &cur->sub[i];
                s = end + 1;
            } else {
                if (*s == '.') {
                    if (first) return nullptr;
                    s++;
                } else if (!first) {
                    return nullptr;
                }
                size_t len = strcspn(s, ".[");
                if (!len) return nullptr;
                cur = child(cur, s, len);
                if (!cur) return nullptr;
                s += len;
            }
            first = false;
        }
        return cur;
    }

    // --- setters ---
    float in_float(Input& in, unsigned idx, int ptype) const {
        const uint32_t* w = (const uint32_t*)in.p;
        int t = in.type < 0 ? ptype : in.type;
        uint32_t v = w[idx];
        float f;
        if (t == D3DXPT_FLOAT) memcpy(&f, &v, 4);
        else if (t == D3DXPT_BOOL) f = v ? 1.0f : 0.0f;
        else f = (float)(int32_t)v;
        return f;
    }
    // the value of row r, column c of the current leaf (false: input exhausted)
    bool value(Input& in, const D3DXCONSTANT_DESC& d, unsigned r, unsigned c, float* out) const {
        switch (in.kind) {
        case SCALARS: {
            unsigned idx = in.used + r * d.Columns + c;
            if (idx >= in.count) return false;
            *out = in_float(in, idx, d.Type);
            return true;
        }
        case VECTORS: {
            unsigned vi = in.used + (d.Class == D3DXPC_MATRIX_ROWS || d.Class == D3DXPC_MATRIX_COLUMNS ? r : 0);
            if (vi >= in.count || c >= 4) return false;
            *out = ((const float*)in.p)[vi * 4 + c];
            return true;
        }
        default: {
            if (in.used >= in.count || r >= 4 || c >= 4) return false;
            const float* m = in.kind == MATRICES ? (const float*)in.p + in.used * 16
                                                 : &((const D3DXMATRIX* const*)in.p)[in.used]->_11;
            *out = in.transpose ? m[c * 4 + r] : m[r * 4 + c];
            return true;
        }
        }
    }
    void advance(Input& in, const D3DXCONSTANT_DESC& d) const {
        if (in.kind == SCALARS) in.used += d.Rows * d.Columns;
        else if (in.kind == VECTORS) in.used += (d.Class == D3DXPC_MATRIX_ROWS || d.Class == D3DXPC_MATRIX_COLUMNS) ? d.Rows : 1;
        else in.used += 1;
    }
    HRESULT write(IDirect3DDevice9* dev, const Node* n, Input& in) {
        if (n->nsub) {
            for (unsigned i = 0; i < n->nsub; i++) {
                HRESULT hr = write(dev, &n->sub[i], in);
                if (hr != D3D_OK) return hr;
            }
            return D3D_OK;
        }
        const D3DXCONSTANT_DESC& d = n->desc;
        if (d.RegisterSet == D3DXRS_SAMPLER || d.Class == D3DXPC_OBJECT) return D3D_OK;
        if (d.Rows > 4 || d.Columns > 4) return D3DERR_INVALIDCALL;
        float v[4][4] = {};
        for (unsigned r = 0; r < d.Rows; r++)
            for (unsigned c = 0; c < d.Columns; c++)
                if (!value(in, d, r, c, &v[r][c])) return S_FALSE;   // input exhausted: stop
        advance(in, d);
        bool columns = d.Class == D3DXPC_MATRIX_COLUMNS;
        unsigned nregs = d.RegisterSet == D3DXRS_BOOL ? d.Rows * d.Columns : columns ? d.Columns : d.Rows;
        if (nregs > d.RegisterCount) nregs = d.RegisterCount;
        if (!nregs) return D3D_OK;
        if (d.RegisterSet == D3DXRS_BOOL) {
            BOOL b[16];
            for (unsigned i = 0; i < nregs; i++) b[i] = v[i / d.Columns][i % d.Columns] != 0.0f;
            return vs_ ? dev->SetVertexShaderConstantB(d.RegisterIndex, b, nregs)
                       : dev->SetPixelShaderConstantB(d.RegisterIndex, b, nregs);
        }
        float f[16] = {};
        int iv[16] = {};
        for (unsigned k = 0; k < nregs; k++)
            for (unsigned j = 0; j < 4; j++) {
                float x = columns ? (j < d.Rows ? v[j][k] : 0.0f) : (j < d.Columns ? v[k][j] : 0.0f);
                f[k * 4 + j] = x;
                iv[k * 4 + j] = (int)(x < 0 ? x - 0.5f : x + 0.5f);
            }
        if (d.RegisterSet == D3DXRS_INT4)
            return vs_ ? dev->SetVertexShaderConstantI(d.RegisterIndex, iv, nregs)
                       : dev->SetPixelShaderConstantI(d.RegisterIndex, iv, nregs);
        return vs_ ? dev->SetVertexShaderConstantF(d.RegisterIndex, f, nregs)
                   : dev->SetPixelShaderConstantF(d.RegisterIndex, f, nregs);
    }
    HRESULT set(IDirect3DDevice9* dev, D3DXHANDLE h, Kind kind, int type, const void* p, UINT count, bool transpose = false) {
        Node* n = node(h);
        if (!dev || !n || (!p && count)) return D3DERR_INVALIDCALL;
        Input in = {kind, type, p, count, transpose, 0};
        HRESULT hr = write(dev, n, in);
        return FAILED(hr) ? hr : D3D_OK;
    }

    // --- IUnknown / ID3DXBuffer ---
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override {
        static const GUID unk = {0, 0, 0, {0xC0, 0, 0, 0, 0, 0, 0, 0x46}};
        if (!ppv) return E_POINTER;
        if (!memcmp(&riid, &unk, sizeof(GUID)) || !memcmp(&riid, &IID_ID3DXBuffer, sizeof(GUID)) ||
            !memcmp(&riid, &IID_ID3DXConstantTable, sizeof(GUID))) {
            AddRef();
            *ppv = this;
            return S_OK;
        }
        *ppv = nullptr;
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return __atomic_add_fetch(&refs_, 1, __ATOMIC_RELAXED); }
    ULONG STDMETHODCALLTYPE Release() override {
        ULONG n = __atomic_sub_fetch(&refs_, 1, __ATOMIC_ACQ_REL);
        if (!n) { this->~ConstTable(); free(this); }
        return n;
    }
    void* STDMETHODCALLTYPE GetBufferPointer() override { return ctab_; }
    DWORD STDMETHODCALLTYPE GetBufferSize() override { return size_; }

    // --- ID3DXConstantTable ---
    HRESULT STDMETHODCALLTYPE GetDesc(D3DXCONSTANTTABLE_DESC* pDesc) override {
        if (!pDesc) return D3DERR_INVALIDCALL;
        *pDesc = desc_;
        return D3D_OK;
    }
    HRESULT STDMETHODCALLTYPE GetConstantDesc(D3DXHANDLE h, D3DXCONSTANT_DESC* pDesc, UINT* pCount) override {
        Node* n = node(h);
        if (!n) return D3DERR_INVALIDCALL;
        if (pDesc && (!pCount || *pCount >= 1)) *pDesc = n->desc;
        if (pCount) *pCount = 1;
        return D3D_OK;
    }
    UINT STDMETHODCALLTYPE GetSamplerIndex(D3DXHANDLE h) override {
        Node* n = node(h);
        return n && n->desc.RegisterSet == D3DXRS_SAMPLER ? n->desc.RegisterIndex : (UINT)-1;
    }
    D3DXHANDLE STDMETHODCALLTYPE GetConstant(D3DXHANDLE hParent, UINT Index) override {
        if (!hParent) return Index < ntop_ ? handle(&nodes_[Index]) : nullptr;
        Node* p = node(hParent);
        if (!p || p->elements || Index >= p->nsub) return nullptr;
        return handle(&p->sub[Index]);
    }
    D3DXHANDLE STDMETHODCALLTYPE GetConstantByName(D3DXHANDLE hParent, const char* pName) override {
        Node* scope = nullptr;
        if (hParent && !(scope = node(hParent))) return nullptr;
        return handle(by_name(scope, pName));
    }
    D3DXHANDLE STDMETHODCALLTYPE GetConstantElement(D3DXHANDLE h, UINT Index) override {
        Node* n = node(h);
        if (!n) return nullptr;
        if (n->elements) return Index < n->nsub ? handle(&n->sub[Index]) : nullptr;
        return Index == 0 ? handle(n) : nullptr;
    }
    HRESULT STDMETHODCALLTYPE SetDefaults(IDirect3DDevice9* dev) override {
        if (!dev) return D3DERR_INVALIDCALL;
        for (unsigned i = 0; i < ntop_; i++)
            if (nodes_[i].desc.DefaultValue)
                set(dev, handle(&nodes_[i]), SCALARS, -1, nodes_[i].desc.DefaultValue, nodes_[i].desc.Bytes / 4);
        return D3D_OK;
    }
    HRESULT STDMETHODCALLTYPE SetValue(IDirect3DDevice9* dev, D3DXHANDLE h, const void* pData, UINT Bytes) override {
        return set(dev, h, SCALARS, -1, pData, Bytes / 4);
    }
    HRESULT STDMETHODCALLTYPE SetBool(IDirect3DDevice9* dev, D3DXHANDLE h, BOOL b) override { return set(dev, h, SCALARS, D3DXPT_BOOL, &b, 1); }
    HRESULT STDMETHODCALLTYPE SetBoolArray(IDirect3DDevice9* dev, D3DXHANDLE h, const BOOL* pb, UINT Count) override {
        return set(dev, h, SCALARS, D3DXPT_BOOL, pb, Count);
    }
    HRESULT STDMETHODCALLTYPE SetInt(IDirect3DDevice9* dev, D3DXHANDLE h, INT n) override { return set(dev, h, SCALARS, D3DXPT_INT, &n, 1); }
    HRESULT STDMETHODCALLTYPE SetIntArray(IDirect3DDevice9* dev, D3DXHANDLE h, const INT* pn, UINT Count) override {
        return set(dev, h, SCALARS, D3DXPT_INT, pn, Count);
    }
    HRESULT STDMETHODCALLTYPE SetFloat(IDirect3DDevice9* dev, D3DXHANDLE h, FLOAT f) override { return set(dev, h, SCALARS, D3DXPT_FLOAT, &f, 1); }
    HRESULT STDMETHODCALLTYPE SetFloatArray(IDirect3DDevice9* dev, D3DXHANDLE h, const FLOAT* pf, UINT Count) override {
        return set(dev, h, SCALARS, D3DXPT_FLOAT, pf, Count);
    }
    HRESULT STDMETHODCALLTYPE SetVector(IDirect3DDevice9* dev, D3DXHANDLE h, const D3DXVECTOR4* pVector) override {
        return set(dev, h, VECTORS, D3DXPT_FLOAT, pVector, 1);
    }
    HRESULT STDMETHODCALLTYPE SetVectorArray(IDirect3DDevice9* dev, D3DXHANDLE h, const D3DXVECTOR4* pVector, UINT Count) override {
        return set(dev, h, VECTORS, D3DXPT_FLOAT, pVector, Count);
    }
    HRESULT STDMETHODCALLTYPE SetMatrix(IDirect3DDevice9* dev, D3DXHANDLE h, const D3DXMATRIX* pMatrix) override {
        return set(dev, h, MATRICES, D3DXPT_FLOAT, pMatrix, 1);
    }
    HRESULT STDMETHODCALLTYPE SetMatrixArray(IDirect3DDevice9* dev, D3DXHANDLE h, const D3DXMATRIX* pMatrix, UINT Count) override {
        return set(dev, h, MATRICES, D3DXPT_FLOAT, pMatrix, Count);
    }
    HRESULT STDMETHODCALLTYPE SetMatrixPointerArray(IDirect3DDevice9* dev, D3DXHANDLE h, const D3DXMATRIX** ppMatrix, UINT Count) override {
        return set(dev, h, MATRIX_POINTERS, D3DXPT_FLOAT, ppMatrix, Count);
    }
    HRESULT STDMETHODCALLTYPE SetMatrixTranspose(IDirect3DDevice9* dev, D3DXHANDLE h, const D3DXMATRIX* pMatrix) override {
        return set(dev, h, MATRICES, D3DXPT_FLOAT, pMatrix, 1, true);
    }
    HRESULT STDMETHODCALLTYPE SetMatrixTransposeArray(IDirect3DDevice9* dev, D3DXHANDLE h, const D3DXMATRIX* pMatrix, UINT Count) override {
        return set(dev, h, MATRICES, D3DXPT_FLOAT, pMatrix, Count, true);
    }
    HRESULT STDMETHODCALLTYPE SetMatrixTransposePointerArray(IDirect3DDevice9* dev, D3DXHANDLE h, const D3DXMATRIX** ppMatrix,
                                                             UINT Count) override {
        return set(dev, h, MATRIX_POINTERS, D3DXPT_FLOAT, ppMatrix, Count, true);
    }
};

HRESULT no_compiler(const char* what, LPD3DXBUFFER* ppErrorMsgs) {
    static const char kFmt[] = "fnv-nx: %s is not available (no runtime shader compiler; the game ships precompiled "
                               "shaders in Data/Shaders/shaderpackage*.sdp)\n";
    char msg[200];
    int n = snprintf(msg, sizeof msg, kFmt, what);
    D3DX_LOG_LIMITED(4, "d3dx: %s", msg);
    if (ppErrorMsgs) *ppErrorMsgs = fnvnx_d3dx::make_buffer(msg, (DWORD)(n + 1));
    return E_FAIL;
}

} // namespace

extern "C" {

DWORD WINAPI D3DXGetShaderVersion(const DWORD* pFunction) { return pFunction ? pFunction[0] : 0; }

UINT WINAPI D3DXGetShaderSize(const DWORD* pFunction) {
    if (!pFunction || !is_shader_version(pFunction[0])) return 0;
    unsigned major = (pFunction[0] >> 8) & 0xFF, i = 1;
    while (i < kMaxTokens) {
        unsigned n = insn_tokens(pFunction + i, major);
        if (!n) return (i + 1) * 4;
        i += n;
    }
    return 0;
}

HRESULT WINAPI D3DXFindShaderComment(const DWORD* pFunction, DWORD FourCC, const void** ppData, UINT* pSizeInBytes) {
    if (ppData) *ppData = nullptr;
    if (pSizeInBytes) *pSizeInBytes = 0;
    if (!pFunction) return D3DERR_INVALIDCALL;
    if (!is_shader_version(pFunction[0])) return D3DXERR_INVALIDDATA;
    unsigned major = (pFunction[0] >> 8) & 0xFF, i = 1;
    while (i < kMaxTokens) {
        const DWORD* p = pFunction + i;
        unsigned n = insn_tokens(p, major);
        if (!n) break;
        if ((p[0] & 0xFFFF) == 0xFFFE && n >= 2 && p[1] == FourCC) {
            if (ppData) *ppData = p + 2;
            if (pSizeInBytes) *pSizeInBytes = (n - 2) * 4;
            return D3D_OK;
        }
        i += n;
    }
    return S_FALSE;
}

HRESULT WINAPI D3DXGetShaderConstantTableEx(const DWORD* pFunction, DWORD Flags, LPD3DXCONSTANTTABLE* ppConstantTable) {
    (void)Flags;
    if (!ppConstantTable) return D3DERR_INVALIDCALL;
    *ppConstantTable = nullptr;
    const void* data = nullptr;
    UINT size = 0;
    HRESULT hr = D3DXFindShaderComment(pFunction, MAKEFOURCC('C', 'T', 'A', 'B'), &data, &size);
    if (hr != D3D_OK) return FAILED(hr) ? hr : D3DXERR_INVALIDDATA;
    void* mem = malloc(sizeof(ConstTable));
    if (!mem) return E_OUTOFMEMORY;
    ConstTable* t = new (mem) ConstTable();
    hr = t->init(data, size, pFunction[0]);
    if (FAILED(hr)) {
        t->Release();
        return hr;
    }
    *ppConstantTable = t;
    return D3D_OK;
}

HRESULT WINAPI D3DXGetShaderConstantTable(const DWORD* pFunction, LPD3DXCONSTANTTABLE* ppConstantTable) {
    return D3DXGetShaderConstantTableEx(pFunction, 0, ppConstantTable);
}

const char* WINAPI D3DXGetPixelShaderProfile(IDirect3DDevice9* pDevice) {
    D3DCAPS9 caps;
    if (!pDevice || FAILED(pDevice->GetDeviceCaps(&caps))) return nullptr;
    unsigned major = D3DSHADER_VERSION_MAJOR(caps.PixelShaderVersion), minor = caps.PixelShaderVersion & 0xFF;
    if (major >= 3) return "ps_3_0";
    if (major == 2) return "ps_2_0";
    if (major == 1) {
        static const char* const k[] = {"ps_1_1", "ps_1_1", "ps_1_2", "ps_1_3", "ps_1_4"};
        return k[minor > 4 ? 4 : minor];
    }
    return nullptr;
}

const char* WINAPI D3DXGetVertexShaderProfile(IDirect3DDevice9* pDevice) {
    D3DCAPS9 caps;
    if (!pDevice || FAILED(pDevice->GetDeviceCaps(&caps))) return nullptr;
    unsigned major = D3DSHADER_VERSION_MAJOR(caps.VertexShaderVersion);
    if (major >= 3) return "vs_3_0";
    if (major == 2) return "vs_2_0";
    if (major == 1) return "vs_1_1";
    return nullptr;
}

HRESULT WINAPI D3DXAssembleShader(LPCSTR, UINT, const D3DXMACRO*, LPD3DXINCLUDE, DWORD, LPD3DXBUFFER* ppShader,
                                  LPD3DXBUFFER* ppErrorMsgs) {
    if (ppShader) *ppShader = nullptr;
    return no_compiler("D3DXAssembleShader", ppErrorMsgs);
}

HRESULT WINAPI D3DXAssembleShaderFromFileA(LPCSTR pSrcFile, const D3DXMACRO*, LPD3DXINCLUDE, DWORD,
                                           LPD3DXBUFFER* ppShader, LPD3DXBUFFER* ppErrorMsgs) {
    (void)pSrcFile;
    if (ppShader) *ppShader = nullptr;
    return no_compiler("D3DXAssembleShaderFromFileA", ppErrorMsgs);
}

HRESULT WINAPI D3DXCompileShader(LPCSTR, UINT, const D3DXMACRO*, LPD3DXINCLUDE, LPCSTR, LPCSTR, DWORD,
                                 LPD3DXBUFFER* ppShader, LPD3DXBUFFER* ppErrorMsgs, LPD3DXCONSTANTTABLE* ppConstantTable) {
    if (ppShader) *ppShader = nullptr;
    if (ppConstantTable) *ppConstantTable = nullptr;
    return no_compiler("D3DXCompileShader", ppErrorMsgs);
}

HRESULT WINAPI D3DXCompileShaderFromFileA(LPCSTR, const D3DXMACRO*, LPD3DXINCLUDE, LPCSTR, LPCSTR, DWORD,
                                          LPD3DXBUFFER* ppShader, LPD3DXBUFFER* ppErrorMsgs,
                                          LPD3DXCONSTANTTABLE* ppConstantTable) {
    if (ppShader) *ppShader = nullptr;
    if (ppConstantTable) *ppConstantTable = nullptr;
    return no_compiler("D3DXCompileShaderFromFileA", ppErrorMsgs);
}

} // extern "C"
