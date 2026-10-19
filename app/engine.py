"""Подбор мест (retrieval): кандидаты рядом, оценка, мини-маршрут из 1–3 мест.

Факты считает код: расстояние, время пешком, часы работы, время на визит, укладывается ли
маршрут в бюджет времени. LLM потом только объясняет выбор (app/texts.py, app/llm.py)
и не может добавить место, которого нет в ответе движка.
"""

import json
import math
import zlib
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import combinations, permutations
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.hours import Status, status_at
from pipeline.build_poi import distance_m, norm_name
from pipeline.coverage import group_of

ROOT = Path(__file__).resolve().parent.parent
POI_PATH = ROOT / "data" / "poi.json"
LANDMARKS_PATH = ROOT / "data" / "landmarks.json"

# С 1 марта 2024 года весь Казахстан живёт по UTC+5, перехода на летнее время нет
ASTANA_TZ = timezone(timedelta(hours=5), "Астана")

WALK_M_PER_MIN = 75        # 4,5 км/ч
KIDS_WALK_M_PER_MIN = 50   # 3 км/ч — с ребёнком медленнее
DETOUR = 1.3               # по улицам длиннее, чем по прямой
FLIGHT_RESERVE_MIN = 60    # «до вылета»: час оставляем на дорогу в терминал и регистрацию
MAX_ITEMS = 3              # задание: 1–3 пункта
ROUTE_BASELINE = 0.55      # слабое место (фастфуд «для количества») в маршрут не добавляем
DUP_NAME_RADIUS_M = 150    # одно название рядом — одно место в выдаче (Ailand: океанариум и аквапарк)
AT_DESTINATION_M = 150     # «доведи до Байтерека»: место у самого Байтерека — это и есть цель, не остановка
SEEN_PENALTY = 0.12        # место уже показывали в этом разговоре
JITTER = 0.06              # ±0,03 к оценке по seed разговора: перемешивает почти равные места

# категория → (подпись, минут на визит: от, до)
CATEGORIES = {
    "tourism=museum": ("музей", 60, 90),
    "tourism=gallery": ("галерея", 30, 60),
    "tourism=attraction": ("достопримечательность", 20, 40),
    "tourism=viewpoint": ("смотровая площадка", 15, 30),
    "tourism=artwork": ("арт-объект", 5, 10),
    "tourism=zoo": ("зоопарк", 90, 120),
    "tourism=aquarium": ("океанариум", 60, 90),
    "tourism=theme_park": ("парк развлечений", 90, 180),
    "tourism=picnic_site": ("место для пикника", 30, 60),
    "historic=monument": ("монумент", 15, 30),
    "historic=memorial": ("мемориал", 10, 20),
    "amenity=cafe": ("кафе", 20, 45),  # от «взять кофе с собой» до «посидеть»
    "amenity=restaurant": ("ресторан", 60, 90),
    "amenity=fast_food": ("фастфуд", 20, 30),
    "amenity=ice_cream": ("мороженое", 10, 20),
    "amenity=food_court": ("фудкорт", 30, 45),
    "amenity=bar": ("бар", 60, 90),
    "amenity=pub": ("паб", 60, 90),
    "amenity=nightclub": ("ночной клуб", 120, 180),
    "amenity=cinema": ("кинотеатр", 110, 150),
    "amenity=theatre": ("театр", 120, 180),
    "amenity=arts_centre": ("арт-центр", 30, 60),
    "amenity=library": ("библиотека", 30, 60),
    "amenity=planetarium": ("планетарий", 45, 60),
    "amenity=exhibition_centre": ("выставочный центр", 45, 90),
    "amenity=music_venue": ("концертная площадка", 90, 150),
    "amenity=events_venue": ("площадка для мероприятий", 60, 120),
    "amenity=community_centre": ("общественное здание", 30, 60),
    "amenity=fountain": ("фонтан", 10, 15),
    "amenity=place_of_worship": ("мечеть / храм", 15, 30),
    "amenity=marketplace": ("рынок", 30, 60),
    "amenity=coworking_space": ("коворкинг", 60, 120),
    "leisure=park": ("парк", 30, 60),
    "leisure=garden": ("сад / сквер", 20, 40),
    "leisure=nature_reserve": ("природная территория", 60, 120),
    "leisure=playground": ("детская площадка", 20, 40),
    "leisure=water_park": ("аквапарк", 120, 180),
    "leisure=amusement_arcade": ("игровые автоматы", 30, 60),
    "leisure=trampoline_park": ("батутный центр", 45, 60),
    "leisure=indoor_play": ("детский игровой центр", 45, 90),
    "leisure=miniature_golf": ("мини-гольф", 30, 45),
    "leisure=stadium": ("стадион", 15, 30),  # посмотреть снаружи; матчи — по расписанию
    "leisure=ice_rink": ("каток", 60, 90),
    "leisure=escape_game": ("квест", 60, 75),
    "leisure=bowling_alley": ("боулинг", 60, 90),
    "shop=mall": ("торговый центр", 60, 120),
    "shop=department_store": ("универмаг", 30, 60),
    "shop=books": ("книжный магазин", 20, 40),
}
# По пути к цели парк — это проход через него, а не отдельная прогулка: меньше времени на месте
PASS_THROUGH_VISIT = {"leisure=park": (15, 30), "leisure=garden": (10, 20)}
HISTORIC_DEFAULT = ("исторический объект", 10, 20)
OTHER_DEFAULT = ("место", 30, 60)

