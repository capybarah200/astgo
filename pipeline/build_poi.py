"""Сборка итогового набора мест: склейка дублей OSM + обогащение из Wikidata и Википедии.

Запуск:  python pipeline/build_poi.py   (сеть не нужна)
Вход:    data/poi_osm.json, data/wikidata_astana.json, data/wikidata_extra.json (если есть) — не меняются
Результат:
  data/poi.json          — места в схеме POI (см. normalize() в collect_osm.py) + поля ниже
  reports/build_poi.md   — что склеено, что сопоставлено с Wikidata, что добавлено, что проверить глазами

Поля, которые добавляет этот этап:
  merged_from         — id точек, склеенных в эту (пустой список, если склеек не было)
  description_source  — откуда описание: "wikipedia" | "osm" | "wikidata" | None (в порядке приоритета)
  wikipedia           — ссылка на статью Википедии (нужна для подписи по CC BY-SA); ru, иначе en
  image               — фото с Commons: {thumb, page, artist, license, license_url} или None
  guide               — текст путеводителя Wikivoyage о месте: {text, url} или None — материал для рассказа гида

Главные объекты Wikidata, которых нет в выборке OSM (Ак Орда, площадь Независимости, Talan Towers),
добавляются как места с id «wikidata:Q…» — только по белому списку типов (P31) и только со статьёй
в Википедии: гостиницы, министерства, сёла и остановки местами для прогулки не становятся.
"""

import json
import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # чтобы `python pipeline/...py` видел пакет pipeline
from pipeline.coverage import GROUPS  # noqa: E402

OSM_PATH = ROOT / "data" / "poi_osm.json"
WIKIDATA_PATH = ROOT / "data" / "wikidata_astana.json"
EXTRA_PATH = ROOT / "data" / "wikidata_extra.json"
WIKIVOYAGE_PATH = ROOT / "data" / "wikivoyage_astana.json"
OUT_PATH = ROOT / "data" / "poi.json"
REPORT_PATH = ROOT / "reports" / "build_poi.md"

DUP_RADIUS_M = 50          # дальше — скорее соседние филиалы сети, чем дубль
WIKIDATA_RADIUS_M = 300    # координаты Wikidata часто стоят на краю территории, а не в центре
TAG_MAX_M = 1000           # тег wikidata дальше этого — скорее ошибка (Q бренда или соседнего здания)
MIN_CONTAINED = 5          # «Mega Silk Way» внутри «Mega SilkWay Astana» — да, «кофе» внутри «кофейня» — нет
DESCRIPTION_LIMIT = 350    # набираем целые предложения, пока влезают
DESCRIPTION_MAX = 500      # если первое предложение длиннее — режем по слову

NAME_FIELDS = ("name", "name_ru", "name_en", "name_kk")
FILL_FIELDS = ("name", "name_kk", "name_ru", "name_en", "opening_hours", "website", "phone",
               "description", "description_source", "wikidata", "wikipedia", "wheelchair")
DERIVED_FIELDS = {"description_source"}  # следует за description — при выборе основы не считаем дважды
FOOD = GROUPS["Кафе и еда"]  # кафе и фастфуд в OSM путают постоянно — считаем одной категорией

