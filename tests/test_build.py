import json
import math
from pathlib import Path

import pytest

from pipeline import collect_wikidata
from pipeline.build_poi import (M_PER_DEG, clean_label, dedup, distance_m, enrich, match_wikidata, merge,
                                names_match, near, norm_name, short_description, with_defaults)
from pipeline.collect_osm import BBOX
from pipeline.collect_wikidata import fetch_intros, parse_items
from pipeline.landmarks import LANDMARKS, build, resolve

ROOT = Path(__file__).resolve().parent.parent
M = 1 / M_PER_DEG  # градусов широты в одном метре


def poi(id_, name, category="amenity=cafe", north_m=0, **fields):
    base = {"id": id_, "name": name, "name_kk": None, "name_ru": None, "name_en": None,
            "lat": 51.1 + north_m * M, "lon": 71.4, "category": category, "opening_hours": None,
            "website": None, "phone": None, "description": None, "wikidata": None, "wheelchair": None,
            "tags": {}, "sources": ["osm"]}
    return with_defaults({**base, **fields})


def item(q, label_ru=None, north_m=0, **fields):
    base = {"q": q, "lat": 51.1 + north_m * M, "lon": 71.4, "label_ru": label_ru, "label_en": None,
            "label_kk": None, "description_ru": None, "ruwiki_title": None, "ruwiki_url": None,
            "ruwiki_intro": None}
    return {**base, **fields}


def test_distance_m():
    assert distance_m(poi("a", "x"), poi("b", "x", north_m=100)) == pytest.approx(100, abs=0.5)


def test_norm_name_ignores_case_quotes_and_yo():
    assert norm_name("«Ататүрік» саябағы") == norm_name("Ататүрік саябағы")
    assert norm_name("Ёлка") == "елка"


def test_names_match_containment_needs_long_enough_name():
    assert names_match({"megasilkway"}, {"megasilkwayastana"})
    assert not names_match({"кофе"}, {"кофейня"})


def test_near_box_follows_radius():
    a = {"lat": 51.1, "lon": 71.4}
    east_400m = {"lat": 51.1, "lon": 71.4 + 400 / (M_PER_DEG * math.cos(math.radians(51.1)))}
    assert distance_m(a, east_400m) == pytest.approx(400, abs=0.5)
    assert not near(a, east_400m, 300)
    assert near(a, east_400m, 500)


# ---------- склейка ----------

def test_dedup_merges_mega_pair():
    node = poi("osm:node/1", "Мега Силк Вей", "shop=mall", name_en="Mega SilkWay Astana", website="https://msw.kz")
    way = poi("osm:way/2", "Mega Silk Way", "shop=mall", north_m=15)
    pois, groups, check = dedup([node, way])
    assert len(pois) == 1 and len(groups) == 1 and not check
    merged = pois[0]
    assert {merged["id"], *merged["merged_from"]} == {"osm:node/1", "osm:way/2"}
    assert merged["website"] == "https://msw.kz"


def test_dedup_keeps_chain_branches_apart():
    pois, groups, _ = dedup([poi("osm:node/1", "Coffee Like"), poi("osm:node/2", "Coffee Like", north_m=120)])
    assert len(pois) == 2 and not groups


def test_dedup_different_category_goes_to_check_not_merge():
    aquarium = poi("osm:node/1", "Ailand", "tourism=aquarium")
    water_park = poi("osm:node/2", "Ailand", "leisure=water_park", north_m=14)
    pois, groups, check = dedup([aquarium, water_park])
    assert len(pois) == 2 and not groups
    assert check == [(aquarium, water_park)]


def test_dedup_cafe_and_fast_food_are_compatible():
    pois, _, _ = dedup([poi("osm:node/1", "Durum", "amenity=cafe"),
                        poi("osm:node/2", "Durum", "amenity=fast_food", north_m=23)])
    assert len(pois) == 1


