"""Где человек и куда идёт: место, район, жилой комплекс или улица по названию из текста.

Семь ориентиров (Mega, Экспо, Байтерек…) ищутся по готовым шаблонам в app/query.py, остальные —
по названиям 1872 мест базы и районов, ЖК и улиц из OSM (data/areas_osm.json: «до Жагалау»,
«я на Кенесары»). Сравниваются целые слова с учётом падежей (основа слова без окончания),
поэтому «у Астана Оперы» находит «Астана Опера», а «в парке влюблённых» — «Парк влюбленных».
Казахские буквы приравнены к русским: «Жағалау» = «Жагалау». Пропускаются:
- названия только из общих слов («Парк», «Coffee», «Библиотека») — непонятно, какой именно;
- сети (одно название 3+ раз: Starbucks, KFC) — «я у Starbucks» не говорит, у какого;
- упоминания после «до», «к», «хочу в» — это цель пути, а не где человек сейчас.
При одинаковом совпадении район важнее жилого комплекса, тот — улицы, улица — заведения:
«до Жагалау» — микрорайон, а не ресторан «Жагалау» на другом берегу.
"""

import json
import re
from pathlib import Path

from app.engine import Catalog, display_name
from app.query import DESTINATION, TARGET, VIA
from pipeline.build_poi import NAME_GENERIC, TRANSLIT, distance_m

AREAS_PATH = Path(__file__).resolve().parent.parent / "data" / "areas_osm.json"

# Казахские буквы → русские: люди пишут «Жагалау», в OSM — «Жағалау»
KAZAKH = str.maketrans({"ә": "а", "ғ": "г", "қ": "к", "ң": "н", "ө": "о", "ұ": "у", "ү": "у", "һ": "х",
                        "і": "и", "ё": "е"})


def norm(text: str) -> str:
    """Нижний регистр, казахские буквы и «ё» → русские; длина строки не меняется."""
    return text.lower().translate(KAZAKH)


GENERIC = {norm(w) for w in {
    "парк", "сквер", "сад", "кафе", "cafe", "coffee", "кофе", "кофейня", "ресторан", "бар", "bar", "паб", "pub",
    "мечеть", "церковь", "храм", "собор", "фонтан", "памятник", "монумент", "мемориал", "музей", "библиотека",
    "центр", "дом", "зал", "спорт", "food", "фуд", "пицца", "pizza", "донер", "бургер", "арена", "дворец",
    "саябағы", "сарайы", "астана", "astana", "the", "и", "а", "с", "тд", "трц", "тц", "детская", "площадка",
    "столовая", "магазин", "рынок", "кинотеатр", "театр", "отель", "hotel", "plaza", "mall", "city", "club",
    # время в запросах: кафе «Вечер» не должно находиться во фразе «что посоветуете на вечер»
    "вечер", "утро", "ночь", "день", "полдень", "выходные", "час", "время",
    # обычные прилагательные: «я в новом районе» — не «улица Новая»
    "новая", "новый", "старая", "старый", "центральная", "центральный", "северная", "южная", "восточная",
    "западная", "главная", "большая", "малая", "береговая",
}}
# Слова вида места в названиях районов, ЖК и улиц: «Жағалау шағын ауданы» → «Жагалау»
AREA_WORDS = {norm(w) for w in {
    "шағын", "ауданы", "аудан", "ауылы", "микрорайон", "мкр", "жк", "жилой", "комплекс", "жм", "массив",
    "квартал", "район", "поселок", "посёлок", "блок", "очередь", "кезек", "көшесі", "көше", "даңғылы", "даңғыл",
    "улица", "ул", "проспект", "пр", "просп", "бульвар", "переулок", "орамы", "тұйық", "тупик", "проезд",
    "шоссе", "тас", "жолы", "алаңы", "площадь", "набережная", "жағалауы",
}}
# При одинаковой длине совпадения: район > известное место, площадь > ЖК > улица, станция > заведение.
# «Байтерек» — монумент, а не ЖК «Байтерек» в Косшы; «Жагалау» — микрорайон, а не ресторан
KIND_RANK = {"district": 5, "square": 4, "complex": 3, "street": 2, "station": 2}
NOTABLE_POI_RANK = 4
WORD = re.compile(r"\w+")
MAX_ENDING = 3  # «опер|ы», «байтер|ека»: длиннее — уже другое слово («star» ≠ «starbucks»)
# Название из одного слова («Керуен», «Место») засчитываем только после «у», «возле», «в»…:
# иначе кафе «Место» нашлось бы во фразе «хочу тихое место»
LOCATION_MARKER = re.compile(r"(?:\bу|\bвозле|\bоколо|\bрядом\s+с|\bнапротив|\bв|\bво|\bна|\bиз)\s+(?:\w+\s+)?$")


