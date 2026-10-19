from datetime import datetime

import pytest
from helpers import M, make_poi as poi

from app.engine import ASTANA_TZ, Catalog, Query, display_name, is_options_mode, open_status, summary, walk_min

SAT_15 = datetime(2026, 10, 17, 15, 0, tzinfo=ASTANA_TZ)


def query(**kw):
    return Query(lat=51.1, lon=71.4, at=kw.pop("at", SAT_15), **kw)


def ids(cands):
    return {c["poi"]["id"] for c in cands}


def test_walk_min_uses_detour_and_speed():
    assert walk_min(750) == 13             # 750 × 1,3 / 75 = 13
    assert walk_min(750, with_kids=True) == 20  # 975 / 50 = 19,5 → 20
    assert walk_min(0) == 1


def test_candidates_respect_time_budget():
    near = poi("n", "Парк у дома", "leisure=park", north_m=300)
    far = poi("f", "Дальний парк", "leisure=park", north_m=3000)
    assert ids(Catalog([near, far]).candidates(query(time_budget_min=60))) == {"n"}


def test_closed_place_excluded_and_closing_soon_flagged():
    closed = poi("c", "Музей", "tourism=museum", opening_hours="Mo-Su 10:00-12:00")
    too_late = poi("t", "Кафе", "amenity=cafe", opening_hours="Mo-Su 10:00-15:10")  # 9 мин после прихода < 20
    soon = poi("s", "Кофейня", "amenity=cafe", opening_hours="Mo-Su 10:00-15:30")   # 29 мин: успеть, но впритык
    cands = Catalog([closed, too_late, soon]).candidates(query())
    assert ids(cands) == {"s"}
    assert cands[0]["closes_soon"] and cands[0]["status"].minutes_left == 29  # считаем от прихода (1 мин пути)


def test_route_checks_hours_at_arrival_not_at_start():
    # Старт 15:00. Поодиночке оба открыты, но вместе — ни в каком порядке:
    # музей первым → к достопримечательности придём после 16:00, когда она уже закрыта;
    # достопримечательность первой → в музей придём в 15:22, а до закрытия 16:05 меньше 60 мин визита
    museum = poi("m", "Музей", "tourism=museum", north_m=10, opening_hours="Mo-Su 10:00-16:05")
    sight = poi("s", "Башня", "tourism=attraction", north_m=20, opening_hours="Mo-Su 10:00-15:25")
    q = query(time_budget_min=180)
    cat = Catalog([museum, sight])
    cands = cat.candidates(q)
    assert ids(cands) == {"m", "s"}            # каждое место по отдельности успеваем
    assert len(cat.route(q, cands)) == 1       # а вместе — нет
    late = poi("s2", "Башня", "tourism=attraction", north_m=20, opening_hours="Mo-Su 10:00-20:00")
    cat = Catalog([museum, late])
    assert len(cat.route(q, cat.candidates(q))) == 2


def test_daytime_place_with_unknown_hours_not_offered_at_night():
    gallery = poi("g", "Галерея", "tourism=gallery")                       # часы неизвестны
    late = poi("b", "Бар", "amenity=bar")                                    # часы неизвестны, но вечерний
    known = poi("m", "Музей", "tourism=museum", opening_hours="Mo-Su 10:00-24:00")  # часы известны
    night = datetime(2026, 10, 17, 22, 0, tzinfo=ASTANA_TZ)
    assert ids(Catalog([gallery, late, known]).candidates(query(at=night, time_budget_min=120))) == {"b", "m"}
    assert "g" in ids(Catalog([gallery]).candidates(query(time_budget_min=120)))  # днём — можно


def test_before_flight_needs_return_trip_and_reserve():
    # 120 мин до вылета − 60 в запасе = 60; туда-обратно по 21 мин + кафе 20 = 62 → не успеть
    far = poi("f", "Кафе", "amenity=cafe", north_m=1200)
    near = poi("n", "Кофейня", "amenity=cafe", north_m=600)
    cands = Catalog([far, near]).candidates(query(time_budget_min=120, before_flight=True))
    assert ids(cands) == {"n"}