def test_dedup_by_wikidata_and_never_across_different_q():
    same_q = [poi("osm:node/1", "Дворец мира", "amenity=community_centre", wikidata="Q1"),
              poi("osm:way/2", "Бейбітшілік сарайы", "amenity=community_centre", north_m=200, wikidata="Q1")]
    assert len(dedup(same_q)[0]) == 1
    other_q = [poi("osm:node/3", "Парк", "leisure=park", wikidata="Q2"),
               poi("osm:node/4", "Парк", "leisure=park", north_m=10, wikidata="Q3")]
    assert len(dedup(other_q)[0]) == 2


def test_dedup_unnamed_point_does_not_bridge_different_q():
    # A(Q1) ~ B(без Q) ~ C(Q2): B уходит к ближайшему соседу, A и C не склеиваются через B
    a = poi("osm:way/1", "Парк", "leisure=park", wikidata="Q1")
    b = poi("osm:node/2", "Парк", "leisure=park", north_m=50)
    c = poi("osm:way/3", "Парк", "leisure=park", north_m=80, wikidata="Q2")
    pois, groups, _ = dedup([a, b, c])
    assert len(pois) == 2
    by_q = {p["wikidata"]: p for p in pois}
    assert by_q["Q2"]["merged_from"] == ["osm:node/2"]  # до C 30 м, до A 50 м
    assert by_q["Q1"]["merged_from"] == []


def test_dedup_ignores_unnamed_points():
    playgrounds = [poi("osm:node/1", None, "leisure=playground"),
                   poi("osm:node/2", None, "leisure=playground", north_m=5)]
    assert len(dedup(playgrounds)[0]) == 2


def test_merge_keeps_richer_base_fills_gaps_and_unions_tags():
    rich = poi("osm:node/1", "Кафе", opening_hours="Mo-Su 10:00-22:00", website="https://a.kz",
               tags={"amenity": "cafe", "name": "Кафе"})
    poor = poi("osm:way/2", "Кафе", phone="+7 700", tags={"amenity": "fast_food", "building": "yes"},
               sources=["osm", "2gis"])
    out = merge([poor, rich])
    assert out["id"] == "osm:node/1" and out["merged_from"] == ["osm:way/2"]
    assert out["phone"] == "+7 700" and out["opening_hours"] == "Mo-Su 10:00-22:00"
    assert out["tags"] == {"amenity": "cafe", "name": "Кафе", "building": "yes"}  # теги основы важнее
    assert out["sources"] == ["osm", "2gis"]


def test_merge_counts_description_once_when_choosing_base():
    # Описание + его источник — это одно поле, а не два: телефон и сайт весят больше
    described = poi("osm:way/1", "Кафе", description="Текст")
    contacts = poi("osm:node/2", "Кафе", phone="+7 700", website="https://a.kz")
    assert merge([described, contacts])["id"] == "osm:node/2"


# ---------- Wikidata ----------

def test_match_wikidata_tag_beats_name():
    tagged = poi("osm:way/1", "Бәйтерек", "historic=monument", wikidata="Q1")
    namesake = poi("osm:node/2", "Байтерек", "amenity=cafe", north_m=10)
    matches, rejected = match_wikidata([tagged, namesake], [item("Q1", "Байтерек")])
    assert [(p["id"], i["q"], how) for p, i, how, _ in matches] == [("osm:way/1", "Q1", "тег")]
    assert not rejected


def test_match_wikidata_rejects_far_tag():
    # Q бренда вместо Q места: объект за 2 км — тег не используем, точку не обогащаем
    cafe = poi("osm:node/1", "Coffee Boom", wikidata="Q1")
    matches, rejected = match_wikidata([cafe], [item("Q1", "Coffee Boom", north_m=2000)])
    assert not matches
    assert [(p["id"], i["q"]) for p, i, *_ in rejected] == [("osm:node/1", "Q1")]