# Не для прогулки: абонементы и тренировки
NOT_FOR_WALK = {"leisure=fitness_centre", "leisure=sports_centre", "leisure=swimming_pool", "leisure=dance"}
# Рядовые мечети, клубы и стадионы не советуем — только известные (есть Wikidata или описание)
NOTABLE_ONLY = {"amenity=place_of_worship", "amenity=community_centre", "leisure=stadium"}
NOT_FOR_KIDS = {"amenity=bar", "amenity=pub", "amenity=nightclub"}
# Безымянные точки: детская площадка полезна и без названия, но только в сценарии «с ребёнком»
UNNAMED_FOR_KIDS = {"leisure=playground"}
KIDS_PLACES = {"tourism=museum", "amenity=planetarium", "amenity=ice_cream", "tourism=zoo", "tourism=aquarium",
               "tourism=theme_park", "leisure=playground", "leisure=water_park", "leisure=indoor_play",
               "leisure=trampoline_park", "leisure=miniature_golf", "leisure=amusement_arcade",
               "leisure=park", "leisure=garden", "leisure=ice_rink", "amenity=cinema"}
NIGHT_PLACES = {"amenity=bar", "amenity=pub", "amenity=nightclub", "amenity=cinema", "amenity=theatre",
                "amenity=restaurant", "leisure=bowling_alley", "leisure=escape_game"}
# Работают днём: если часы неизвестны, поздно вечером и ночью их не советуем
DAYTIME_PLACES = {"tourism=museum", "tourism=gallery", "amenity=library", "amenity=arts_centre",
                  "amenity=exhibition_centre", "amenity=place_of_worship", "amenity=community_centre",
                  "amenity=marketplace", "amenity=planetarium", "shop=mall", "shop=department_store", "shop=books",
                  "tourism=zoo", "tourism=aquarium"}
# «Что-то необычное»: эти места необычны по самой категории, даже без Wikidata
UNUSUAL_PLACES = {"leisure=escape_game", "amenity=planetarium", "tourism=viewpoint", "tourism=attraction",
                  "tourism=artwork", "tourism=gallery", "amenity=arts_centre", "tourism=aquarium",
                  "tourism=theme_park", "leisure=trampoline_park", "leisure=miniature_golf"}
WORK_PLACES = {"amenity=cafe", "amenity=coworking_space", "amenity=library"}  # посидеть с ноутбуком
ONE_PLACE_GROUPS = {"Кафе и еда", "Бары и ночь"}  # поесть, выпить — выбрать одно место, а не обойти три
OUTDOOR = {"leisure=park", "leisure=garden", "leisure=nature_reserve", "leisure=playground", "tourism=picnic_site",
           "tourism=artwork", "tourism=viewpoint", "amenity=fountain", "leisure=stadium"}