def test_kids_exclude_bars_and_allow_unnamed_playgrounds():
    pois = [poi("bar", "Бар", "amenity=bar"), poi("pg", None, "leisure=playground"),
            poi("pk", "Парк", "leisure=park")]
    assert ids(Catalog(pois).candidates(query(with_kids=True, time_budget_min=120))) == {"pg", "pk"}
    # без ребёнка безымянная площадка не нужна, а бар — можно
    assert ids(Catalog(pois).candidates(query(time_budget_min=120))) == {"bar", "pk"}


def test_unnamed_display_name_is_honest():
    assert display_name(poi("pg", None, "leisure=playground")) == "Детская площадка (без названия в данных)"


def test_ordinary_mosque_not_recommended_but_famous_is():
    ordinary = poi("o", "Мечеть", "amenity=place_of_worship")
    famous = poi("f", "Хазрет Султан", "amenity=place_of_worship", wikidata="Q4494668")
    assert ids(Catalog([ordinary, famous]).candidates(query())) == {"f"}


def test_start_point_itself_not_recommended():
    mall = poi("mall", "Mega Silk Way", "shop=mall")
    assert not Catalog([mall]).candidates(query(start_poi_id="mall"))


def test_outdoor_without_hours_is_not_claimed_open():
    st = open_status(poi("p", "Парк", "leisure=park"), SAT_15)
    assert st.state == "outdoor"
    assert open_status(poi("m", "Музей", "tourism=museum"), SAT_15).state == "unknown"


def test_route_up_to_three_distinct_categories_within_time():
    pois = [poi(f"p{i}", f"Парк {i}", "leisure=park", north_m=100 * i) for i in range(4)] + \
           [poi("m", "Монумент", "historic=monument", north_m=150), poi("a", "Скульптура", "tourism=artwork", north_m=50)]
    q = query(time_budget_min=120)
    cat = Catalog(pois)
    route = cat.route(q, cat.candidates(q))
    assert 1 <= len(route) <= 3
    assert len({c["poi"]["category"] for c in route}) == len(route)
    assert summary(q, route)["fits_budget"]


@pytest.mark.parametrize("budget, total", [(60, [35, 60]), (120, [35, 65]), (30, [35, 35])])
def test_route_total_not_longer_than_budget(budget, total):
    # «у меня 60 мин» → не «маршрут на 35–65 мин»: задержаться можно до конца бюджета, не дольше
    chosen = [{"poi": poi("p", "Парк", "leisure=park"), "leg_min": 5, "visit": (30, 60)}]
    assert summary(query(time_budget_min=budget), chosen)["total_min"] == total


def test_route_skips_same_name_nearby():
    # Ailand: океанариум и аквапарк в 14 м — в выдаче одно место
    pois = [poi("a1", "Ailand", "tourism=aquarium"), poi("a2", "Ailand", "leisure=water_park", north_m=14)]
    q = query(time_budget_min=300, with_kids=True)
    cat = Catalog(pois)
    assert len(cat.route(q, cat.candidates(q))) == 1


def test_route_empty_when_nothing_fits():
    q = query(time_budget_min=15)
    cat = Catalog([poi("m", "Музей", "tourism=museum")])
    assert cat.route(q, cat.candidates(q)) == []


def test_options_mode_for_coffee():
    pois = [poi(f"c{i}", f"Кафе {i}", "amenity=cafe", north_m=100 * i) for i in range(5)]
    q = query(wants=["coffee"])
    cat = Catalog(pois)
    chosen = cat.options(q, cat.candidates(q))
    assert len(chosen) == 3
    assert all(c["leg_min"] == c["walk_min"] for c in chosen)  # каждый вариант — от старта
    assert summary(q, chosen) == {"mode": "options", "usable_min": 60, "destination": None}


def test_chain_detection():
    pois = [poi(f"k{i}", "KFC", "amenity=fast_food", north_m=100 * i) for i in range(3)] + [poi("x", "Уникум", "amenity=cafe")]
    cat = Catalog(pois)
    assert cat.is_chain(pois[0]) and not cat.is_chain(pois[-1])