@pytest.mark.parametrize("north_m, expected", [(250, 1), (400, 0)])
def test_match_wikidata_by_name_within_radius(north_m, expected):
    park = poi("osm:way/1", "Парк Жеруйык", "leisure=park")
    matches, _ = match_wikidata([park], [item("Q1", "Парк Жеруйык", north_m=north_m)])
    assert len(matches) == expected


def test_match_wikidata_by_name_takes_closest_point_once():
    near_ = poi("osm:node/1", "Ромашка", north_m=50)
    far = poi("osm:node/2", "Ромашка", north_m=200)
    matches, _ = match_wikidata([far, near_], [item("Q1", "Ромашка")])
    assert [p["id"] for p, *_ in matches] == ["osm:node/1"]


def test_match_wikidata_ignores_disambiguation_in_label():
    mosque = poi("osm:way/1", "Садуакас кажы Гылмани", "amenity=place_of_worship")
    matches, _ = match_wikidata([mosque], [item("Q1", "Садуакас кажы Гылмани (мечеть)")])
    assert len(matches) == 1


def test_enrich_fills_gaps_and_wikipedia_beats_osm_description():
    place = poi("osm:way/1", "Бейбітшілік сарайы", name_ru="Свое", description="62 x 62 m",
                tags={"description": "62 x 62 m"})
    enrich(place, item("Q1", "Чужое (здание)", label_en="Palace of Peace",
                       ruwiki_intro="Дворе́ц ми́ра (каз. Бейбітшілік сарайы) — здание в Астане.\n\nЕщё абзац.",
                       ruwiki_url="https://ru.wikipedia.org/wiki/X"))
    assert place["name_ru"] == "Свое" and place["name_en"] == "Palace of Peace"
    assert place["description"] == "Дворец мира — здание в Астане."
    assert place["description_source"] == "wikipedia"
    assert place["wikipedia"] == "https://ru.wikipedia.org/wiki/X" and place["wikidata"] == "Q1"
    assert place["sources"] == ["osm", "wikidata", "wikipedia"]
    assert place["tags"]["description"] == "62 x 62 m"  # исходный текст OSM не потерян


def test_enrich_wikidata_description_only_when_empty():
    arch = poi("osm:way/1", "Мәңгілік Ел", "historic=monument")
    enrich(arch, item("Q1", "Арка", description_ru="монумент в Астане"))
    assert (arch["description"], arch["description_source"]) == ("Монумент в Астане", "wikidata")
    assert arch["sources"] == ["osm", "wikidata"]

    cafe = poi("osm:node/2", "Кафе", description="своё")
    enrich(cafe, item("Q2", "Кафе", description_ru="кафе"))
    assert (cafe["description"], cafe["description_source"]) == ("своё", "osm")


def test_short_description_initial_is_not_sentence_end():
    # Одно предложение длиннее лимита: если принять «Б.» за конец предложения, текст оборвётся на «Б.»
    sentence = "Парк расположен на проспекте Б. Момышулы, " + "рядом с улицами и площадями, " * 12 + "в центре."
    assert 350 < len(sentence) < 500
    assert short_description(sentence + " Второе предложение.") == sentence


def test_short_description_kazakh_initial_is_not_sentence_end():
    sentence = "Парк расположен на улице Ә. Бөкейхана, " + "рядом с улицами и площадями, " * 12 + "в центре."
    assert 350 < len(sentence) < 500
    assert short_description(sentence + " Второе предложение.") == sentence


def test_short_description_respects_limit():
    out = short_description("Парк Жеруйык — парк. " + "Ещё одно длинное предложение про парк и его историю. " * 10)
    assert out.startswith("Парк Жеруйык — парк. Ещё")
    assert len(out) <= 350 and out.endswith(".")


def test_short_description_cuts_huge_first_sentence():
    out = short_description("Слово " * 200 + "конец.")
    assert len(out) <= 501 and out.endswith("…")


def test_short_description_empty():
    assert short_description(None) is None and short_description("") is None


def test_clean_label():
    assert clean_label("Ак орда (резиденция)") == "Ак орда"
    assert clean_label(None) is None


