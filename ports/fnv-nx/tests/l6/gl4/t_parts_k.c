/* Part k: QueryBegin / QueryEnd / query_result (occlusion: exact pixel counts incl. overlapping, restarted and
 * submit-spanning queries and the backend's own blits; event; timestamp; wait 0 / 1 and refused calls) and
 * sampler_update (a later draw changes its filtering; nothing of the old definition survives). */
#include "t_ref.h"

static void draw_rect(Stream* s, uint32_t id, int x0, int y0, int x1, int y1, uint32_t fs) {
    char src[600];
    snprintf(src, sizeof src, "#version 460\nvoid main() { vec2 c[4] = vec2[4](vec2(%d, %d), vec2(%d, %d), vec2(%d, %d), vec2(%d, %d));\n"
             "vec2 p = c[gl_VertexID]; gl_Position = vec4(2.0 * p.x / 16.0 - 1.0, 1.0 - 2.0 * p.y / 16.0, 0.5, 1.0); }\n",
             x0, y0, x1, y0, x0, y1, x1, y1);
    uint32_t vs = t_shader(NxStage_Vertex, src);
    t_state(s, id, vs, fs, 0, T_W0_ALL, 0, 0, 0);
    t_op(s, NxOp_BindState, 1, id);
    t_op(s, NxOp_Draw, 5, NxPrim_TriStrip, 4, 1, 0, 0);
}
static void begin1(Stream* s, uint32_t T) { t_op(s, NxOp_BeginPass4, 5, 1, T, 0, 0, 0); }
static uint64_t result(uint32_t id, uint32_t wait, uint64_t* out) { *out = 0xDEAD; return t_ext(NXGPU_FN_QUERY_RESULT, 3, id, wait, (uint64_t)(uintptr_t)out); }
static void expect(uint32_t id, uint64_t want, const char* what) {
    uint64_t out;
    CHECKM(result(id, 0, &out) == 0 && out == want, "%s: wait 0 gives %llu, want %llu", what, (unsigned long long)out, (unsigned long long)want);
    CHECKM(result(id, 1, &out) == 0 && out == want, "%s: wait 1 gives %llu", what, (unsigned long long)out);
}

