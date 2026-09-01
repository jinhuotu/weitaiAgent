"""平面图轿车/重卡符号几何。"""

from api.services.layouts.schema import RectM
from api.services.layouts.symbols import vehicle_polylines


def test_car_symbol_has_sedan_cabin() -> None:
    rect = RectM(x=0, y=0, w=3, h=6)
    rings = vehicle_polylines(rect, "head", truck=False)
    assert len(rings) >= 6
    body = rings[0]
    assert len(body) >= 20
    xs = [p[0] for p in body]
    ys = [p[1] for p in body]
    assert max(xs) - min(xs) < 2.2
    assert max(ys) - min(ys) > 3.2
    nose_y = max(ys)
    tail_y = min(ys)
    nose_w = max(p[0] for p in body if p[1] > nose_y - 0.08) - min(
        p[0] for p in body if p[1] > nose_y - 0.08
    )
    tail_w = max(p[0] for p in body if p[1] < tail_y + 0.08) - min(
        p[0] for p in body if p[1] < tail_y + 0.08
    )
    assert nose_w > 0.35
    assert tail_w > 0.35
