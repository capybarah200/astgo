import pytest
from helpers import make_poi

from app.engine import Catalog
from app.places import PlaceIndex, mentioned

PLACES = [
    make_poi("opera", "Астана Опера", "amenity=theatre"),
    make_poi("museum", "Қазақстан Ұлттық Мұражайы", "tourism=museum", 300,
             name_ru="Национальный музей Республики Казахстан"),
    make_poi("stars", "Stars", "amenity=cafe", 600),
    make_poi("park", "Парк", "leisure=park", 900),  # только общее слово — непонятно, какой
    make_poi("clock", "Кофе И Часы", "amenity=cafe", 1200),
    make_poi("mesto", "Место", "amenity=cafe", 1300),
    make_poi("keruen", "Керуен", "shop=mall", 1400),
    *[make_poi(f"sb{i}", "Starbucks", "amenity=cafe", 1000 + 100 * i) for i in range(3)],  # сеть
]


@pytest.fixture(scope="module")
def index():
    return PlaceIndex(Catalog(PLACES))


@pytest.mark.parametrize("text, place_id", [
    ("я у Астана Оперы", "opera"),                      # падеж
    ("возле Национального музея", "museum"),            # первые два слова длинного названия
    ("я у Stars", "stars"),
    ("я у Starbucks", None),                            # «Stars» ≠ «Starbucks»; а сеть не говорит, у какой
    ("я в парке", None),                                # общее слово
    ("хочу сходить в Астана Оперу", None),              # цель пути, а не где я
    ("иду до Национального музея", None),
    ("хочу тихое место с кофе и розеткой", None),       # не кафе «Место» и не «Кофе И Часы»
    ("я в Керуене", "keruen"),                          # одно слово — после «в», «у», «возле»
    ("Керуен хороший торговый центр", None),
    ("сижу в кафе Кофе и часы", "clock"),
])
def test_find(index, text, place_id):
    found = index.find(text)
    assert (found["poi_id"] if found else None) == place_id


def test_found_place_has_label_and_coords(index):
    found = index.find("я у Астана Оперы")
    assert found == {"lat": 51.1, "lon": 71.4, "label": "Астана Опера", "poi_id": "opera"}


@pytest.mark.parametrize("text, place_id", [
    ("хочу дойти до Астана Оперы", "opera"),
    ("доведи к Национальному музею", "museum"),
    ("я у Астана Оперы", None),          # это старт, а не цель
    ("мимо Астана Оперы", None),         # по пути, но не цель
])
def test_find_destination(index, text, place_id):
    found = index.find(text, destination=True)
    assert (found and found["poi_id"]) == place_id


def test_find_via(index):
    text = "хочу дойти до Астана Оперы через Национальный музей"
    assert index.find(text, via=True)["poi_id"] == "museum"
    assert index.find(text) is None                                  # «через» — не старт
    assert index.find(text, destination=True)["poi_id"] == "opera"   # и не цель


@pytest.fixture(scope="module")
def city():
    """Места и районы: ресторан «Жагалау» на одном берегу, микрорайон Жагалау — на другом."""
    pois = [make_poi("rest", "Жагалау", "amenity=restaurant", 5000),
            make_poi("vecher", "Вечер", "amenity=cafe", 100)]
    areas = [
        {"id": "osm:way/1", "kind": "district", "name": "Жағалау шағын ауданы", "name_ru": "микрорайон Жагалау",
         "name_kk": None, "name_en": None, "lat": 51.13, "lon": 71.36, "points": [], "parts": 1},
        {"id": "osm:way/2", "kind": "complex", "name": "ЖК Хайвил Астана блок B", "name_ru": None, "name_kk": None,
         "name_en": None, "lat": 51.12, "lon": 71.46, "points": [], "parts": 1},
        {"id": "osm:way/4", "kind": "complex", "name": "ЖК Арай блок Б", "name_ru": None, "name_kk": None,
         "name_en": None, "lat": 51.14, "lon": 71.43, "points": [], "parts": 1},
        {"id": "osm:way/3", "kind": "street", "name": "Қабанбай Батыр даңғылы", "name_ru": "проспект Кабанбай Батыра",
         "name_kk": None, "name_en": None, "lat": 51.10, "lon": 71.40, "points": [[51.05, 71.41], [51.10, 71.40]],
         "parts": 50},
    ]
    return PlaceIndex(Catalog(pois), areas)