@pytest.mark.parametrize("category, kw, expected", [
    ("leisure=playground", {"with_kids": True, "interests": ["Культура"]}, True),   # детское — подходит
    ("tourism=museum", {"with_kids": True, "interests": ["Культура"]}, True),
    ("historic=memorial", {"with_kids": True, "interests": ["Культура"]}, False),
    ("amenity=restaurant", {"wants": ["coffee"]}, False),
    ("amenity=cafe", {"wants": ["coffee"]}, True),
    ("leisure=park", {"wants": ["night"]}, False),
    ("leisure=park", {}, True),
])
def test_matches_focus(category, kw, expected):
    assert Catalog([]).matches_focus({"poi": poi("x", "X", category)}, query(**kw)) is expected


def test_route_to_destination_goes_along_the_way_and_ends_there():
    # старт на юге, цель в 3 км к северу; по пути парк и монумент, в стороне — музей
    dest_lat = 51.1 + 3000 * M
    pois = [poi("park", "Парк Север", "leisure=park", north_m=1000),
            poi("mon", "Монумент", "historic=monument", north_m=2000),
            poi("museum", "Музей", "tourism=museum", north_m=-1500, opening_hours="24/7"),
            poi("at_goal", "Скульптура у цели", "tourism=artwork", north_m=2950)]
    q = query(dest_lat=dest_lat, dest_lon=71.4, dest_label="Цель")
    q.time_budget_min = q.direct_min + 60
    cat = Catalog(pois)
    route = cat.route(q, cat.candidates(q))
    assert [c["poi"]["id"] for c in route] == ["park", "mon"]  # по ходу движения, без крюка на юг
    s = summary(q, route)
    assert s["destination"] == "Цель" and s["return_min"] == walk_min(1000)  # от монумента до цели
    assert s["total_min"][0] <= q.usable_min


def test_place_at_destination_is_not_a_stop():
    q = query(dest_lat=51.1 + 3000 * M, dest_lon=71.4, dest_label="Цель", time_budget_min=200)
    assert not Catalog([poi("at_goal", "Скульптура у цели", "tourism=artwork", north_m=2950)]).candidates(q)


def test_order_accounts_for_last_leg_to_destination():
    # без учёта пути до цели короче «сначала вперёд (400 м), потом назад (600 м за стартом)» —
    # и маршрут закончился бы в 3,6 км от цели. С учётом — сначала место позади, потом вперёд к цели
    behind = poi("behind", "Монумент", "historic=monument", north_m=-600)
    ahead = poi("ahead", "Парк", "leisure=park", north_m=400)
    q = query(dest_lat=51.1 + 3000 * M, dest_lon=71.4, dest_label="Цель", time_budget_min=200)
    cat = Catalog([behind, ahead])
    cands = {c["poi"]["id"]: c for c in cat.candidates(q)}
    plan = cat._plan(q, (cands["ahead"], cands["behind"]))
    assert [c["poi"]["id"] for c, *_ in plan] == ["behind", "ahead"]


def test_beautiful_route_to_destination_can_take_two_parks_and_three_stops():
    # «по красивым местам до цели»: два парка по пути — это то, что просили; парк по пути — проход, 15–30 мин
    pois = [poi("p1", "Ботанический сад", "leisure=park", north_m=800, wikidata="Q1"),
            poi("p2", "Линейный парк", "leisure=park", north_m=2000, wikidata="Q2"),
            poi("arch", "Арка", "tourism=attraction", north_m=1400, wikidata="Q3")]
    q = query(dest_lat=51.1 + 3000 * M, dest_lon=71.4, dest_label="Цель",
              interests=["Парки", "Достопримечательности"])
    q.time_budget_min = q.direct_min + 60
    cat = Catalog(pois)
    cands = cat.candidates(q)
    assert {c["poi"]["id"]: c["visit"] for c in cands}["p1"] == (15, 30)
    assert [c["poi"]["id"] for c in cat.route(q, cands)] == ["p1", "arch", "p2"]


def test_night_does_not_demote_parks_that_were_asked_for():
    park = poi("p", "Парк", "leisure=park")
    night = datetime(2026, 10, 17, 23, 20, tzinfo=ASTANA_TZ)
    cat = Catalog([park])
    assert cat.interest(park, query(at=night, interests=["Парки"]))[0] == 1.0
    assert cat.interest(park, query(at=night))[0] < cat.interest(park, query())[0]  # не просили — ночью ниже