# Тип объекта Wikidata (P31) → категория места. Порядок важен: первый совпавший тип задаёт категорию
TYPE_CATEGORIES = {
    "Q33506": "tourism=museum", "Q207694": "tourism=museum", "Q1007870": "tourism=gallery",
    "Q32815": "amenity=place_of_worship", "Q56242225": "amenity=place_of_worship", "Q2977": "amenity=place_of_worship",
    "Q16970": "amenity=place_of_worship",
    "Q24354": "amenity=theatre", "Q1060829": "amenity=music_venue", "Q41253": "amenity=cinema",
    "Q4989906": "historic=monument", "Q5003624": "historic=memorial", "Q483453": "amenity=fountain",
    "Q481289": "tourism=attraction", "Q174782": "tourism=attraction", "Q11303": "tourism=attraction",
    "Q537127": "tourism=attraction", "Q158438": "tourism=attraction", "Q12280": "tourism=attraction",
    "Q570116": "tourism=attraction", "Q2319498": "tourism=attraction", "Q811979": "tourism=attraction",
    "Q41176": "tourism=attraction",  # «здание» со статьёй — архитектурный символ: Триумф Астана, Talan Towers
    "Q22698": "leisure=park", "Q54114": "leisure=park", "Q23397": "leisure=nature_reserve",
    "Q11315": "shop=mall", "Q483110": "leisure=stadium",
}
# Не места для прогулки, даже если среди типов есть «здание»: гостиница, министерство, вуз, село…
EXCLUDE_TYPES = {
    "Q27686", "Q654772", "Q3918", "Q2385804", "Q3914", "Q137794598", "Q1519799", "Q1364302", "Q327333",
    "Q4830453", "Q6881511", "Q43229", "Q486972", "Q130212458", "Q2175765", "Q55488", "Q79007", "Q129675946",
    "Q1497364", "Q644371", "Q94993988", "Q172754", "Q18608583", "Q4654165", "Q4164871", "Q87415039",
    "Q13226383", "Q375928", "Q2570643", "Q189445", "Q190752", "Q4296769", "Q45400320", "Q7930989",
    "Q12266391", "Q1549591", "Q108178728", "Q15840617", "Q2643128", "Q17334923",
}

EARTH_RADIUS_M = 6_371_000
M_PER_DEG = EARTH_RADIUS_M * math.pi / 180
STRESS_MARK = chr(0x301)  # знак ударения в Википедии: «Байтере́к»
# Сокращения, после которых точка — не конец предложения: инициалы (и казахские), «г.», «ул.», «каз.»…
ABBREV = re.compile(r"(?:^|[\s(«])(?:[А-ЯЁӘҒҚҢӨҰҮҺІA-Z]|г|гг|ул|им|пр|просп|д|св|ст|т|е|см|др|каз|рус|англ|букв"
                    r"|ок|тыс|млн)\.$")