ENDING_LETTERS = set("аеиоуыэюяйь")  # после norm(): ё → е, казахские гласные → русские


def stem(word: str) -> str:
    """Основа для сравнения с падежами — слово без окончания: до двух гласных (или «й») в конце.

    «опера» → «опер» (подходит «оперы»), «президентский» → «президентск» («в Президентском»),
    «байтерек» → «байтерек» («у Байтерека»). Отрезать вслепую нельзя: при основе «бере-» слово
    «берегу» совпадало с улицами «Береке» и «Берел».
    """
    word = norm(word)
    if len(word) >= 5 and word[-2:] in ("ец", "ок", "ек"):
        return word[:-2]  # беглая гласная: «дворец» — «у Дворца»
    for _ in range(2):
        if len(word) > 3 and word[-1] in ENDING_LETTERS:
            word = word[:-1]
    return word


def _latin(word: str) -> str:
    """Как в pipeline/build_poi.latin_tokens: латиница без различий з/с, к/c, й/y."""
    return word.lower().translate(TRANSLIT).replace("z", "s").replace("c", "k").replace("y", "i")


GENERIC_LATIN = {_latin(w) for w in GENERIC} | NAME_GENERIC


def mentioned(said: str, text: str | None, proper_only: bool = False) -> bool:
    """Слова said называют что-то из text на любом языке: «ритц карлтона» — «Ritz-Carlton», «Ак Орды» — «Ак Орда».

    Общие слова («кафе», «парк») не в счёт: «без кафе» — про все кафе, а не про одно показанное.
    proper_only — только имена собственные (латиница или заглавная буква не в начале предложения): для
    описаний, где «без торговых центров» иначе совпало бы с «торговыми галереями» в тексте о Хан Шатыре.
    """
    words = [_latin(w) for w in WORD.findall(said)]
    source = [m.group(0) for m in WORD.finditer(text or "")
              if not proper_only or m.group(0).isascii()
              or m.group(0)[0].isupper() and m.start() > 0 and not re.search(r"[.!?]\s*$", text[:m.start()])]
    for token in {_latin(w) for w in source if len(w) >= 4} - GENERIC_LATIN:
        key = token
        for _ in range(2):  # без окончания, как stem(): «orda» → «ord» подходит к «ordi» («Орды»)
            if len(key) > 3 and key[-1] in "aeiou":
                key = key[:-1]
        if any(w.startswith(key) and len(w) - len(key) <= MAX_ENDING for w in words):
            return True
    return False


def label_of(record: dict) -> str:
    """Подпись места для ответа: у станций — что это за станция («станция ЛРТ «Байтерек»»)."""
    name = display_name(record)
    if record.get("kind") == "station":
        return f"станция ЛРТ «{name}»" if record.get("lrt") else f"вокзал «{name}»"
    return name


def load_areas(path: Path = AREAS_PATH) -> list[dict]:
    """Районы, ЖК и улицы (pipeline/collect_areas.py); нет файла — работаем без них."""
    return json.loads(path.read_text(encoding="utf-8"))["areas"] if path.exists() else []