# Вес группы мест для обычной прогулки, когда интересы не указаны
DEFAULT_GROUP_WEIGHT = {"Достопримечательности": 0.9, "Парки": 0.85, "Культура": 0.8, "Кафе и еда": 0.35,
                        "ТРЦ и магазины": 0.4, "Для детей": 0.4, "Бары и ночь": 0.3,
                        "Спорт и активности": 0.3, "Коворкинги": 0.1}
# …и поправки внутри группы: библиотека — культура, но не прогулка; фастфуд — не цель прогулки
DEFAULT_CATEGORY_FACTOR = {"amenity=library": 0.6, "amenity=fast_food": 0.6, "shop=department_store": 0.6}


@dataclass
class Query:
    lat: float
    lon: float
    start_label: str = "выбранная точка"
    start_poi_id: str | None = None
    time_budget_min: int = 60
    with_kids: bool = False
    before_flight: bool = False
    interests: list[str] = field(default_factory=list)  # группы мест: «Культура», «Парки»…
    wants: list[str] = field(default_factory=list)      # coffee | night | unusual | quiet | socket
    at: datetime = field(default_factory=lambda: datetime.now(ASTANA_TZ))
    exclude_ids: list[str] = field(default_factory=list)  # «ещё варианты»: уже показанные места
    max_walk_min: int | None = None                       # «ближе»: предел пути до места
    # Цель пути («доведи до Байтерека»): маршрут из старта через места по пути и заканчивается в цели
    dest_lat: float | None = None
    dest_lon: float | None = None
    dest_label: str | None = None
    dest_poi_id: str | None = None
    # Разнообразие: показанное раньше в разговоре — ниже; seed разговора чуть перемешивает равные места
    seen_ids: list[str] = field(default_factory=list)
    seed: int = 0
    via_poi_id: str | None = None  # «через Байтерек»: место, которое обязательно войдёт в маршрут
    # «Укажи одно заведение»: одно место, и только из уже показанных
    max_items: int = MAX_ITEMS
    only_ids: list[str] = field(default_factory=list)

    @property
    def usable_min(self) -> int:
        return self.time_budget_min - (FLIGHT_RESERVE_MIN if self.before_flight else 0)

    @property
    def round_trip(self) -> bool:
        return self.before_flight  # к самолёту надо вернуться

    @property
    def has_destination(self) -> bool:
        return self.dest_lat is not None and self.dest_lon is not None and not self.round_trip

    @property
    def end(self) -> dict | None:
        """Где маршрут должен закончиться: у старта («до вылета»), в цели пути или где угодно (None)."""
        if self.round_trip:
            return {"lat": self.lat, "lon": self.lon}
        if self.has_destination:
            return {"lat": self.dest_lat, "lon": self.dest_lon}
        return None

    @property
    def direct_min(self) -> int:
        """Пешком от старта до цели напрямую; 0 — цели нет."""
        if not self.has_destination:
            return 0
        return walk_min(distance_m({"lat": self.lat, "lon": self.lon}, self.end), self.with_kids)

    @property
    def is_night(self) -> bool:
        return "night" in self.wants or is_late(self.at)


def category_info(category: str) -> tuple[str, int, int]:
    if category in CATEGORIES:
        return CATEGORIES[category]
    return HISTORIC_DEFAULT if category.startswith("historic=") else OTHER_DEFAULT


def is_outdoor(category: str) -> bool:
    return category in OUTDOOR or category.startswith("historic=")


def is_late(at: datetime) -> bool:
    """Вечер и ночь — одно определение для оценки, предупреждений и отсева дневных мест."""
    return at.hour >= 21 or at.hour < 7


def walk_min(dist_m: float, with_kids: bool = False) -> int:
    speed = KIDS_WALK_M_PER_MIN if with_kids else WALK_M_PER_MIN
    return max(1, math.ceil(dist_m * DETOUR / speed))


def end_leg(q: Query, pos: dict) -> int:
    """Последний отрезок: от места до конца маршрута (старт или цель пути); 0 — конец где угодно."""
    return walk_min(distance_m(pos, q.end), q.with_kids) if q.end else 0


def display_name(poi: dict) -> str:
    if poi["name"]:
        name = poi["name_ru"] or poi["name"]
        return name[:1].upper() + name[1:]  # в OSM бывает «мечеть "Хазрет Султан"» со строчной
    return f"{category_info(poi['category'])[0].capitalize()} (без названия в данных)"