def distance_m(a: dict, b: dict) -> float:
    """Расстояние в метрах. На масштабе города плоское приближение ошибается меньше чем на 0,1%."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    x = (lon2 - lon1) * math.cos((lat1 + lat2) / 2)
    return EARTH_RADIUS_M * math.hypot(x, lat2 - lat1)


def near(a: dict, b: dict, radius_m: float) -> bool:
    """Быстрый отсев перед точным расстоянием: квадрат ±radius_m (с запасом 1%) вокруг a."""
    dlat = radius_m * 1.01 / M_PER_DEG
    dlon = dlat / math.cos(math.radians(a["lat"]))
    return abs(a["lat"] - b["lat"]) <= dlat and abs(a["lon"] - b["lon"]) <= dlon


def clean_label(label: str | None) -> str | None:
    """«Ак орда (резиденция)» → «Ак орда»: уточнение в скобках — не часть названия."""
    return re.sub(r"\s*\([^()]*\)$", "", label).strip() if label else label


def norm_name(name: str | None) -> str:
    """«Ататүрік» саябағы → ататүріксаябағы: регистр, ё и знаки препинания не важны."""
    return re.sub(r"[\W_]+", "", (name or "").lower().replace("ё", "е"))


def name_keys(poi: dict) -> set[str]:
    return {k for k in (norm_name(poi.get(f)) for f in NAME_FIELDS) if k}


def item_keys(item: dict) -> set[str]:
    return {k for k in (norm_name(clean_label(item.get(f))) for f in ("label_ru", "label_en", "label_kk")) if k}


def names_match(keys_a: set[str], keys_b: set[str]) -> bool:
    """Хотя бы один вариант названия совпал или содержится в другом."""
    for a in keys_a:
        for b in keys_b:
            short, longer = sorted((a, b), key=len)
            if short == longer or (len(short) >= MIN_CONTAINED and short in longer):
                return True
    return False


def compatible(cat_a: str, cat_b: str) -> bool:
    return cat_a == cat_b or (cat_a in FOOD and cat_b in FOOD)


def with_defaults(poi: dict) -> dict:
    return {**poi, "merged_from": poi.get("merged_from", []),
            "description_source": poi.get("description_source") or ("osm" if poi.get("description") else None),
            "wikipedia": poi.get("wikipedia"), "image": poi.get("image"), "guide": poi.get("guide")}


# ---------- Склейка дублей ----------

def find_duplicates(pois: list[dict]) -> tuple[list[tuple[float, int, int]], list[tuple[int, int]]]:
    """Пары: (склеить — (метры, i, j) от ближних к дальним; проверить глазами — (i, j)).

    Склеиваем: один и тот же Q в радиусе 300 м — или похожее название в радиусе 50 м
    при совместимых категориях. Похожее название, но другая категория (океанариум и аквапарк
    «Ailand») — не склеиваем, а выносим в отчёт: это может быть комплекс из разных мест.
    """
    merge, check = [], []
    keys = [name_keys(p) for p in pois]
    radius = max(WIKIDATA_RADIUS_M, DUP_RADIUS_M)
    for i, a in enumerate(pois):
        for j in range(i + 1, len(pois)):
            b = pois[j]
            if not near(a, b, radius):
                continue
            d = distance_m(a, b)
            if a["wikidata"] and b["wikidata"]:
                if a["wikidata"] == b["wikidata"] and d <= WIKIDATA_RADIUS_M:
                    merge.append((d, i, j))
                continue  # разные Q — разные объекты, даже при одинаковом названии
            if keys[i] and keys[j] and d <= DUP_RADIUS_M and names_match(keys[i], keys[j]):
                if compatible(a["category"], b["category"]):
                    merge.append((d, i, j))
                else:
                    check.append((i, j))
    return sorted(merge), check


def group_pairs(pois: list[dict], pairs: list[tuple[float, int, int]]) -> list[list[int]]:
    """Связные группы: если A~B и B~C, то склеиваются все три.

    Кроме случая, когда в группе оказались бы два разных Q: это разные объекты, и точку
    без Q между ними нельзя делать мостом. Пары идут от ближних к дальним, поэтому
    спорная точка уходит к ближайшему соседу.
    """
    parent = list(range(len(pois)))
    group_q = [p["wikidata"] for p in pois]

    def root(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for _, i, j in pairs:
        ri, rj = root(i), root(j)
        if ri == rj or (group_q[ri] and group_q[rj] and group_q[ri] != group_q[rj]):
            continue
        parent[ri] = rj
        group_q[rj] = group_q[rj] or group_q[ri]
    groups = defaultdict(list)
    for i in range(len(pois)):
        groups[root(i)].append(i)
    return sorted(groups.values())


def filled(poi: dict) -> int:
    return sum(1 for f in FILL_FIELDS if f not in DERIVED_FIELDS and poi.get(f))


def merge(group: list[dict]) -> dict:
    """Основа — точка с большим числом заполненных полей (при равенстве — контур, а не узел).
    Пустые поля основы заполняются из остальных; теги и источники объединяются."""
    ranked = sorted(group, key=lambda p: (-filled(p), p["id"].startswith("osm:node/"), p["id"]))
    main, rest = ranked[0], ranked[1:]
    out = dict(main)
    for field in FILL_FIELDS:
        if not out.get(field):
            out[field] = next((p[field] for p in rest if p.get(field)), None)
    tags = {}
    for p in reversed(ranked):  # теги основы перекрывают остальные
        tags.update(p["tags"])
    out["tags"] = tags
    out["sources"] = list(dict.fromkeys(s for p in ranked for s in p["sources"]))
    out["merged_from"] = sorted({i for p in rest for i in [p["id"], *p["merged_from"]]} | set(main["merged_from"]))
    return out


def dedup(pois: list[dict]) -> tuple[list[dict], list[tuple[dict, list[dict]]], list[tuple[dict, dict]]]:
    """→ (места после склейки, [(склеенное место, исходные точки)], пары «проверить глазами»)."""
    merge_pairs, check_pairs = find_duplicates(pois)
    result, merged_groups = [], []
    for idx in group_pairs(pois, merge_pairs):
        group = [pois[i] for i in idx]
        if len(group) == 1:
            result.append(group[0])
        else:
            merged = merge(group)
            result.append(merged)
            merged_groups.append((merged, group))
    return result, merged_groups, [(pois[i], pois[j]) for i, j in check_pairs]


# ---------- Wikidata и Википедия ----------

def short_description(intro: str | None) -> str | None:
    """Первый абзац статьи без ударений и скобок, целыми предложениями до DESCRIPTION_LIMIT символов."""
    if not intro:
        return None
    text = intro.replace(STRESS_MARK, "").strip().split("\n")[0]
    while re.search(r"\([^()]*\)", text):  # вложенные скобки снимаются изнутри наружу
        text = re.sub(r"\s*\([^()]*\)", "", text)
    text = re.sub(r"\s+", " ", text).replace(" ,", ",").replace(" .", ".").strip()
    sentences = []
    for part in re.split(r"(?<=[.!?…])\s+", text):
        if sentences and ABBREV.search(sentences[-1]):
            sentences[-1] += " " + part
        else:
            sentences.append(part)
    out = sentences[0]
    for sentence in sentences[1:]:
        if len(out) + 1 + len(sentence) > DESCRIPTION_LIMIT:
            break
        out += " " + sentence
    if len(out) > DESCRIPTION_MAX:
        out = out[:DESCRIPTION_MAX].rsplit(" ", 1)[0].rstrip(",;:—– ") + "…"
    return out or None


Match = tuple[dict, dict, str, float]  # (место, объект Wikidata, как сопоставлено, метры)


def match_wikidata(pois: list[dict], items: list[dict]) -> tuple[list[Match], list[Match]]:
    """→ (пары место–объект Wikidata, отвергнутые пары по тегу).

    Сначала по тегу wikidata из OSM, если объект не дальше TAG_MAX_M — иначе тег, скорее всего,
    ошибочный, и пара уходит в отвергнутые. Потом объекты без пары — по точному совпадению
    названия в радиусе 300 м: каждой точке и каждому объекту не больше одной пары, ближние — первыми.
    """
    by_q = {i["q"]: i for i in items}
    matches, rejected, used_q, used_poi = [], [], set(), set()
    for p in pois:
        item = by_q.get(p["wikidata"])
        if not item:
            continue
        d = distance_m(p, item)
        if d > TAG_MAX_M:
            rejected.append((p, item, "тег", d))
            continue
        matches.append((p, item, "тег", d))
        used_q.add(item["q"])
        used_poi.add(p["id"])

    free = [p for p in pois if not p["wikidata"] and p["name"]]
    keys = {p["id"]: name_keys(p) for p in free}
    candidates = []
    for item in items:
        ikeys = item_keys(item)
        if item["q"] in used_q or not ikeys:
            continue
        for p in free:
            if near(p, item, WIKIDATA_RADIUS_M) and keys[p["id"]] & ikeys:
                d = distance_m(p, item)
                if d <= WIKIDATA_RADIUS_M:
                    candidates.append((d, item["q"], p["id"], p, item))
    for d, q, pid, p, item in sorted(candidates, key=lambda c: c[:3]):
        if q not in used_q and pid not in used_poi:
            matches.append((p, item, "имя", d))
            used_q.add(q)
            used_poi.add(pid)
    return matches, rejected


def enrich(poi: dict, item: dict, extra: dict | None = None) -> None:
    """Заполняет пустые поля места данными из Wikidata и Википедии (+ фото и английская статья из extra).

    Исключение — описание: текст Википедии важнее описания из OSM. В OSM это заметки
    картографов («62 x 62 m , 77m hight…»), исходный текст остаётся в tags. Русская статья
    важнее английской; английская — когда русской нет (Gemini перескажет её по-русски).
    """
    extra = extra or {}
    poi["wikidata"] = poi["wikidata"] or item["q"]
    for field, label in (("name_ru", "label_ru"), ("name_en", "label_en"), ("name_kk", "label_kk")):
        if not poi[field]:
            poi[field] = clean_label(item[label])
    if not poi["name"]:
        poi["name"] = poi["name_kk"] or poi["name_ru"] or poi["name_en"]
    intro = short_description(item["ruwiki_intro"])
    en_intro = short_description(extra.get("enwiki_intro"))
    if intro:
        poi["description"], poi["description_source"] = intro, "wikipedia"
    elif en_intro:
        poi["description"], poi["description_source"] = en_intro, "wikipedia"
    elif not poi["description"] and item["description_ru"]:
        text = item["description_ru"]
        poi["description"], poi["description_source"] = text[:1].upper() + text[1:], "wikidata"
    if not poi["wikipedia"]:
        poi["wikipedia"] = item["ruwiki_url"] or (extra.get("enwiki_url") if en_intro else None)
    if extra.get("image") and not poi.get("image"):
        poi["image"] = {k: extra["image"].get(k) for k in ("thumb", "page", "artist", "license", "license_url")}
    added = (["wikidata"] + (["wikipedia"] if poi["description_source"] == "wikipedia" else [])
             + (["commons"] if poi.get("image") else []))
    poi["sources"] = list(dict.fromkeys(poi["sources"] + added))


def category_from_types(types: list[str]) -> str | None:
    """Типы P31 → категория места; None — не место для прогулки (гостиница, министерство…) или тип незнаком."""
    if not types or EXCLUDE_TYPES & set(types):
        return None
    return next((cat for q, cat in TYPE_CATEGORIES.items() if q in types), None)


def place_from_wikidata(item: dict, extra: dict) -> dict | None:
    """Объект Wikidata, которого нет среди мест OSM, → место; только известное (со статьёй) и по типу."""
    category = category_from_types([t["q"] for t in extra.get("types", [])])
    name = clean_label(item["label_ru"] or item["label_en"] or item["label_kk"])
    if not category or not name or not (item["ruwiki_url"] or extra.get("enwiki_url")):
        return None
    poi = with_defaults({
        "id": f"wikidata:{item['q']}", "name": name, "name_kk": None, "name_ru": None, "name_en": None,
        "lat": item["lat"], "lon": item["lon"], "category": category, "opening_hours": None, "website": None,
        "phone": None, "description": None, "wikidata": None, "wheelchair": None, "tags": {}, "sources": [],
    })
    enrich(poi, item, extra)
    return poi


TRANSLIT = str.maketrans({"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
                          "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
                          "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
                          "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
                          "я": "ya", "ә": "a", "ғ": "g", "қ": "k", "ң": "n", "ө": "o", "ұ": "u", "ү": "u",
                          "һ": "h", "і": "i"})
# Слова вида места — не повод считать названия одинаковыми: «Grand Mosque» ≠ «мечеть Хазрет Султан»
NAME_GENERIC = {"park", "bulding", "buildings", "building", "palace", "dvorets", "mosque", "mechet", "astana",
                "square", "ploshad", "fountain", "fontan", "museum", "musei", "monument", "pamyatnik", "center",
                "centre", "tsentr", "the", "and", "of", "sarai", "saraiy", "meshiti", "sayabak", "sayabagy"}
# …и слова из официальных названий: «Парламент Республики Казахстан» ≠ концертный зал «Казахстан»
NAME_GENERIC_PREFIXES = ("kasak", "respublik", "natsion", "nation", "akadem", "gosudar", "tsentral", "kentral",
                         "tower", "stolich", "gorodsk")
SAME_NAME_RADIUS_M = 1000  # Wikivoyage ставит точку где-то на большой территории: Президентский парк — 956 м


def latin_tokens(name: str | None) -> set[str]:
    """Название → слова латиницей без различий з/с, к/c, й/y: «Президентский» ≈ «Presidential»."""
    text = (name or "").lower().translate(TRANSLIT)
    text = text.replace("z", "s").replace("c", "k").replace("y", "i")
    return {w for w in re.findall(r"[a-z]{4,}", text)
            if w not in NAME_GENERIC and not w.startswith(NAME_GENERIC_PREFIXES)}


def similar_names(a: set[str], b: set[str]) -> bool:
    """Хотя бы одно значимое слово совпало или начинается одинаково (6+ букв): «presidential» ~ «presidentskii»."""
    return any(x == y or (len(x) >= 6 and len(y) >= 6 and x[:6] == y[:6]) for x in a for y in b)


def attach_guides(pois: list[dict], listings: list[dict], url: str,
                  ru_labels: dict[str, str] | None = None) -> tuple[int, list[dict]]:
    """Описания Wikivoyage → места: по Q Wikidata, иначе по названию в радиусе 300 м.

    → (сколько прикреплено, новые места из «See» путеводителя, которых у нас нет: «Триумф Астаны»).
    Новое место — только с координатами; та же категория в 150 м от нашего места — не добавляем.
    """
    by_q = {p["wikidata"]: p for p in pois if p["wikidata"]}
    attached, added = 0, []
    for lst in listings:
        poi = by_q.get(lst["wikidata"]) if lst["wikidata"] else None
        if not poi and lst["lat"] is not None:
            keys = {k for k in (norm_name(lst["name"]), norm_name(lst["alt"])) if k}
            poi = next((p for p in pois if p["name"] and near(p, lst, WIKIDATA_RADIUS_M)
                        and distance_m(p, lst) <= WIKIDATA_RADIUS_M and names_match(name_keys(p), keys)), None)
        guide = {"text": lst["content"], "url": url}
        if poi:
            if not poi.get("guide"):
                poi["guide"] = guide
                poi["sources"] = list(dict.fromkeys(poi["sources"] + ["wikivoyage"]))
                attached += 1
            continue
        if lst["type"] != "see" or lst["lat"] is None:
            continue
        # То же место под другим названием и на другом языке: «Presidential Park» = «Президентский парк»
        tokens = latin_tokens(lst["name"]) | latin_tokens(lst["alt"])
        if any(near(p, lst, SAME_NAME_RADIUS_M) and distance_m(p, lst) <= SAME_NAME_RADIUS_M
               and similar_names(tokens, set().union(*(latin_tokens(p.get(f)) for f in NAME_FIELDS)))
               for p in pois + added if p["name"]):
            continue
        # alt — местное название, но бывает и пояснением («Kazako: Asyl tas; russo: …») — такое не берём
        alt = lst["alt"] if lst["alt"] and not re.search(r"[:;]", lst["alt"]) else None
        cyrillic = alt if alt and re.search(r"[А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүІі]", alt) else None
        new = with_defaults({
            "id": f"wikivoyage:{lst['wikidata'] or norm_name(lst['name'])}", "name": cyrillic or lst["name"],
            # русское название из Wikidata: иначе в русском интерфейсе «The Triumph of Astana»
            "name_kk": None, "name_ru": (ru_labels or {}).get(lst["wikidata"]), "name_en": lst["name"],
            "lat": lst["lat"], "lon": lst["lon"],
            "category": "tourism=attraction", "opening_hours": None, "website": None, "phone": None,
            "description": None, "wikidata": lst["wikidata"], "wheelchair": None, "tags": {},
            "sources": ["wikivoyage"], "guide": guide})
        if not any(p["category"] == new["category"] and near(p, new, 150) and distance_m(p, new) <= 150
                   for p in pois + added):
            added.append(new)
    return attached, added


def add_wikidata_places(pois: list[dict], items: list[dict], extras: dict[str, dict]) -> list[dict]:
    """Новые места из несопоставленных объектов Wikidata. Та же категория в 150 м от места OSM —
    скорее то же здание под другим названием: не добавляем."""
    added = []
    for item in items:
        new = place_from_wikidata(item, extras.get(item["q"], {}))
        if new and not any(p["category"] == new["category"] and near(p, new, 150) and distance_m(p, new) <= 150
                           for p in pois + added):
            added.append(new)
    return added


# ---------- Отчёт ----------

def coverage_rows(before: list[dict], after: list[dict]) -> list[str]:
    named_before = [p for p in before if p["name"]]
    named_after = [p for p in after if p["name"]]
    rows = ["| Поле | До (poi_osm.json) | После (poi.json) |", "|---|---|---|",
            f"| Точек всего | {len(before)} | {len(after)} |",
            f"| С названием | {len(named_before)} | {len(named_after)} |"]
    for field in ("name_ru", "name_en", "name_kk", "description", "wikidata"):
        rows.append(f"| {field} (среди мест с названием) | {sum(1 for p in named_before if p.get(field))} | "
                    f"{sum(1 for p in named_after if p.get(field))} |")
    return rows


def report(before, after, merged_groups, check_pairs, matches, rejected, missing_q) -> str:
    by_tag = sum(1 for m in matches if m[2] == "тег")
    merged_into = {old: p["id"] for p in after for old in p["merged_from"]}

    def ref(p: dict) -> str:
        """id точки; если она склеена в другую — с указанием, где её искать в poi.json."""
        new = merged_into.get(p["id"])
        return f"`{p['id']}`" + (f" (в `{new}`)" if new else "")

    lines = ["# Сборка мест: склейка дублей и Wikidata", "",
             f"Склеено групп: **{len(merged_groups)}** (убрано точек: "
             f"{sum(len(g) - 1 for _, g in merged_groups)}). Сопоставлено с Wikidata: **{len(matches)}** "
             f"(по тегу OSM — {by_tag}, по названию — {len(matches) - by_tag}).", "",
             "## Заполненность", "", *coverage_rows(before, after), "",
             f"## Склеено ({len(merged_groups)})", "",
             f"Правило: один Q в радиусе {WIKIDATA_RADIUS_M} м или похожее название в радиусе {DUP_RADIUS_M} м "
             "при той же категории (кафе / фастфуд / ресторан — одна). Первая точка в строке — основа.", ""]
    for main, group in merged_groups:
        parts = [f"`{p['id']}` {p['name']} ({p['category']}, {distance_m(main, p):.0f} м)" for p in group
                 if p["id"] != main["id"]]
        lines.append(f"- **{main['name']}** `{main['id']}` {main['category']} ← " + "; ".join(parts))
    lines += ["", f"## Проверить глазами: похожее название, другая категория, до {DUP_RADIUS_M} м ({len(check_pairs)})",
              "", "Не склеены автоматически.", ""]
    for a, b in check_pairs:
        lines.append(f"- {ref(a)} {a['name']} ({a['category']}) — {ref(b)} {b['name']} ({b['category']}),"
                     f" {distance_m(a, b):.0f} м")
    if rejected:
        lines += ["", f"## Тег wikidata дальше {TAG_MAX_M} м от объекта — не использован ({len(rejected)})", ""]
        lines += [f"- `{p['id']}` {p['name']} — {item['q']} {item['label_ru'] or ''}, {d:.0f} м"
                  for p, item, _, d in rejected]
    lines += ["", f"## Сопоставлено с Wikidata ({len(matches)})", "",
              "| Место | id | Q | Подпись Wikidata | Как | Метры | Описание |", "|---|---|---|---|---|---|---|"]
    for p, item, how, d in sorted(matches, key=lambda m: (m[2], m[0]["name"] or "")):
        lines.append(f"| {p['name']} | `{p['id']}` | {item['q']} | {item['label_ru'] or item['label_en'] or ''} "
                     f"| {how} | {d:.0f} | {p['description_source'] or '—'} |")
    if missing_q:
        lines += ["", "## Тег wikidata есть, но объекта нет в выгрузке Wikidata", "",
                  "Обычно у объекта в Wikidata нет координат или они вне рамки.", ""]
        lines += [f"- `{p['id']}` {p['name']} — {p['wikidata']}" for p in missing_q]
    return "\n".join(lines)


def main() -> None:
    osm = json.loads(OSM_PATH.read_text(encoding="utf-8"))
    wd = json.loads(WIKIDATA_PATH.read_text(encoding="utf-8"))
    extra = json.loads(EXTRA_PATH.read_text(encoding="utf-8")) if EXTRA_PATH.exists() else {"items": {}, "meta": {}}
    extras = extra["items"]
    before = [with_defaults(p) for p in osm["pois"]]

    pois, merged_groups, check_pairs = dedup(before)
    pois = [dict(p) for p in pois]  # enrich меняет места — не трогаем исходные словари
    known_q = {i["q"] for i in wd["items"]}
    missing_q = [p for p in pois if p["wikidata"] and p["wikidata"] not in known_q]
    matches, rejected = match_wikidata(pois, wd["items"])
    for p, item, _, _ in matches:
        enrich(p, item, extras.get(item["q"]))
    used_q = {item["q"] for _, item, _, _ in matches}
    added = add_wikidata_places(pois, [i for i in wd["items"] if i["q"] not in used_q], extras)
    pois += added
    voyage = json.loads(WIKIVOYAGE_PATH.read_text(encoding="utf-8")) if WIKIVOYAGE_PATH.exists() else None
    ru_labels = {i["q"]: clean_label(i["label_ru"]) for i in wd["items"] if i["label_ru"]}
    guides_attached, voyage_added = (attach_guides(pois, voyage["listings"], voyage["meta"]["url"], ru_labels)
                                     if voyage else (0, []))
    pois += voyage_added

    meta = {
        "source": "OpenStreetMap contributors (ODbL); Wikidata (CC0); ru/en.wikipedia.org (CC BY-SA 4.0); "
                  "фото — Wikimedia Commons (лицензия у каждого своя, указана у фото)",
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "inputs": {"osm_collected_at": osm["meta"]["collected_at"],
                   "wikidata_collected_at": wd["meta"]["collected_at"],
                   "wikidata_extra_collected_at": extra["meta"].get("collected_at")},
        "count": len(pois),
    }
    OUT_PATH.write_text(json.dumps({"meta": meta, "pois": pois}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    text = report(before, pois, merged_groups, check_pairs, matches, rejected, missing_q)
    text += "\n\n" + "\n".join([
        f"## Добавлено из Wikidata ({len(added)})", "",
        "Объекты, которых нет в выборке OSM: по типу (P31) из белого списка и только со статьёй в Википедии.", "",
        *[f"- `{p['id']}` {p['name']} — {p['category']}" + (" · фото" if p["image"] else "") for p in added],
        "", f"Фото с Commons: у {sum(1 for p in pois if p['image'])} мест; "
            f"описание из Википедии: у {sum(1 for p in pois if p['description_source'] == 'wikipedia')}.", "",
        "## Путеводитель Wikivoyage", "",
        f"Описание прикреплено к {guides_attached} местам; новых мест из раздела «See»: {len(voyage_added)}.", "",
        *[f"- `{p['id']}` {p['name']}" for p in voyage_added]])
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"\nСохранено {len(pois)} мест → {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
