import copy
import re
from html.parser import HTMLParser

import pytest

from riskmon.dashboard import MAX_TABLE_ROWS, nice_ticks, render, time_ticks
from riskmon.generate import FeedSpec, generate
from riskmon.replay import replay

VOID = {"meta", "br", "img", "input", "link", "hr", "line", "circle", "rect", "path", "polygon", "polyline"}


class Structure(HTMLParser):
    """Records tags, attributes and text, and checks that every element is closed in order."""

    def __init__(self):
        super().__init__()
        self.stack, self.tags, self.attrs, self.text, self.errors = [], [], [], [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs += attrs
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs += attrs

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack.pop() != tag:
            self.errors.append(tag)

    def handle_data(self, data):
        self.text.append(data)


def parse(page):
    doc = Structure()
    doc.feed(page)
    doc.close()
    assert doc.errors == [] and doc.stack == [], (doc.errors, doc.stack)
    return doc


@pytest.fixture(scope="module")
def report():
    events, truth = generate(FeedSpec())
    return {"feed": "out/feed.jsonl", **replay(events, jumps=truth.jumps).report(True)}


def test_the_page_is_well_formed_and_self_contained(report):
    page = render(report)
    doc = parse(page)
    assert "script" not in doc.tags and "iframe" not in doc.tags and "img" not in doc.tags
    names = {name for name, _ in doc.attrs}
    assert not names & {"src", "href", "onload", "onclick", "onerror"}
    assert not re.search(r"https?:|url\(|//[a-z]", page)   # nothing loaded from anywhere
    assert "default-src 'none'" in page


def test_it_shows_the_headline_numbers_charts_and_tables(report):
    page = render(report)
    text = " ".join(parse(page).text)
    for expected in ("7,276", "7,191", "19 of 20", "0 false alerts", "Synthetic data", "SYNA", "SYNB",
                     "1 more suppressed by the cooldown", "cooldown"):
        assert expected in text, expected
    # One price chart and one deviation chart per symbol, each in a wide and a narrow version.
    assert page.count('<svg class="chart wide"') == page.count('<svg class="chart narrow"') == 4
    # Every alert is marked on both versions of its symbol's chart, with a title giving its numbers.
    assert page.count("<title>Alert: ") == 2 * len(report["alerts"]["list"]) == 38
    assert page.count("<title>Missed jump: ") == 2


def test_every_alert_has_a_row_in_the_table(report):
    rows = re.findall(r"<tr><td class=\"text\">\d\d:\d\d:\d\d</td><td class=\"text\">(SYN[AB])</td><td>(\d+)</td>", render(report))
    assert sorted(rows) == sorted((a["symbol"], str(a["seq"])) for a in report["alerts"]["list"])


def test_text_from_the_feed_or_the_command_line_is_escaped(report):
    hostile = copy.deepcopy(report)
    hostile["feed"] = '<script>alert("feed")</script>'
    hostile["series"]['<img src=x onerror="alert(1)">'] = hostile["series"].pop("SYNA")
    hostile["symbols"]['<img src=x onerror="alert(1)">'] = hostile["symbols"].pop("SYNA")
    for alert in hostile["alerts"]["list"]:
        if alert["symbol"] == "SYNA":
            alert["symbol"] = '<img src=x onerror="alert(1)">'
    page = render(hostile)
    doc = parse(page)
    assert "script" not in doc.tags and "img" not in doc.tags
    assert "&lt;script&gt;" in page and "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in page


def test_without_a_truth_file_there_is_no_scoring(report):
    plain = {**report, "evaluation": None, "synthetic": None}
    text = " ".join(parse(render(plain)).text)
    assert "Injected jumps detected" not in text and "Missed jumps" not in text and "Synthetic data" not in text
    assert "Missed jump: " not in render(plain)


def test_a_feed_not_marked_synthetic_says_so(report):
    assert "Not marked synthetic" in render({**report, "synthetic": False})


def test_an_empty_replay_renders():
    page = render({"feed": "empty.jsonl", **replay([]).report()})
    assert "nothing to chart" in page and "No alerts fired" in page
    parse(page)


def test_a_symbol_with_one_price_renders(report):
    from riskmon.events import Event

    page = render(replay([Event(time=1_000, seq=0, symbol="ONE", price=5.0)]).report())
    assert "Not enough returns for a standard deviation" in page
    parse(page)


def test_a_missed_jump_without_a_time_is_listed_but_not_drawn(report):
    edited = copy.deepcopy(report)
    for outcome in edited["evaluation"]["outcomes"]:
        if outcome["result"] == "missed":
            outcome["time"] = None
    page = render(edited)
    assert "Missed jump: " not in page and "<td>cooldown</td>" not in page and "cooldown</td>" in page


def test_long_alert_lists_are_cut_with_a_note(report):
    many = copy.deepcopy(report)
    alert = many["alerts"]["list"][0]
    many["alerts"]["list"] = [dict(alert, seq=i) for i in range(MAX_TABLE_ROWS + 5)]
    page = render(many)
    assert f"the first {MAX_TABLE_ROWS} of {MAX_TABLE_ROWS + 5:,}" in page
    assert len(re.findall(r"<tr><td class=\"text\">\d\d:\d\d:\d\d</td>", page)) == MAX_TABLE_ROWS


@pytest.mark.parametrize("low, high", [(96.4, 102.6), (0.0, 7.3), (1e-6, 3e-6), (-5.0, 5.0), (99.99, 100.01)])
def test_ticks_start_and_end_on_round_values_around_the_range(low, high):
    ticks = nice_ticks(low, high)
    assert ticks[0] <= low and ticks[-1] >= high
    steps = {round(b - a, 12) for a, b in zip(ticks, ticks[1:], strict=False)}
    assert len(steps) == 1
    step = steps.pop()
    mantissa = step / 10 ** __import__("math").floor(__import__("math").log10(step))
    assert round(mantissa, 9) in (1, 2, 2.5, 5, 10)
    assert 3 <= len(ticks) <= 12


def test_degenerate_ranges_give_one_tick():
    assert nice_ticks(5.0, 5.0) == [5.0]


def test_time_ticks_fall_on_round_utc_boundaries():
    start = 1_767_225_600_000 + 7_000             # 2026-01-01 00:00:07 UTC
    ticks, step = time_ticks(start, start + 3_600_000)
    assert step == 10 * 60_000                    # six ticks cover the hour
    assert all(t % step == 0 for t in ticks) and ticks[0] >= start
    assert time_ticks(start, start)[1] == 1_000


def _series(points):
    return {"origin": 0, "width_ms": 1_000, "points": [
        {"k": k, "t": k * 1_000, "low": price, "high": price, "last": price, "std": std, "n": 1}
        for k, price, std in points]}


def test_lines_break_where_there_were_no_events_or_no_deviation():
    from riskmon.dashboard import WIDE, _price_chart, _runs, _std_chart

    data = _series([(0, 100.0, 0.001), (1, 101.0, None), (2, 102.0, 0.002), (5, 99.0, 0.003), (6, 98.0, 0.003)])
    assert [[p["k"] for p in run] for run in _runs(data["points"], "last")] == [[0, 1, 2], [5, 6]]
    assert [[p["k"] for p in run] for run in _runs(data["points"], "std")] == [[0], [2], [5, 6]]
    price = _price_chart("A", data, [], [], WIDE)
    assert price.count('stroke="var(--series)" stroke-width="2"') == 2      # one line per run
    std = _std_chart("A", data, 0, 6_000, WIDE)
    assert std.count('stroke="var(--series)" stroke-width="2"') == 3


def test_an_alert_points_the_way_the_price_moved():
    from riskmon.dashboard import WIDE, _price_chart

    data = _series([(k, 100.0 + k, 0.001) for k in range(10)])
    base = {"symbol": "A", "seq": 1, "price": 104.0, "mean": 0.0, "std": 0.001, "count": 40}
    up = dict(base, time=4_000, log_return=0.01, z=10.0)
    down = dict(base, time=6_000, log_return=-0.01, z=-10.0)
    page = _price_chart("A", data, [up, down], [], WIDE)
    tips = re.findall(r'<polygon points="[\d.]+,([\d.]+) [\d.]+,([\d.]+)', page)
    (up_apex, up_base), (down_apex, down_base) = [(float(a), float(b)) for a, b in tips]
    assert up_apex < up_base and down_apex > down_base     # SVG y grows downwards


def test_the_alert_table_says_which_alerts_were_false(report):
    edited = copy.deepcopy(report)
    first = edited["alerts"]["list"][0]
    edited["evaluation"]["false_alert_list"] = [first]
    page = render(edited)
    assert page.count("<td class=\"text\">false alert</td>") == 1
    assert page.count("<td class=\"text\">injected jump</td>") == len(edited["alerts"]["list"]) - 1
    unscored = render({**report, "evaluation": None})
    assert "injected jump</td>" not in unscored and "false alert</td>" not in unscored


def test_counts_in_sentences_are_singular_or_plural(report):
    page = render(report)
    assert "7 alerts · 1 missed jump · " in page and "12 alerts · 0 missed jumps · " in page


def test_the_price_axis_ends_on_ticks_at_the_plot_edges(report):
    from riskmon.dashboard import BOTTOM, PRICE_HEIGHT, TOP, WIDE, _price_chart

    page = _price_chart("SYNA", report["series"]["SYNA"], [], [], WIDE)
    grid = {float(y) for y in re.findall(r'y1="([\d.]+)" y2="\1" stroke="var\(--grid\)"', page)}
    assert {float(TOP), float(PRICE_HEIGHT - BOTTOM)} <= grid


def test_the_settings_are_stated(report):
    text = " ".join(parse(render(report)).text)
    assert "Window 300 s · allowed lateness 2 s · alert at |z| ≥ 5 after 30 returns, then 30 s quiet" in text


def test_symbols_are_listed_by_name_not_by_first_release(report):
    page = render(report)                       # SYNB's first price is released before SYNA's
    assert list(report["series"]) == ["SYNB", "SYNA"]
    assert page.index("<h2>SYNA</h2>") < page.index("<h2>SYNB</h2>")
    table = page[page.index("Counts per symbol."):]
    assert table.index(">SYNA<") < table.index(">SYNB<")
