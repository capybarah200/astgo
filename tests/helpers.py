"""Общие тестовые данные: место в схеме POI и маленький набор мест у условного Байтерека."""

from pipeline.build_poi import M_PER_DEG, with_defaults

M = 1 / M_PER_DEG  # градусов широты в одном метре

LANDMARKS = [
    {"key": "baiterek", "label": "Байтерек", "lat": 51.1, "lon": 71.4, "poi_id": None},
    {"key": "expo", "label": "Экспо", "lat": 51.09, "lon": 71.42, "poi_id": None},
]


def make_poi(id_, name, category, north_m=0, lat=51.1, **fields):
    base = {"id": id_, "name": name, "name_kk": None, "name_ru": None, "name_en": None,
            "lat": lat + north_m * M, "lon": 71.4, "category": category, "opening_hours": None,
            "website": None, "phone": None, "description": None, "wikidata": None, "wheelchair": None,
            "tags": {}, "sources": ["osm"]}
    return with_defaults({**base, **fields})


POIS = [make_poi("park", "Парк", "leisure=park", 300), make_poi("mon", "Монумент", "historic=monument", 200),
        make_poi("cafe1", "Кофейня", "amenity=cafe", 100, opening_hours="24/7"),
        make_poi("cafe2", "Кафе Два", "amenity=cafe", 150, opening_hours="24/7"),
        make_poi("pg", None, "leisure=playground", 120),
        make_poi("art", "Скульптура Кобызшы", "tourism=artwork", 60),
        make_poi("garden", "Сквер Дружбы", "leisure=garden", 250),
        make_poi("opera", "Астана Опера", "amenity=theatre", 2000, wikidata="Q21371563")]