static void occlusion(void) {
    uint32_t T = t_image(NxImgType_2D, NxFmt_RGBA8, 16, 16, 1, 1, 1), fs = t_shader(NxStage_Fragment, T_FS_WHITE);
    t_zero(T, NxImgType_2D, NxFmt_RGBA8, 16, 16, 1, 1);
    Stream* s = (Stream*)calloc(1, sizeof *s);
    begin1(s, T);
    t_op(s, NxOp_QueryBegin, 2, 1, NxQuery_Occlusion);
    draw_rect(s, 1, 2, 3, 10, 8, fs);   /* 8 x 5 = 40 pixels */
    t_op(s, NxOp_QueryEnd, 1, 1);
    t_op(s, NxOp_EndPass, 0);
    CHECK(t_submit(s) == 0);
    expect(1, 40, "occlusion count of an 8 x 5 rectangle");
    /* overlapping queries 2 and 3: 2 = R1 + R2, 3 = R2 + R3 */
    begin1(s, T);
    t_op(s, NxOp_QueryBegin, 2, 2, NxQuery_Occlusion);
    draw_rect(s, 2, 2, 3, 10, 8, fs);
    t_op(s, NxOp_QueryBegin, 2, 3, NxQuery_Occlusion);
    draw_rect(s, 3, 0, 0, 3, 3, fs);
    t_op(s, NxOp_QueryEnd, 1, 2);
    draw_rect(s, 4, 12, 12, 14, 14, fs);
    t_op(s, NxOp_QueryEnd, 1, 3);
    t_op(s, NxOp_EndPass, 0);
    CHECK(t_submit(s) == 0);
    expect(2, 49, "overlapping query, begun first"); expect(3, 13, "overlapping query, begun second");
    /* restart: Begin again discards what was counted */
    begin1(s, T);
    t_op(s, NxOp_QueryBegin, 2, 4, NxQuery_Occlusion);
    draw_rect(s, 5, 2, 3, 10, 8, fs);
    t_op(s, NxOp_QueryBegin, 2, 4, NxQuery_Occlusion);
    draw_rect(s, 6, 12, 12, 14, 14, fs);
    t_op(s, NxOp_QueryEnd, 1, 4);
    t_op(s, NxOp_QueryBegin, 2, 5, NxQuery_Occlusion);
    t_op(s, NxOp_QueryEnd, 1, 5);
    t_op(s, NxOp_EndPass, 0);
    CHECK(t_submit(s) == 0);
    expect(4, 4, "restarted query"); expect(5, 0, "query without draws");
    /* a query spanning two submits */
    begin1(s, T);
    t_op(s, NxOp_QueryBegin, 2, 6, NxQuery_Occlusion);
    draw_rect(s, 7, 2, 3, 10, 8, fs);
    t_op(s, NxOp_EndPass, 0);
    CHECK(t_submit(s) == 0);
    uint64_t out;
    CHECKM(result(6, 0, &out) == 1, "a query still active is not ready (wait 0)");
    begin1(s, T);
    draw_rect(s, 8, 0, 0, 3, 3, fs);
    t_op(s, NxOp_QueryEnd, 1, 6);
    t_op(s, NxOp_EndPass, 0);
    CHECK(t_submit(s) == 0);
    expect(6, 49, "query spanning two submits");
    /* the backend's own draws (a scaling blit) are not counted */
    uint32_t a = t_image(NxImgType_2D, NxFmt_RGBA8, 2, 2, 1, 1, 1), b = t_image(NxImgType_2D, NxFmt_RGBA8, 4, 4, 1, 1, 1);
    t_zero(a, NxImgType_2D, NxFmt_RGBA8, 2, 2, 1, 1); t_zero(b, NxImgType_2D, NxFmt_RGBA8, 4, 4, 1, 1);
    t_op(s, NxOp_QueryBegin, 2, 7, NxQuery_Occlusion);
    t_op(s, NxOp_Blit, 13, a, 0, 0, 0, 2, 2, b, 0, 0, 0, 4, 4, 0);
    begin1(s, T);
    draw_rect(s, 9, 12, 12, 14, 14, fs);
    t_op(s, NxOp_EndPass, 0);
    t_op(s, NxOp_Blit, 13, a, 0, 0, 0, 2, 2, b, 0, 0, 0, 4, 4, 0);
    t_op(s, NxOp_QueryEnd, 1, 7);
    CHECK(t_submit(s) == 0);
    expect(7, 4, "blits inside a query are not counted");
    /* wait on a query that was never ended can never complete */
    t_op(s, NxOp_QueryBegin, 2, 8, NxQuery_Occlusion);
    CHECK(t_submit(s) == 0);
    CHECK(result(8, 0, &out) == 1 && result(8, 1, &out) == NXGPU_BAD_CALL);
    t_op(s, NxOp_QueryEnd, 1, 8);
    CHECK(t_submit(s) == 0);
    expect(8, 0, "query ended after an idle submit");
    CHECK(result(999, 0, &out) == NXGPU_BAD_CALL && result(0, 0, &out) == NXGPU_BAD_CALL && result(1, 2, &out) == NXGPU_BAD_CALL);
    CHECK(t_ext(NXGPU_FN_QUERY_RESULT, 3, (uint64_t)1, (uint64_t)0, (uint64_t)0) == NXGPU_BAD_CALL);
    t_op(s, NxOp_QueryEnd, 1, 77);
    CHECK(t_submit(s) == NXGPU_BAD_CALL);   /* never begun */
    t_op(s, NxOp_QueryEnd, 1, 1);
    CHECK(t_submit(s) == NXGPU_BAD_CALL);   /* already ended */
    t_op(s, NxOp_QueryBegin, 2, 9, 3);
    CHECK(t_submit(s) == NXGPU_BAD_CALL);   /* not a kind */
    t_op(s, NxOp_QueryBegin, 2, 0, NxQuery_Occlusion);
    CHECK(t_submit(s) == NXGPU_BAD_CALL);   /* id 0 */
    free(s);
}

static void event_timestamp(void) {
    uint32_t T = t_image(NxImgType_2D, NxFmt_RGBA8, 16, 16, 1, 1, 1), fs = t_shader(NxStage_Fragment, T_FS_WHITE);
    Stream* s = (Stream*)calloc(1, sizeof *s);
    uint64_t out;
    t_op(s, NxOp_QueryBegin, 2, 20, NxQuery_Event);
    t_op(s, NxOp_QueryBegin, 2, 21, NxQuery_Event);
    t_op(s, NxOp_QueryBegin, 2, 22, NxQuery_TimestampNs);
    t_op(s, NxOp_QueryBegin, 2, 23, NxQuery_TimestampNs);
    begin1(s, T);
    t_op(s, NxOp_QueryEnd, 1, 22);
    for (uint32_t i = 0; i < 30; i++) draw_rect(s, 30 + i, 0, 0, 16, 16, fs);
    t_op(s, NxOp_QueryEnd, 1, 20);
    t_op(s, NxOp_QueryEnd, 1, 23);
    t_op(s, NxOp_EndPass, 0);
    CHECK(t_submit(s) == 0);
    CHECKM(result(20, 0, &out) == 0 && out == 1, "event query ended: ready, TRUE (wait 0)");
    CHECKM(result(20, 1, &out) == 0 && out == 1, "event query ended: ready, TRUE (wait 1)");
    CHECKM(result(21, 0, &out) == 1, "event query never ended: not ready (wait 0)");
    CHECKM(result(21, 1, &out) == NXGPU_BAD_CALL, "event query never ended: wait 1 is refused");
    uint64_t t1, t2;
    CHECK(result(22, 1, &t1) == 0 && result(23, 0, &t2) == 0);
    CHECKM(t1 > 0 && t2 >= t1, "timestamps: %llu then %llu (ns, monotonic)", (unsigned long long)t1, (unsigned long long)t2);
    CHECKM(result(22, 0, &out) == 0 && out == t1, "a timestamp result is stable");
    free(s);
}