def open_status(poi: dict, at: datetime) -> Status:
    if not poi["opening_hours"] and is_outdoor(poi["category"]):
        return Status("outdoor", "открытая территория, часы не указаны")
    return status_at(poi["opening_hours"], at)


class Catalog:
    """Места из data/poi.json + сети (одно название 3+ раз) — для ответов «необычное» и честной пометки."""

    def __init__(self, pois: list[dict]):
        self.pois = pois
        counts = Counter(norm_name(p["name"]) for p in pois if p["name"])
        self.chains = {name for name, n in counts.items() if n >= 3}

    @classmethod
    def load(cls, path: Path = POI_PATH) -> "Catalog":
        return cls(json.loads(path.read_text(encoding="utf-8"))["pois"])

    def is_chain(self, poi: dict) -> bool:
        return bool(poi["name"]) and norm_name(poi["name"]) in self.chains

    def recommendable(self, poi: dict, q: Query) -> bool:
        cat = poi["category"]
        if cat in NOT_FOR_WALK or poi["id"] == q.dest_poi_id:
            return False
        # «Я у Байтерека» — рядом, но ещё не наверху: достопримечательность у старта советуем первой.
        # ТРЦ или кафе, в котором человек стоит, — нет
        if poi["id"] == q.start_poi_id and group_of(cat) != "Достопримечательности":
            return False
        # То же место под тем же названием другой точкой: «я в Президентском парке» — не советуем его же.
        # Только если старт (цель) — место из базы; «маршрут от Байтерека» (без id) Байтерек советовать должен
        same_names = _named_ends(q.start_label, q.start_poi_id, q.dest_label, q.dest_poi_id)
        if poi["id"] != q.start_poi_id and poi["name"] and norm_name(display_name(poi)) in same_names:
            return False
        if q.has_destination and distance_m(poi, q.end) < AT_DESTINATION_M:
            return False
        if cat in NOTABLE_ONLY and not (poi["wikidata"] or poi["description"]):
            return False
        if q.with_kids and cat in NOT_FOR_KIDS:
            return False
        if not poi["name"]:
            return q.with_kids and cat in UNNAMED_FOR_KIDS
        return True

    # ---------- оценка ----------

    def interest(self, poi: dict, q: Query) -> tuple[float, list[str]]:
        cat, group = poi["category"], group_of(poi["category"])
        reasons = []
        if "coffee" in q.wants:
            value = 1.0 if cat == "amenity=cafe" else 0.4 if group == "Кафе и еда" else 0.1
            if cat == "amenity=cafe":
                reasons.append("coffee")
        elif "socket" in q.wants:  # «поработать с ноутбуком» — где можно посидеть, а не памятник
            value = 1.0 if cat in WORK_PLACES else 0.1
            if cat in WORK_PLACES:
                reasons.append("socket")
        elif q.interests:
            # «хочу в музей» — музей, а не библиотека: поправки внутри группы действуют и при явном интересе
            value = (1.0 if group in q.interests else 0.25) * DEFAULT_CATEGORY_FACTOR.get(cat, 1.0)
            if group in q.interests:
                reasons.append(f"interest:{group}")
        else:
            value = DEFAULT_GROUP_WEIGHT.get(group, 0.3) * DEFAULT_CATEGORY_FACTOR.get(cat, 1.0)
        if q.with_kids:
            if cat in KIDS_PLACES:
                value = max(value, 1.0)
                reasons.append("kids")
            elif group not in q.interests:  # «с ребёнком» + «достопримечательности» — их и показываем
                value *= 0.4
        if q.is_night:
            if cat in NIGHT_PLACES:
                value = max(value, 0.9 if "night" in q.wants else value)
                reasons.append("night")
            elif is_outdoor(cat) and group not in q.interests:
                value *= 0.5  # темно: парк в 23:00 — не лучшая идея, если о парках не просили прямо
        if "unusual" in q.wants:
            notable = poi["wikidata"] or poi["description"] or cat in UNUSUAL_PLACES
            if notable and not self.is_chain(poi):
                reasons.append("unusual")
            else:
                value *= 0.5
        if "tour" in q.wants:  # обзорная прогулка: главные места города — те, о которых есть статья
            if (poi["wikidata"] or poi["description"]) and not self.is_chain(poi) and group != "Кафе и еда":
                value = max(value, 1.0)
                reasons.append("tour")
            else:
                value *= 0.6
        return value, reasons

    def quality(self, poi: dict) -> float:
        value = 0.4 + 0.3 * bool(poi["wikidata"]) + 0.2 * bool(poi["description"]) \
            + 0.05 * bool(poi["opening_hours"]) + 0.05 * bool(poi["website"])
        return max(0.0, value - (0.15 if self.is_chain(poi) else 0))

    def status_on_arrival(self, poi: dict, arrival: datetime) -> Status | None:
        """Статус в момент прихода; None — идти бессмысленно: закрыто или не успеть побыть минимум."""
        status = open_status(poi, arrival)
        if status.state == "closed":
            return None
        if status.minutes_left is not None and status.minutes_left < category_info(poi["category"])[1]:
            return None  # открыто до 22:00, а приходим в 21:48 на детский центр от 45 минут
        if status.state == "unknown" and is_late(arrival) and poi["category"] in DAYTIME_PLACES:
            return None  # часы неизвестны, но галерея в 23:00 почти наверняка закрыта
        return status

    def candidates(self, q: Query) -> list[dict]:
        """Места, до которых можно дойти и побыть там хотя бы минимум, уложившись во время.

        Часы работы проверяются на момент прихода (старт + дорога), а не на момент старта.
        """
        out = []
        start = {"lat": q.lat, "lon": q.lon}
        excluded, seen, only = set(q.exclude_ids), set(q.seen_ids), set(q.only_ids)
        for poi in self.pois:
            if poi["id"] in excluded or only and poi["id"] not in only or not self.recommendable(poi, q):
                continue
            dist = distance_m(start, poi)
            walk = walk_min(dist, q.with_kids)
            if q.max_walk_min is not None and walk > q.max_walk_min:
                continue
            label, visit_lo, visit_hi = category_info(poi["category"])
            if q.has_destination and poi["category"] in PASS_THROUGH_VISIT:
                visit_lo, visit_hi = PASS_THROUGH_VISIT[poi["category"]]
            last = end_leg(q, poi)  # «до вылета» — обратно к старту, «доведи до…» — дальше к цели
            # Выбор одного места («хочу в бар»): сколько там сидеть, решает человек — важно только дойти.
            # Иначе бар в 8 минутах не влезал в час по умолчанию (визит от 60 мин), и советовался парк
            if walk + last + (0 if is_options_mode(q) else visit_lo) > q.usable_min:
                continue
            status = self.status_on_arrival(poi, q.at + timedelta(minutes=walk))
            if status is None:
                continue
            interest, reasons = self.interest(poi, q)
            closes_soon = status.minutes_left is not None and status.minutes_left < visit_hi
            open_factor = {"open": 1.0, "outdoor": 0.85, "unknown": 0.6}[status.state]
            if closes_soon:
                open_factor = 0.5
            if q.has_destination:  # по пути — это малый крюк, а не близость к старту
                proximity = 1 - (walk + last - q.direct_min) / max(q.usable_min - q.direct_min, 1)
            else:
                proximity = 1 - walk / max(q.usable_min, 1)
            score = 0.45 * interest + 0.25 * self.quality(poi) + 0.15 * proximity + 0.15 * open_factor
            if poi["id"] in seen:
                score -= SEEN_PENALTY  # уже советовали в этом разговоре — пусть будет что-то новое
            if q.seed:  # одинаковые запросы в разных разговорах — не всегда одна и та же двадцатка мест
                score += (zlib.crc32(f"{q.seed}:{poi['id']}".encode()) % 1000 / 1000 - 0.5) * JITTER
            out.append({"poi": poi, "dist_m": dist, "walk_min": walk, "label": label,
                        "visit": (visit_lo, visit_hi), "status": status, "closes_soon": closes_soon,
                        "score": score, "reasons": reasons})
        return sorted(out, key=lambda c: (-c["score"], c["poi"]["id"]))

    # ---------- маршрут ----------

    def matches_focus(self, c: dict, q: Query) -> bool:
        """Подходит ли место под явную просьбу: кофе, интересы, ребёнок, ночь."""
        cat = c["poi"]["category"]
        in_interests = group_of(cat) in q.interests
        if "coffee" in q.wants:
            return cat == "amenity=cafe"
        if "socket" in q.wants:
            return cat in WORK_PLACES
        if "night" in q.wants and cat not in NIGHT_PLACES:
            return False
        if q.with_kids:  # «площадки или музей»: детское место — или то, о чём попросили
            return cat in KIDS_PLACES or in_interests
        return in_interests or not q.interests

    def route(self, q: Query, cands: list[dict], top_n: int = 15, per_group: int = 4) -> list[dict]:
        """Мини-маршрут из 1–3 мест: перебор сочетаний среди лучших кандидатов.

        Цель — сумма оценок минус штраф за ходьбу; всё должно уложиться в usable_min
        (по нижней оценке времени визита), а «до вылета» — ещё и обратная дорога к старту.
        Разные группы мест (не три кафе подряд), если человек не назвал, что хочет: «красивые места» —
        это парки и достопримечательности, и три места из них — нормальный маршрут. Разные категории
        (не две площадки подряд) — кроме пути к цели: два парка по дороге — это хорошо.
        Одно название рядом — одно место.
        """
        focused = "coffee" in q.wants or bool(q.interests)
        pool, per = [], Counter()
        for c in cands:
            poi, group = c["poi"], group_of(c["poi"]["category"])
            if self._same_place_nearby(poi, pool):
                continue
            if not focused and per[group] >= per_group:
                continue
            pool.append(c)
            per[group] += 1
            if len(pool) == top_n:
                break
        # «Через Байтерек» — эта точка обязательна; не помещается во время — маршрут без неё
        must = next((c for c in cands if c["poi"]["id"] == q.via_poi_id), None) if q.via_poi_id else None
        if must is not None and must not in pool:
            pool = [must] + pool[:top_n - 1]
        best = self._best_plan(q, pool, focused, must)
        if not best and must is not None:
            best = self._best_plan(q, pool, focused, None)
        return [{**c, "leg_min": leg, "leg_m": leg_m, "status": status,
                 "closes_soon": status.minutes_left is not None and status.minutes_left < c["visit"][1]}
                for c, leg, leg_m, status in best]

    def _best_plan(self, q: Query, pool: list[dict], focused: bool, must: dict | None) -> list:
        best, best_value = [], float("-inf")
        for k in range(1, q.max_items + 1):
            for combo in combinations(pool, k):
                if must is not None and must not in combo:
                    continue
                if not q.has_destination and len({c["poi"]["category"] for c in combo}) < k:
                    continue  # две детские площадки подряд — не маршрут
                if not focused and len({group_of(c["poi"]["category"]) for c in combo}) < k:
                    continue
                plan = self._plan(q, combo)
                if plan is None:
                    continue
                walk = sum(leg for _, leg, *_ in plan)
                if q.has_destination:  # к цели идти всё равно: штрафуем полный путь, включая последний отрезок
                    walk += end_leg(q, plan[-1][0]["poi"])
                # место выше порога добавляет ценность, ниже — только отнимает время
                value = sum(c["score"] - ROUTE_BASELINE for c in combo) - 0.3 * walk / max(q.usable_min, 1)
                if value > best_value:
                    best, best_value = plan, value
        return best

    def options(self, q: Query, cands: list[dict]) -> list[dict]:
        """Варианты на выбор (не маршрут): «кофе рядом» — три кафе, идти в одно.

        Каждое место — от старта; одно название рядом — один вариант.
        """
        chosen = []
        for c in cands:
            if self._same_place_nearby(c["poi"], chosen):
                continue
            chosen.append({**c, "leg_min": c["walk_min"], "leg_m": c["dist_m"]})
            if len(chosen) == q.max_items:
                break
        return chosen

    @staticmethod
    def _same_place_nearby(poi: dict, chosen: list[dict]) -> bool:
        """Одно название в радиусе DUP_NAME_RADIUS_M — одно место в выдаче (Ailand: океанариум и аквапарк)."""
        return bool(poi["name"]) and any(norm_name(poi["name"]) == norm_name(x["poi"]["name"])
                                         and distance_m(poi, x["poi"]) <= DUP_NAME_RADIUS_M for x in chosen)

    def _plan(self, q: Query, combo: tuple[dict, ...]) -> list[tuple[dict, int, float, Status]] | None:
        """Порядок обхода с минимальной ходьбой, если он укладывается во время, иначе None.

        Каждое место проверяется на открытость в момент прихода: второе место маршрута
        посещают через час-два после старта. Ходьба считается до конца маршрута: с возвратом
        к старту («до вылета») или до цели пути — поэтому места идут по ходу движения к цели.
        """
        start = {"lat": q.lat, "lon": q.lon}
        best, best_walk = None, None
        for order in permutations(combo):
            pos, legs, walk, elapsed = start, [], 0, 0
            for c in order:
                leg_m = distance_m(pos, c["poi"])
                leg = walk_min(leg_m, q.with_kids)
                elapsed += leg
                status = self.status_on_arrival(c["poi"], q.at + timedelta(minutes=elapsed))
                if status is None:
                    break
                legs.append((c, leg, leg_m, status))
                walk += leg
                elapsed += c["visit"][0]
                pos = c["poi"]
            else:
                back = end_leg(q, pos)
                if elapsed + back <= q.usable_min and (best_walk is None or walk + back < best_walk):
                    best, best_walk = legs, walk + back
        return best