def test_parse_items_one_item_per_q_and_decodes_article_title():
    rows = [
        {"item": {"value": "http://www.wikidata.org/entity/Q5"}, "loc": {"value": "Point(71.43 51.12)"},
         "ru": {"value": "Байтерек"},
         "article": {"value": "https://ru.wikipedia.org/wiki/%D0%91%D0%B0%D0%B9%D1%82%D0%B5%D1%80%D0%B5%D0%BA_(x)"}},
        {"item": {"value": "http://www.wikidata.org/entity/Q5"}, "loc": {"value": "Point(71.50 51.20)"},
         "en": {"value": "Bayterek"}},
    ]
    [parsed] = parse_items({"results": {"bindings": rows}})
    assert (parsed["q"], parsed["lat"], parsed["lon"]) == ("Q5", 51.12, 71.43)  # наименьшая координата
    assert parsed["label_ru"] == "Байтерек" and parsed["label_en"] == "Bayterek"
    assert parsed["ruwiki_title"] == "Байтерек (x)"
    # порядок строк SPARQL не гарантирован — результат от него не зависит
    assert parse_items({"results": {"bindings": rows[::-1]}}) == [parsed]


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def test_fetch_intros_follows_normalization_redirect_and_continue(monkeypatch):
    # «ак орда» → нормализация «Ак орда» → редирект «Ак Орда»; её текст приходит во втором ответе
    renames = {"normalized": [{"from": "ак орда", "to": "Ак орда"}],
               "redirects": [{"from": "Ак орда", "to": "Ак Орда"}]}
    answers = iter([
        {"continue": {"excontinue": 1, "continue": "||"},
         "query": {**renames, "pages": [{"title": "Байтерек", "extract": "Монумент."}, {"title": "Ак Орда"}]}},
        {"query": {**renames, "pages": [{"title": "Байтерек"}, {"title": "Ак Орда", "extract": "Резиденция."}]}},
    ])
    calls = []

    def fake_get(url, params, **kwargs):
        calls.append(params)
        return _Resp(next(answers))

    monkeypatch.setattr(collect_wikidata.requests, "get", fake_get)
    monkeypatch.setattr(collect_wikidata.time, "sleep", lambda s: None)
    intros, raw = fetch_intros(["Байтерек", "ак орда"])
    assert intros == {"Байтерек": "Монумент.", "ак орда": "Резиденция."}
    assert calls[1]["excontinue"] == 1 and len(raw) == 2


# ---------- ориентиры ----------

def test_landmark_found_by_merged_osm_id():
    merged = poi("osm:node/1", "Mega Silk Way", "shop=mall", merged_from=["osm:way/2"])
    assert resolve("osm:way/2", [merged], [])["poi_id"] == "osm:node/1"


def test_landmark_missing_raises():
    with pytest.raises(LookupError):
        resolve("wikidata:Q999", [], [])


def test_landmarks_resolve_on_real_data():
    poi_path, wd_path = ROOT / "data" / "poi.json", ROOT / "data" / "wikidata_astana.json"
    if not (poi_path.exists() and wd_path.exists()):
        pytest.skip("нет data/poi.json или data/wikidata_astana.json — сначала собрать данные")
    pois = json.loads(poi_path.read_text(encoding="utf-8"))["pois"]
    items = json.loads(wd_path.read_text(encoding="utf-8"))["items"]
    landmarks = build(pois, items)
    assert len({lm["key"] for lm in landmarks}) == len(LANDMARKS)
    south, west, north, east = BBOX
    for lm in landmarks:
        assert south <= lm["lat"] <= north and west <= lm["lon"] <= east, lm["key"]


# ---------- вторая волна: места из Wikidata, фото, путеводитель ----------

from pipeline.build_poi import (attach_guides, category_from_types, latin_tokens,  # noqa: E402
                                place_from_wikidata, similar_names)


