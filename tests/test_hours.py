from datetime import datetime

import pytest

from app.hours import parse, status_at

# 2026-10-17 — суббота
SAT_15 = datetime(2026, 10, 17, 15, 0)
SAT_23 = datetime(2026, 10, 17, 23, 0)
SUN_01 = datetime(2026, 10, 18, 1, 0)
MON_09 = datetime(2026, 10, 19, 9, 0)


@pytest.mark.parametrize("oh, at, state, text", [
    ("Mo-Su 10:00-22:00", SAT_15, "open", "открыто до 22:00"),
    ("Mo-Su 10:00-22:00", SAT_23, "closed", "сегодня уже закрыто"),
    ("Mo-Su 10:00-22:00", MON_09, "closed", "закрыто, откроется в 10:00"),
    ("24/7", SAT_23, "open", "открыто круглосуточно"),
    ("Mo-Su 00:00-24:00", SAT_23, "open", "открыто круглосуточно"),
    ("10:00-22:00", SAT_15, "open", "открыто до 22:00"),
    ("Mo-Fr 09:00-18:00", SAT_15, "closed", "сегодня выходной"),
    ("closed", SAT_15, "closed", "сегодня выходной"),
])
def test_status_basic(oh, at, state, text):
    st = status_at(oh, at)
    assert (st.state, st.text) == (state, text)


def test_after_midnight_belongs_to_previous_day():
    # Суббота 11:00-03:00: в воскресенье в 01:00 ещё открыто — интервал субботы
    st = status_at("Sa 11:00-03:00", SUN_01)
    assert st.state == "open" and st.text == "открыто до 03:00" and st.minutes_left == 120


def test_minutes_left():
    assert status_at("Mo-Su 10:00-15:30", SAT_15).minutes_left == 30


def test_later_rule_overrides_earlier():
    # «Fr,Sa off» перекрывает пятницу из «Mo-Fr»
    assert status_at("Mo-Fr 09:00-18:00; Fr,Sa off", SAT_15).state == "closed"
    assert parse("Mo-Fr 09:00-18:00; Fr,Sa off")[4] == []


def test_comma_after_time_starts_new_rule():
    week = parse("Mo-Fr 09:00-18:00, Sa-Su 10:00-20:00")
    assert week[5] == [(600, 1200)] and week[0] == [(540, 1080)]


@pytest.mark.parametrize("selector, days", [
    ("Su-Th", {6, 0, 1, 2, 3}),
    ("We-Mo", {2, 3, 4, 5, 6, 0}),
    ("Su,Tu-Fr", {6, 1, 2, 3, 4}),
    ("Mo-Su,PH", set(range(7))),
])
def test_day_ranges_wrap_week_and_skip_holidays(selector, days):
    assert set(parse(f"{selector} 10:00-20:00")) == days


def test_spaces_inside_selectors():
    assert set(parse("Su, Tu-Fr 10:00 - 20:00")) == {6, 1, 2, 3, 4}


def test_several_intervals():
    week = parse("Mo-Su 09:00-13:00,14:00-18:00")
    assert week[0] == [(540, 780), (840, 1080)]
    assert status_at("Mo-Su 09:00-13:00,14:00-18:00", datetime(2026, 10, 17, 13, 30)).text == \
        "закрыто, откроется в 14:00"


@pytest.mark.parametrize("oh", [None, "", "Mo-Su,PH 10:00+", "Jan-Mar 10:00-18:00", "по договорённости",
                                "Mo-Su 10:00-22:00 || PH off", "Mo-Su 25:00-26:00"])
def test_unsupported_is_unknown_not_open(oh):
    assert status_at(oh, SAT_15).state == "unknown"


def test_unknown_text_shows_raw_hours():
    assert status_at("Mo-Su,PH 10:00+", SAT_15).text == "часы работы: «Mo-Su,PH 10:00+» — проверьте"


def test_parses_all_real_hours_without_crash():
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "data" / "poi.json"
    if not path.exists():
        pytest.skip("нет data/poi.json")
    hours = [p["opening_hours"] for p in json.loads(path.read_text(encoding="utf-8"))["pois"] if p["opening_hours"]]
    parsed = sum(1 for h in hours if parse(h) is not None)
    assert parsed / len(hours) > 0.95  # почти все реальные строки разбираются
