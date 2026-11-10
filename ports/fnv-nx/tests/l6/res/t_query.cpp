// part e: queries (object part): creation per ENV answer, GetType / GetDataSize, Issue / GetData state machine with a test backend
#include "t_common.h"
#include "res_core.h"
void t_query() {
    t_part('e'); TEnv env = t_env(); NxDevice9* d = env.dev; IDirect3DQuery9* q = nullptr;
    FILE* f = t_open("qsizes.txt"); char line[256]; int n = 0;   // spec tables.query_type: `name value size`
    while (f && fgets(line, sizeof line, f)) { char nm[80]; unsigned v, sz; if (sscanf(line, "%79s %u %u", nm, &v, &sz) != 3) continue; n++; CHECK_EQ(nx_query_data_size(v), sz); }
    if (f) fclose(f); CHECK_EQ(n, 15);
    for (uint32_t t = 0; t < 40; t++) {
        bool known = nx_query_data_size(t) != 0; q = (IDirect3DQuery9*)1; HRESULT hr = d->CreateQuery((D3DQUERYTYPE)t, &q);
        bool ok = known && t != D3DQUERYTYPE_VCACHE;                                                               // test ENV: VCACHE unsupported
        CHECK_HR(hr, ok ? D3D_OK : D3DERR_NOTAVAILABLE); CHECK_HR(d->CreateQuery((D3DQUERYTYPE)t, nullptr), ok ? D3D_OK : D3DERR_NOTAVAILABLE);   // null ppQuery: support check only
        if (ok) { CHECK(q != nullptr && q->GetType() == (D3DQUERYTYPE)t && q->GetDataSize() == nx_query_data_size(t)); IDirect3DDevice9* gd; CHECK_HR(q->GetDevice(&gd), D3D_OK); gd->Release(); q->Release(); } else CHECK(q == nullptr);
    }
    CHECK_HR(d->CreateQuery((D3DQUERYTYPE)0xFFFFFFFF, &q), D3DERR_NOTAVAILABLE); CHECK_HR(d->CreateQuery((D3DQUERYTYPE)0x16, &q), D3DERR_NOTAVAILABLE);
    TEnv ex = t_env(true); CHECK_HR(ex.devex->CreateQuery(D3DQUERYTYPE_EVENT, &q), D3D_OK); q->Release();
    // state machine: created -> (BEGIN) building -> (END) signaled; GetData per state
    d->CreateQuery(D3DQUERYTYPE_OCCLUSION, &q); DWORD v = 0x12345678; uint8_t big[16]; g_q_ready = false; g_q_issues = g_q_flushes = 0;
    CHECK_HR(q->GetData(&v, 4, 0), S_OK); CHECK_EQ(v, 0xdddddddd);                                               // never issued: S_OK + 0xdd fill (Wine d3d9 query.c)
    memset(big, 0x11, 16); CHECK_HR(q->GetData(big, 16, 0), S_OK); CHECK(big[3] == 0xdd && big[4] == 0 && big[15] == 0); CHECK_HR(q->GetData(nullptr, 0, 0), S_OK);
    CHECK_HR(q->Issue(D3DISSUE_BEGIN), D3D_OK); CHECK_EQ(g_q_issues, 1); CHECK_EQ(g_q_last_flags, D3DISSUE_BEGIN); CHECK_HR(q->GetData(&v, 4, 0), S_FALSE); CHECK_HR(q->GetData(&v, 4, D3DGETDATA_FLUSH), S_FALSE); CHECK_EQ(g_q_flushes, 0);   // building: never reaches the backend
    g_q_ready = true; CHECK_HR(q->GetData(&v, 4, 0), S_FALSE);                                                    // still building (ready is irrelevant until END)
    CHECK_HR(q->Issue(D3DISSUE_END), D3D_OK); CHECK_EQ(g_q_last_flags, D3DISSUE_END); CHECK_HR(q->GetData(&v, 4, 0), S_OK); CHECK_EQ(v & 0xFF, 0xA0 + D3DQUERYTYPE_OCCLUSION);
    g_q_ready = false; CHECK_HR(q->GetData(&v, 4, 0), S_FALSE); CHECK_EQ(g_q_flushes, 0); CHECK_HR(q->GetData(&v, 4, D3DGETDATA_FLUSH), S_FALSE); CHECK_EQ(g_q_flushes, 1);
    g_q_ready = true; memset(big, 0x11, 16); CHECK_HR(q->GetData(big, 16, 0), S_OK); CHECK(big[0] == 0xA9 && big[3] == 0xA0 + 3 + 9 && big[4] == 0x11);   // copies min(size, data_size)
    CHECK_HR(q->GetData(big, 2, 0), S_OK); CHECK_HR(q->GetData(nullptr, 4, 0), S_OK); CHECK_HR(q->GetData(nullptr, 0, 0), S_OK);
    CHECK_HR(q->Issue(0), D3D_OK); CHECK_EQ(g_q_last_flags, 0); CHECK_HR(q->GetData(&v, 4, 0), S_OK);                // Wine: any flags other than BEGIN = signaled
    CHECK_HR(q->Issue(D3DISSUE_BEGIN | D3DISSUE_END), D3D_OK); CHECK_HR(q->GetData(&v, 4, 0), S_FALSE);            // BEGIN bit wins (wined3d cs issue_query)
    CHECK_HR(q->Issue(0xFFFFFFF0), D3D_OK); CHECK_EQ(g_q_issues, 5); CHECK_EQ(q->Release(), 0);
    d->CreateQuery(D3DQUERYTYPE_EVENT, &q); CHECK_HR(q->Issue(D3DISSUE_END), D3D_OK); g_q_ready = false; BOOL b = 5; CHECK_HR(q->GetData(&b, 4, 0), S_FALSE); CHECK_EQ(b, 5); g_q_ready = true; CHECK_HR(q->GetData(&b, 4, 0), S_OK); q->Release();
}