@pytest.mark.parametrize("types, category", [
    (["Q481289"], "tourism=attraction"),            # официальная резиденция — Ак Орда
    (["Q41176", "Q27686"], None),                   # «здание», но гостиница — не место для прогулки
    (["Q32815", "Q41176"], "amenity=place_of_worship"),  # мечеть важнее «здания»
    (["Q130212458"], None),                          # село
    (["Q999999999"], None),                          # незнакомый тип — не добавляем
])
def test_category_from_wikidata_types(types, category):
    assert category_from_types(types) == category


def test_wikidata_place_needs_known_type_and_article():
    akorda = item("Q413579", "Ак орда (резиденция)", ruwiki_url="https://ru.wikipedia.org/wiki/Ак_Орда",
                  ruwiki_intro="Ак Орда — резиденция президента. Построена в 2004 году.")
    extra = {"types": [{"q": "Q481289"}], "image": {"thumb": "https://upload.wikimedia.org/x.jpg", "page": "p",
                                                    "artist": "A", "license": "CC BY-SA 4.0", "license_url": None}}
    p = place_from_wikidata(akorda, extra)
    assert p["id"] == "wikidata:Q413579" and p["name"] == "Ак орда" and p["category"] == "tourism=attraction"
    assert p["description_source"] == "wikipedia" and p["image"]["license"] == "CC BY-SA 4.0"
    assert set(p["sources"]) == {"wikidata", "wikipedia", "commons"}
    assert place_from_wikidata(item("Q1", "Без статьи"), {"types": [{"q": "Q481289"}]}) is None


def test_english_article_when_no_russian():
    p = poi("a", "Talan Towers", "tourism=attraction")
    enrich(p, item("Q2", "Talan Towers"), {"enwiki_url": "https://en.wikipedia.org/wiki/Talan_Towers",
                                           "enwiki_intro": "Talan Towers is a complex of two towers."})
    assert p["description"] == "Talan Towers is a complex of two towers." and p["wikipedia"].startswith("https://en.")


@pytest.mark.parametrize("a, b, same", [
    ("Presidential Park", "Президентский парк", True),
    ("Expo 2017 buildings", "EXPO", True),
    ("Қазақстан Республикасы Парламентінің Сенаты", "«Қазақстан» орталық концерт залы", False),  # общее слово
    ("Grand Mosque of Astana", "Мечеть Хазрет Султан", False),               # «мечеть» — тоже
])
def test_similar_names_across_languages(a, b, same):
    assert similar_names(latin_tokens(a), latin_tokens(b)) == same


def test_guides_attach_by_wikidata_and_add_missing_sights():
    pyramid = poi("osm:way/1", "Бейбітшілік пен келісім сарайы", "tourism=attraction", wikidata="Q2119741")
    park = poi("osm:way/2", "Президентский парк", "leisure=park", north_m=900)
    listings = [
        {"type": "see", "name": "Palace of Peace", "alt": None, "wikidata": "Q2119741", "lat": 51.2, "lon": 71.4,
         "content": "Pyramid by Norman Foster."},
        {"type": "see", "name": "Presidential Park", "alt": None, "wikidata": None, "lat": 51.1, "lon": 71.4,
         "content": "Big park."},                                     # тот же парк по-английски — не дубль
        {"type": "see", "name": "Triumph of Astana", "alt": None, "wikidata": "Q475139", "lat": 51.15, "lon": 71.4,
         "content": "Skyscraper."},
    ]
    attached, added = attach_guides([pyramid, park], listings, "https://en.wikivoyage.org/wiki/Astana",
                                    ru_labels={"Q475139": "Триумф Астана"})
    assert attached == 1 and pyramid["guide"]["text"] == "Pyramid by Norman Foster." and "wikivoyage" in pyramid["sources"]
    assert [p["id"] for p in added] == ["wikivoyage:Q475139"] and added[0]["guide"]["text"] == "Skyscraper."
    assert added[0]["name_ru"] == "Триумф Астана"  # в русском интерфейсе — не «Triumph of Astana»
