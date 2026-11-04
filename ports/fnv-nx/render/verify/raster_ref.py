#!/usr/bin/env python3
# V6n exact coverage reference: D3D9 rasterization rules computed with rational arithmetic.
#   pixel (x, y) is covered by a triangle when its centre (x, y) (D3D9: integer coordinates) is inside, with the
#   top-left rule for centres exactly on an edge (y-down window space: a "top" edge is horizontal with the
#   triangle below it, a "left" edge has the triangle to its right). Both windings are handled (cull is separate).
# Used to adjudicate coverage where softpipe (the colour/state oracle) is not exact: softpipe's span setup rounds
# slanted edges to whole pixels at sub-pixel distances (found by V6n, RENDER_VERIFY.md §4.3).
#   coverage(tri, w, h) -> set of (x, y)
from fractions import Fraction as F

def _edge_ok(ax, ay, bx, by, px, py, ccw):
    # edge function, sign normalised so that inside > 0
    e = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    if ccw:
        e = -e
    if e > 0:
        return True
    if e < 0:
        return False
    # on the edge: top-left rule (D3D9, y down). Direction of the edge in the winding where inside is e > 0.
    dx, dy = (bx - ax, by - ay) if not ccw else (ax - bx, ay - by)
    # with y down and inside on the "right-hand" side of travel for e > 0 ... top edge: dy == 0 and dx > 0;
    # left edge: dy < 0 (travelling up the screen)
    return (dy == 0 and dx > 0) or dy < 0

def coverage(tri, w, h):
    (x0, y0), (x1, y1), (x2, y2) = [(F(x), F(y)) for x, y in tri]
    area = (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0)
    if area == 0:
        return set()
    ccw = area < 0   # area > 0: clockwise on screen (y down)
    out = set()
    lo_x, hi_x = max(0, int(min(x0, x1, x2)) - 1), min(w - 1, int(max(x0, x1, x2)) + 1)
    lo_y, hi_y = max(0, int(min(y0, y1, y2)) - 1), min(h - 1, int(max(y0, y1, y2)) + 1)
    for py in range(lo_y, hi_y + 1):
        for px in range(lo_x, hi_x + 1):
            if (_edge_ok(x0, y0, x1, y1, px, py, ccw) and _edge_ok(x1, y1, x2, y2, px, py, ccw) and
                    _edge_ok(x2, y2, x0, y0, px, py, ccw)):
                out.add((px, py))
    return out

if __name__ == '__main__':
    import sys
    t = [tuple(map(float, a.split(','))) for a in sys.argv[1:4]]
    c = coverage(t, 64, 64)
    print(sorted(c))