@lru_cache(maxsize=256)
def _named_ends(start_label: str, start_pid: str | None, dest_label: str | None, dest_pid: str | None) -> frozenset:
    """Названия старта и цели, если это места из базы, — один раз на запрос, а не на каждое из 1872 мест."""
    return frozenset(norm_name(label) for label, pid in ((start_label, start_pid), (dest_label, dest_pid))
                     if label and pid)


def is_options_mode(q: Query) -> bool:
    """Выбрать одно место из нескольких, а не обойти все: кофе, поработать, поесть, бар.

    «Где поужинать» — не маршрут «кофейня → мороженое», а несколько ресторанов на выбор.
    """
    return bool({"coffee", "socket"} & set(q.wants)) or bool(q.interests) and set(q.interests) <= ONE_PLACE_GROUPS


def summary(q: Query, chosen: list[dict]) -> dict:
    """Итог маршрута: сколько идти, сколько на местах, когда закончите."""
    if is_options_mode(q):
        return {"mode": "options", "usable_min": q.usable_min,
                "destination": q.dest_label if q.has_destination else None}
    walk = sum(c["leg_min"] for c in chosen)
    back = end_leg(q, chosen[-1]["poi"]) if chosen else q.direct_min  # к старту («до вылета») или к цели
    lo = walk + back + sum(c["visit"][0] for c in chosen)
    # Верхняя граница — не дольше бюджета: маршрут подобран под него, задержаться можно до конца
    # отведённого времени. Иначе «у меня 90 мин» → «маршрут на 74–134 мин», будто не помещается.
    hi = max(lo, min(walk + back + sum(c["visit"][1] for c in chosen), q.usable_min))
    finish = lambda m: (q.at + timedelta(minutes=m)).strftime("%H:%M")  # noqa: E731
    return {"mode": "route", "walk_min": walk, "return_min": back, "total_min": [lo, hi],
            "fits_budget": lo <= q.usable_min, "finish_at": [finish(lo), finish(hi)],
            "usable_min": q.usable_min, "destination": q.dest_label if q.has_destination else None}


def nearest(catalog: Catalog, q: Query, n: int = 3) -> list[dict]:
    """Ближайшие места с названием — для честного ответа, когда в бюджет ничего не влезло."""
    start = {"lat": q.lat, "lon": q.lon}
    excluded = set(q.exclude_ids)  # «без Ритц Карлтона» — и в подсказке «ближайшее» его нет
    named = [p for p in catalog.pois if p["name"] and p["category"] not in NOT_FOR_WALK and p["id"] not in excluded]
    named.sort(key=lambda p: distance_m(start, p))
    return [{"name": display_name(p), "category_label": category_info(p["category"])[0],
             "dist_m": round(distance_m(start, p)), "walk_min": walk_min(distance_m(start, p), q.with_kids)}
            for p in named[:n]]


def load_landmarks(path: Path = LANDMARKS_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["landmarks"]