/* ---- sampler_update --------------------------------------------------------------------------------------------- */
static void sampler(void) {
    static const char fs_tex[] = "#version 460\nlayout (location = 0) out vec4 o;\nlayout (binding = 0) uniform sampler2D t;\n"
                                 "void main() { o = texture(t, vec2(gl_FragCoord.x / 16.0, 0.5)); }\n";
    uint32_t vs = t_shader(NxStage_Vertex, T_VS_FULL), fs = t_shader(NxStage_Fragment, fs_tex);
    uint8_t tex[8] = {0, 0, 0, 255, 255, 255, 255, 255};
    uint32_t img = t_image(NxImgType_2D, NxFmt_RGBA8, 2, 1, 1, 1, 1), box[6] = {0, 0, 0, 2, 1, 1};
    CHECK(t_update(img, 0, 0, box, tex, 8, 8, 0) == 0);
    NxSamplerState st;
    memset(&st, 0, sizeof st);
    st.wrap = 2 | 2 << 3 | 2 << 6;
    uint32_t smp = t_sampler(&st);
    CHECK(smp != 0);
    uint8_t want_n[16 * 4], want_l[16 * 4];
    ref_point(tex, 2, 1, want_n, 16, 1, 4);
    ref_bilinear(tex, 2, 1, want_l, 16, 1, 4);
    uint32_t T[3];
    Stream* s = (Stream*)calloc(1, sizeof *s);
    t_state(s, 1, vs, fs, 0, T_W0_ALL, 0, 0, 0);
    for (int k = 0; k < 3; k++) {
        T[k] = t_image(NxImgType_2D, NxFmt_RGBA8, 16, 1, 1, 1, 1);
        if (k == 1) { st.filter = NXSMP_MIN_LINEAR | NXSMP_MAG_LINEAR; CHECK(t_ext(NXGPU_FN_SAMPLER_UPDATE, 2, (uint64_t)smp, (uint64_t)(uintptr_t)&st) == 0); }
        if (k == 2) { st.filter = 0; CHECK(t_ext(NXGPU_FN_SAMPLER_UPDATE, 2, (uint64_t)smp, (uint64_t)(uintptr_t)&st) == 0); }
        begin1(s, T[k]);
        t_op(s, NxOp_BindTextures, 4, NxStage_Fragment, 0, 1, NX_TEX_HANDLE(img, smp));
        t_op(s, NxOp_BindState, 1, 1);
        t_op(s, NxOp_Draw, 5, 0, 3, 1, 0, 0);
        t_op(s, NxOp_EndPass, 0);
        CHECK(t_submit(s) == 0);
    }
    uint8_t* g0 = t_read_all(T[0], 0, 0, 16, 1, 1, t_fmt(NxFmt_RGBA8));
    uint8_t* g1 = t_read_all(T[1], 0, 0, 16, 1, 1, t_fmt(NxFmt_RGBA8));
    uint8_t* g2 = t_read_all(T[2], 0, 0, 16, 1, 1, t_fmt(NxFmt_RGBA8));
    CHECKM(g0 && !memcmp(g0, want_n, 64), "nearest sampler magnifies in blocks");
    int md = g1 ? maxdiff(g1, want_l, 64) : 99;
    CHECKM(md <= 2, "after sampler_update (linear) the draw is bilinear: max diff %d", md);
    CHECKM(g1 && memcmp(g1, want_n, 64), "the update changed the output");
    CHECKM(g2 && !memcmp(g2, want_n, 64), "updating back to nearest leaves nothing of the linear definition");
    CHECK(t_ext(NXGPU_FN_SAMPLER_UPDATE, 2, (uint64_t)0, (uint64_t)(uintptr_t)&st) == NXGPU_BAD_CALL);
    CHECK(t_ext(NXGPU_FN_SAMPLER_UPDATE, 2, (uint64_t)(smp + 1), (uint64_t)(uintptr_t)&st) == NXGPU_BAD_CALL);
    CHECK(t_ext(NXGPU_FN_SAMPLER_UPDATE, 2, (uint64_t)smp, (uint64_t)0) == NXGPU_BAD_CALL);
    free(g0); free(g1); free(g2); free(s);
}

void part_k(void) { occlusion(); event_timestamp(); sampler(); }
