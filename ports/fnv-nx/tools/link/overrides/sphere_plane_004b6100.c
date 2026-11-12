/* fnvnx_sphere_plane_004b6100 - classify a bounding sphere against a plane (0x004b6100) as one leaf (stream S15-simd,
 * docs/PERF.md 11.6, tests/s15/simd/cull_test.c).
 *
 * The decomp calls the plane's distance 0x004b6160 (normal . center - constant), which calls the dot product
 * 0x004b6190: three frames per test, 38 A64 instructions. Here the same float operations in the same
 * order: d = ((n.x c.x + n.y c.y) + n.z c.z) - k; 2 if d <= -r, else 1 if d >= r, else 0 (NaN: 0). Layouts: sphere
 * {center x, y, z; radius}, plane {normal x, y, z; constant}, 16 bytes each, ILP32 and x86 alike. The override
 * inlines copies of 0x004b6160 and 0x004b6190: an override or patch of either must update it too. */
struct fnvnx_sphere { float c[3], r; };
struct fnvnx_plane { float n[3], k; };

int fnvnx_sphere_plane_004b6100(const struct fnvnx_sphere *s, const struct fnvnx_plane *p)
{
    float d = p->n[0] * s->c[0] + p->n[1] * s->c[1];
    d = d + p->n[2] * s->c[2];
    d = d - p->k;
    if (d <= -s->r)
        return 2;
    return d >= s->r ? 1 : 0;
}
