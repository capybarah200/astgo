import pytest

from pipeline import collect_osm
from pipeline.collect_osm import BBOX, SELECTORS, build_query, normalize, primary_category
from pipeline.coverage import FIELDS, group_of


def test_primary_category_prefers_tourism_over_amenity():
    # Музей-кафе: для рекомендаций важнее, что это музей
    assert primary_category({"amenity": "cafe", "tourism": "museum"}) == "tourism=museum"


def test_primary_category_skips_unselected_values():
    # Ресторан в отеле: отели не собираем, категория — ресторан
    assert primary_category({"tourism": "hotel", "amenity": "restaurant"}) == "amenity=restaurant"


def test_every_selector_value_has_group():
    for key, values in SELECTORS.items():
        if key == "historic":
            continue
        for value in values.split("|"):
            assert group_of(f"{key}={value}") != "Прочее", f"{key}={value} не попал ни в одну группу"


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


@pytest.mark.parametrize("bad", [
    {"remark": "runtime error: Query timed out", "elements": [{"type": "node"}]},
    {"elements": []},
])
def test_fetch_rejects_partial_or_empty_response(monkeypatch, bad):
    good = {"elements": [{"type": "node", "id": 1}]}
    answers = iter([bad, good])
    monkeypatch.setattr(collect_osm.requests, "post", lambda *a, **k: _Resp(next(answers)))
    monkeypatch.setattr(collect_osm.time, "sleep", lambda s: None)
    assert collect_osm.fetch("q") == good  # плохой ответ пропущен, взят следующий сервер


def test_primary_category_none_without_known_keys():
    assert primary_category({"name": "Что-то", "building": "yes"}) is None


def test_normalize_node():
    poi = normalize({
        "type": "node", "id": 42, "lat": 51.1, "lon": 71.4,
        "tags": {"amenity": "cafe", "name": "Коктем", "contact:website": "https://k.kz",
                 "description": "en", "description:ru": "ру"},
    })
    assert poi["id"] == "osm:node/42"
    assert (poi["lat"], poi["lon"]) == (51.1, 71.4)
    assert poi["category"] == "amenity=cafe"
    assert poi["website"] == "https://k.kz"
    assert poi["description"] == "ру"
    assert poi["sources"] == ["osm"]


def test_normalize_way_uses_center():
    poi = normalize({
        "type": "way", "id": 7, "center": {"lat": 51.2, "lon": 71.5},
        "tags": {"leisure": "park"},
    })
    assert poi["id"] == "osm:way/7"
    assert (poi["lat"], poi["lon"]) == (51.2, 71.5)
    assert poi["name"] is None


def test_normalize_skips_without_coords_or_category():
    assert normalize({"type": "relation", "id": 1, "tags": {"leisure": "park"}}) is None
    assert normalize({"type": "node", "id": 2, "lat": 51, "lon": 71, "tags": {"building": "yes"}}) is None


def test_build_query_covers_bbox_and_all_keys():
    query = build_query()
    assert ",".join(str(x) for x in BBOX) in query
    for key in SELECTORS:
        assert f'nwr["{key}"' in query
    assert query.rstrip().endswith("out center tags;")


def test_group_of():
    assert group_of("historic=memorial") == "Достопримечательности"
    assert group_of("leisure=playground") == "Для детей"
    assert group_of("amenity=cafe") == "Кафе и еда"
    assert group_of("amenity=unknown_thing") == "Прочее"


def test_coverage_field_checks():
    assert FIELDS["розетки"]({"socket:type2": "1"})
    assert not FIELDS["розетки"]({"amenity": "cafe"})
    assert FIELDS["сайт"]({"contact:website": "x"})
    assert FIELDS["описание"]({"description:kk": "x"})


def test_areas_grouped_by_name_split_by_distance_and_numbers_dropped():
    from pipeline.collect_areas import normalize as normalize_areas

    def way(id_, lat, tags):
        return {"type": "way", "id": id_, "center": {"lat": lat, "lon": 71.4}, "tags": tags}

    street = {"highway": "residential", "name": "Кенесары көшесі"}
    els = [way(1, 51.100, street), way(2, 51.101, street), way(3, 51.102, street),  # одна улица из трёх отрезков
           way(4, 51.200, street),                                                   # тёзка в 11 км — другая улица
           way(5, 51.150, {"landuse": "residential", "name": "Жағалау шағын ауданы"}),
           way(6, 51.150, {"building": "apartments", "name": "12А"})]                 # номер дома — не название
    areas = normalize_areas(els)
    streets = sorted((a for a in areas if a["kind"] == "street"), key=lambda a: a["lat"])
    assert [a["parts"] for a in streets] == [3, 1] and len(streets[0]["points"]) == 3
    assert {(a["kind"], a["name"]) for a in areas if a["kind"] != "street"} == {("district", "Жағалау шағын ауданы")}
