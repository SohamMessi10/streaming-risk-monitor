import pytest
from hypothesis import given
from hypothesis import strategies as st

from riskmon.series import Downsampler


def direct(points, origin, width):
    """The reference: bucket every point at the final width, from the origin."""
    buckets = {}
    for time, price, std in points:
        k = (time - origin) // width
        b = buckets.setdefault(k, {"k": k, "t": time, "low": price, "high": price, "last": price, "std": None, "n": 0})
        b["low"], b["high"], b["last"] = min(b["low"], price), max(b["high"], price), price
        if std is not None:
            b["std"] = std
        b["n"] += 1
    return [buckets[k] for k in sorted(buckets)]


points_strategy = st.lists(
    st.tuples(st.integers(0, 5_000), st.floats(1, 1_000), st.one_of(st.none(), st.floats(0, 1))),
    min_size=1, max_size=400,
).map(lambda ps: sorted(ps, key=lambda p: p[0]))


@given(points_strategy, st.integers(2, 40))
def test_downsampling_equals_bucketing_every_point_at_the_final_width(points, max_points):
    sampler = Downsampler(max_points)
    for point in points:
        sampler.add(*point)
    out = sampler.to_dict()
    assert len(out["points"]) <= max_points
    assert out["points"] == direct(points, points[0][0], out["width_ms"])
    assert sum(p["n"] for p in out["points"]) == len(points)


@given(points_strategy, st.integers(2, 40))
def test_the_width_is_the_smallest_doubling_that_fits(points, max_points):
    sampler = Downsampler(max_points)
    for point in points:
        sampler.add(*point)
    width = sampler.width
    assert width & (width - 1) == 0                                   # a power of two
    if width > 1:
        assert len(direct(points, points[0][0], width // 2)) > max_points


def test_memory_stays_bounded_over_a_long_feed():
    sampler = Downsampler(50)
    for i in range(200_000):
        sampler.add(i * 7, 100.0 + (i % 13), 0.01)
    assert len(sampler.buckets) <= 50
    assert sampler.buckets[0].low == 100.0 and max(b.high for b in sampler.buckets) == 112.0


def test_time_must_not_go_backwards():
    sampler = Downsampler()
    sampler.add(10, 1.0, None)
    with pytest.raises(ValueError, match="backwards"):
        sampler.add(9, 1.0, None)


@pytest.mark.parametrize("bad", [1, 0, 2.5, True])
def test_bad_sizes_are_refused(bad):
    with pytest.raises(ValueError):
        Downsampler(bad)