def test_tour_prefers_notable_places():
    famous = poi("f", "Ак Орда", "tourism=attraction", wikidata="Q1")
    plain = poi("p", "Скульптура", "tourism=artwork")
    cat = Catalog([famous, plain])
    q = query(wants=["tour"])
    assert cat.interest(famous, q) == (1.0, ["tour"])
    assert cat.interest(plain, q)[0] < cat.interest(plain, query())[0]


def test_seen_places_lose_to_new_ones_and_seed_only_reorders_close_scores():
    pois = [poi("a", "Парк А", "leisure=park", north_m=100), poi("b", "Парк Б", "leisure=park", north_m=100)]
    cat = Catalog(pois)
    base = {c["poi"]["id"]: c["score"] for c in cat.candidates(query())}
    seen = {c["poi"]["id"]: c["score"] for c in cat.candidates(query(seen_ids=["a"]))}
    assert seen["a"] < base["a"] and seen["b"] == base["b"]
    jitter = {c["poi"]["id"]: c["score"] for c in cat.candidates(query(seed=7))}
    assert all(abs(jitter[k] - base[k]) <= 0.03 for k in base)
    assert jitter == {c["poi"]["id"]: c["score"] for c in cat.candidates(query(seed=7))}  # один разговор — один порядок


def test_sight_at_start_is_a_stop_but_mall_is_not():
    tower = poi("t", "Байтерек", "historic=monument", wikidata="Q1")
    mall = poi("m", "Хан Шатыр", "shop=mall", north_m=10, wikidata="Q2")
    cat = Catalog([tower, mall])
    assert {c["poi"]["id"] for c in cat.candidates(query(start_poi_id="t", start_label="Байтерек", time_budget_min=120))} == {"m", "t"}
    assert "m" not in {c["poi"]["id"] for c in cat.candidates(query(start_poi_id="m", start_label="Хан Шатыр", time_budget_min=120))}


def test_place_with_the_start_name_is_not_recommended():
    a = poi("a", "Президентский парк", "leisure=park")
    b = poi("b", "Президентский парк", "leisure=park", north_m=700)
    ids = {c["poi"]["id"] for c in Catalog([a, b]).candidates(query(start_poi_id="a", start_label="Президентский парк"))}
    assert "b" not in ids


def test_food_bars_and_laptop_are_pick_one_and_visit_length_does_not_block():
    bar = poi("bar", "Brewery", "amenity=bar", north_m=470, opening_hours="24/7")
    q = query(interests=["Бары и ночь"])  # час по умолчанию, бар от 60 мин — раньше «ничего не укладывается»
    assert is_options_mode(q) and [c["poi"]["id"] for c in Catalog([bar]).candidates(q)] == ["bar"]
    assert is_options_mode(query(interests=["Кафе и еда"])) and is_options_mode(query(wants=["socket"]))
    assert not is_options_mode(query(interests=["Кафе и еда", "Парки"]))


def test_museum_request_prefers_museum_over_library():
    museum = poi("mu", "Музей", "tourism=museum", north_m=600, opening_hours="24/7")
    library = poi("li", "Библиотека", "amenity=library", north_m=100, opening_hours="24/7")
    q = query(interests=["Культура"], time_budget_min=120)
    cat = Catalog([museum, library])
    assert cat.interest(library, q)[0] < cat.interest(museum, q)[0]


def test_tour_from_the_center_can_start_at_baiterek_itself():
    # «маршрут от Байтерека» (старт без id): сам Байтерек — первая остановка, а не «то же название»
    tower = poi("bt", "Байтерек", "historic=monument", wikidata="Q1")
    other = poi("x", "Сквер", "leisure=park", north_m=300)
    cat = Catalog([tower, other])
    assert "bt" in {c["poi"]["id"] for c in cat.candidates(query(start_label="Байтерек"))}
    assert "bt" not in {c["poi"]["id"] for c in cat.candidates(query(start_label="Байтерек", start_poi_id="x"))}


def test_display_name_starts_with_capital():
    assert display_name(poi("m", 'мечеть "Хазрет Султан"', "amenity=place_of_worship")) == 'Мечеть "Хазрет Султан"'