class PlaceIndex:
    def __init__(self, catalog: Catalog, areas: list[dict] = ()):
        self.entries = []  # (основы слов, запись, ранг вида места)
        self._by_id = {poi["id"]: poi for poi in catalog.pois}
        self.lrt = [a for a in areas if a["kind"] == "station" and a.get("lrt")]
        for poi in catalog.pois:
            if not poi["name"] or catalog.is_chain(poi):
                continue
            rank = NOTABLE_POI_RANK if poi["wikidata"] else 0
            # Известное место зовут по главному слову: «у оперы» — «Астана Опера» («Астана» — общее слово)
            for key in self._keys(poi, strip=set(), min_single=5, main_word=bool(poi["wikidata"] or poi["description"])):
                self.entries.append((key, poi, rank))
        for area in areas:
            if area["id"] in self._by_id:
                continue
            self._by_id[area["id"]] = area
            # По главному слову: «на Омарова», «на Мангилик Ел», «в ЖК Хайвил» («ЖК Хайвил Астана блок B»)
            for key in self._keys(area, strip=AREA_WORDS | GENERIC, min_single=4, longest=True):
                self.entries.append((key, area, KIND_RANK.get(area["kind"], 0)))

    @staticmethod
    def _keys(record: dict, strip: set[str], min_single: int, longest: bool = False,
              main_word: bool = False) -> list[tuple[str, ...]]:
        seen, keys = set(), []
        for field in ("name", "name_ru", "name_en", "name_kk"):
            words = [w for w in (norm(x) for x in WORD.findall(record.get(field) or "")) if w not in strip]
            if strip:  # у районов и ЖК буква блока («блок B») — не часть названия; номер («Жагалау-3») — часть
                words = [w for w in words if not (w.isalpha() and len(w) <= 2)]
            if not words or all(w in GENERIC for w in words):
                continue
            if len(words) == 1 and len(words[0]) < min_single:
                continue  # «Рахат», «Ажар» — ещё ладно, а «SF», «Арай» слишком легко спутать
            variants = [words]
            head = words[:2]
            if len(words) >= 3 and all(len(w) >= 3 for w in head) and not all(w in GENERIC for w in head):
                variants.append(head)  # «Национальный музей (Республики Казахстан)»; но не «Кофе и (часы)»
            if longest and len(words) >= 2:
                main = max(words, key=len)
                if len(main) >= 6 and main not in GENERIC:
                    variants.append([main])
            specific = [w for w in words if w not in GENERIC]
            if main_word and len(words) >= 2 and len(specific) == 1 and len(specific[0]) >= min_single:
                variants.append(specific)
            for variant in variants:
                key = tuple(stem(w) for w in variant)
                if key not in seen:
                    seen.add(key)
                    keys.append(key)
        return keys

    def find(self, text: str, skip_destinations: bool = True, destination: bool = False,
             via: bool = False) -> dict | None:
        """Самое длинное совпадение названия в тексте → место старта; None — не нашли.

        destination=True — наоборот, только место после «до», «к», «хочу в» (куда человек идёт);
        via=True — только после «через» (промежуточная точка маршрута).
        У улицы в ответе есть points — точки вдоль неё, чтобы выбрать ближайшую часть улицы.
        """
        lowered = norm(text)
        tokens = [(m.group(0), m.start()) for m in WORD.finditer(lowered)]
        best, best_rank = None, None
        for key, record, kind_rank in self.entries:
            n = len(key)
            for i in range(len(tokens) - n + 1):
                if not all(tokens[i + k][0].startswith(key[k]) and len(tokens[i + k][0]) - len(key[k]) <= MAX_ENDING
                           for k in range(n)):
                    continue
                before = lowered[:tokens[i][1]]
                if via:
                    # промежуточная точка — место для прогулки, а не ЖК «Триумфальная Арка» у самой арки
                    if not VIA.search(before) or "kind" in record:
                        continue
                elif destination:
                    if not TARGET.search(before):
                        continue
                elif skip_destinations and (DESTINATION.search(before) or n == 1 and not LOCATION_MARKER.search(before)):
                    continue
                rank = (sum(len(s) for s in key), kind_rank, record.get("parts", 0))
                if best_rank is None or rank > best_rank:
                    best, best_rank = record, rank
                break
        if best is None:
            return None
        found = {"lat": best["lat"], "lon": best["lon"], "label": label_of(best), "poi_id": best["id"]}
        if best.get("points"):
            found["points"] = best["points"]
        return found

    def by_id(self, poi_id: str) -> dict | None:
        return self._by_id.get(poi_id)

    def nearest_lrt(self, point: dict) -> tuple[dict, float] | None:
        """Ближайшая станция ЛРТ и расстояние до неё по прямой — для «до центра — на ЛРТ»."""
        if not self.lrt:
            return None
        station = min(self.lrt, key=lambda s: distance_m(s, point))
        return station, distance_m(station, point)