@pytest.mark.parametrize("text, destination, place_id", [
    ("доведи до Жагалау", True, "osm:way/1"),             # район, а не ресторан «Жагалау» на другом берегу
    ("я в Жағалау", False, "osm:way/1"),                   # казахские буквы = русские
    ("я в ЖК Хайвил", False, "osm:way/2"),                 # «Астана блок B» — не часть названия
    ("я в ЖК Арай", False, "osm:way/4"),                   # короткое название: буква блока мешала бы
    ("хочу дойти до Кабанбай батыра", True, "osm:way/3"),
    ("что посоветуете на вечер", False, None),             # время суток — не кафе «Вечер»
    ("хочу в кино вечером", True, None),
])
def test_districts_complexes_streets(city, text, destination, place_id):
    found = city.find(text, destination=destination)
    assert (found and found["poi_id"]) == place_id


def test_street_destination_has_points(city):
    assert city.find("до Кабанбай батыра", destination=True)["points"]


def test_short_and_main_word_names():
    index = PlaceIndex(Catalog([make_poi("akorda", "Ак орда", "tourism=attraction", wikidata="Q1"),
                                make_poi("opera", "Астана Опера", "amenity=theatre", 500, wikidata="Q2")]))
    assert index.find("я у Ак Орды, погулять")["poi_id"] == "akorda"           # «орды» — «орда»
    assert index.find("как дойти до Ак Орды", destination=True)["poi_id"] == "akorda"
    assert index.find("я у оперы, хочу поесть")["poi_id"] == "opera"            # «Астана» — общее слово
    assert index.find("хочу дойти до домой в Жагалау", destination=True) is None


@pytest.mark.parametrize("text, place_id", [
    ("я на правом берегу", None),          # «берегу» ≠ улица «Береке» — раньше основа «бере-» совпадала
    ("я у Береке", "bereke"),
    ("я у Дворца мира", "palace"),         # беглая гласная: дворец — дворца
    ("я в новом районе", None),            # «новом» — не улица «Новая»
])
def test_word_endings(text, place_id):
    pois = [make_poi("palace", "Дворец мира", "tourism=attraction", wikidata="Q1")]
    areas = [{"id": "bereke", "kind": "street", "name": "Береке көшесі", "name_ru": None, "name_kk": None,
              "name_en": None, "lat": 51.0, "lon": 71.4, "points": [], "parts": 1},
             {"id": "nova", "kind": "street", "name": "Новая улица", "name_ru": None, "name_kk": None,
              "name_en": None, "lat": 51.0, "lon": 71.5, "points": [], "parts": 1}]
    found = PlaceIndex(Catalog(pois), areas).find(text)
    assert (found and found["poi_id"]) == place_id


@pytest.mark.parametrize("said, text, yes", [
    ("ритц карлтона", "Башни с офисами и отелем Ritz-Carlton.", True),   # кириллица — латиница
    ("ак орды", "Ак Орда", True),
    ("талан тауэрс", "Talan Towers", True),
    ("кафе", "Кафе Два", False),           # общее слово — про все кафе, а не про одно
    ("байтерека", "Хан Шатыр", False),
])
def test_mentioned(said, text, yes):
    assert mentioned(said, text) == yes


def test_mentioned_in_description_only_by_proper_names():
    text = "Комплекс с торгово-развлекательным центром и отелем Ritz-Carlton."
    assert mentioned("ритц карлтона", text, proper_only=True)
    assert mentioned("торговых центров", text) and not mentioned("торговых центров", text, proper_only=True)


def test_via_is_a_place_not_a_complex_named_after_it():
    # у арки стоит ЖК «Триумфальная Арка»: при равном совпадении район важнее, но через ЖК маршрут не построить
    arch = make_poi("arch", "Триумфальная арка Мангилик Ел", "tourism=attraction", wikidata="Q1")
    complex_ = {"id": "osm:relation/1", "kind": "district", "name": "Триумфальная Арка", "name_ru": None,
                "name_kk": None, "name_en": None, "lat": 51.1, "lon": 71.4, "points": [], "parts": 1}
    index = PlaceIndex(Catalog([arch]), [complex_])
    assert index.find("иду через Триумфальная арка", via=True)["poi_id"] == "arch"
