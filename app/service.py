"""Диалог: реплика человека → ответ ассистента с 1–3 местами, маршрутом и честными примечаниями.

Контекст разговора (где человек, сколько у него времени, с ребёнком ли, что уже показали) живёт
у клиента: сервер возвращает его в `state`, клиент присылает его со следующей репликой. На сервере
ничего не хранится; присланный `state` проверяется, как любые данные от клиента.
Точные координаты пользователя округляются до ~100 м.
"""

import logging
import re
from collections import deque
from collections.abc import Callable
from datetime import datetime, time, timedelta
from threading import Lock
from time import monotonic

from app import llm as llm_mod
from app import texts
from app.engine import (ASTANA_TZ, FLIGHT_RESERVE_MIN, MAX_ITEMS, Catalog, Query, category_info, display_name,
                        is_options_mode, nearest, summary, walk_min)
from app.places import PlaceIndex, label_of, mentioned
from app.query import (GROUPS, INTEREST_WORDS, VIA_TAIL, WANT_LABELS, WANT_WORDS, WANTS, WITHOUT, clean, describe,
                       expand_aliases, find_landmark, is_general_request, normalize_at, ordinal_indexes, parse_minutes,
                       parse_rules)
from pipeline.build_poi import distance_m

COORD_DIGITS = 3            # ~100 м: достаточно для подбора, недостаточно, чтобы узнать подъезд
MAX_FROM_CENTER_M = 30_000  # дальше — не Астана, данных нет
LLM_CALLS_PER_MIN = 20      # на весь сервер; сверх — правила и шаблоны
DEFAULT_BUDGET_MIN = 60
DEST_STOPS_MIN = 60   # «доведи до Байтерека» без времени: дорога напрямую + час на остановки и крюк
MAX_DEST_WALK_MIN = 120  # дальше пешком не ведём: из аэропорта до Жагалау — 4,5 часа ходьбы
SAME_POINT_M = 300    # старт и цель ближе — это одно место: «я у Байтерека» после «до Байтерека» — пришли
# Обзорная прогулка («всю Астану»): без времени — 3 часа; если рядом меньше 3 главных мест (у вокзалов) —
# честно «главное в центре» и маршрут от Байтерека
TOUR_BUDGET_MIN = 180
TOUR_NEAR_M = 2000
TOUR_MIN_NOTABLE = 3
LRT_HINT_MAX_MIN = 25  # станцию ЛРТ советуем, если до неё не дольше 25 мин пешком
FOCUS_BUDGET_MIN = 150  # конкретная просьба без времени: кино, театр, «красивые места»
FOCUS_MAX_WALK_MIN = 30  # …но до места не дальше получаса пешком
GREETING = re.compile(r"^\W*(?:привет|здравствуй|добр(?:ый|ое|ого)|салем|сәлем|hello|hi)\b")
NEAR_THERE = re.compile(r"рядом|поблизост|возле|около|недалеко")
PRICE = re.compile(r"бесплат|дешев|недорог|\bцен[аыу]\b|сколько\s+стоит")
# Короткую реплику, которую правила поняли сами («Ещё варианты», «А с ребёнком?», «я у Байтерека»),
# не отправляем в Gemini: бесплатная квота мала. С отрицанием («без баров») — к модели.
SHORT_REPLY_WORDS = 3
NEGATION = re.compile(r"\b(?:не|без|кроме)\b")
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
POINT_LABELS = {"map": "точка на карте", "geo": "вы здесь (±100 м)"}
NAME_FIELDS = ("name", "name_ru", "name_en", "name_kk")
# Причина сбоя Gemini (таймаут, код ответа) — в журнал сервера; ни ключа, ни текста реплики в ней нет
log = logging.getLogger(__name__)


class RequestError(ValueError):
    pass


def fresh_state() -> dict:
    return {"start": None, "destination": None, "time_budget_min": None, "with_kids": False,
            "before_flight": False, "interests": [], "wants": [], "at": None, "exclude_ids": [], "shown_ids": [],
            "max_walk_min": None, "last_max_leg": None,
            "route_end": None,  # последнее место прошлого маршрута — «дальше» продолжает оттуда
            "seen_ids": [],     # всё, что уже советовали в разговоре, — чтобы не повторяться
            "seed": 0,          # случайное число разговора от браузера; 0 — без перемешивания (тесты, отчёт)
            "at_user": False,   # время назвал человек («а в 22:00?») — время из ссылки его не перебивает
            "skip_ids": [],     # «без Ритц Карлтона»: убранные человеком места — не предлагать до «сначала»
            "via": None}        # «через Байтерек»: обязательная точка, пока не сменится план


def _int_or_none(value, lo: int, hi: int) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi else None


def _ids(value, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str) and 0 < len(v) <= 80][-limit:]


def _point(raw) -> dict | None:
    """Точка из контекста клиента (старт или цель пути) → координаты в рамке Астаны, подпись, id места."""
    if not isinstance(raw, dict):
        return None
    lat, lon = raw.get("lat"), raw.get("lon")
    if not (all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lat, lon))
            and 50 <= lat <= 52 and 70 <= lon <= 73):
        return None
    poi_id = raw.get("poi_id") if isinstance(raw.get("poi_id"), str) else None
    digits = 7 if poi_id else COORD_DIGITS  # координаты места из базы — не личные данные
    label = raw.get("label") if isinstance(raw.get("label"), str) else "выбранная точка"
    return {"lat": round(float(lat), digits), "lon": round(float(lon), digits),
            "label": label[:100], "poi_id": poi_id[:80] if poi_id else None}


def sanitize_state(raw) -> dict:
    """Контекст от клиента → только допустимые значения; всё непонятное отбрасывается."""
    st = fresh_state()
    if not isinstance(raw, dict):
        return st
    st["start"] = _point(raw.get("start"))
    st["destination"] = _point(raw.get("destination"))
    st["time_budget_min"] = _int_or_none(raw.get("time_budget_min"), 10, 600)
    st["with_kids"] = raw.get("with_kids") is True
    st["before_flight"] = raw.get("before_flight") is True
    st["interests"] = [g for g in raw.get("interests") or [] if g in GROUPS][:5] \
        if isinstance(raw.get("interests"), list) else []
    st["wants"] = [w for w in raw.get("wants") or [] if w in WANTS][:5] if isinstance(raw.get("wants"), list) else []
    at = raw.get("at")
    st["at"] = at if isinstance(at, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at) else None
    st["exclude_ids"] = _ids(raw.get("exclude_ids"), 60)
    st["shown_ids"] = _ids(raw.get("shown_ids"), 3)
    st["max_walk_min"] = _int_or_none(raw.get("max_walk_min"), 1, 120)
    st["last_max_leg"] = _int_or_none(raw.get("last_max_leg"), 1, 600)
    st["route_end"] = _point(raw.get("route_end"))
    st["seen_ids"] = _ids(raw.get("seen_ids"), 60)
    st["seed"] = _int_or_none(raw.get("seed"), 1, 2**31 - 1) or 0
    st["at_user"] = raw.get("at_user") is True and st["at"] is not None
    st["skip_ids"] = _ids(raw.get("skip_ids"), 20)
    st["via"] = _point(raw.get("via"))
    return st


class Assistant:
    def __init__(self, catalog: Catalog, landmarks: list[dict], gemini: llm_mod.Gemini | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(ASTANA_TZ), areas: list[dict] = ()):
        self.catalog = catalog
        self.places = PlaceIndex(catalog, areas)  # места + районы, ЖК и улицы: «до Жагалау», «я на Кенесары»
        self.catalog_ids = {p["id"] for p in catalog.pois}  # места, которые можно включить в маршрут
        self.landmarks = {lm["key"]: lm for lm in landmarks}
        self._landmark_by_poi = {lm["poi_id"]: lm for lm in landmarks if lm.get("poi_id")}
        self.gemini = gemini
        self.clock = clock  # подменяется в отчёте по сценариям: часы работы зависят от дня недели
        self._llm_calls: deque[float] = deque()
        self._llm_lock = Lock()

    def _llm_allowed(self) -> bool:
        """Общий предел вызовов Gemini в минуту: сайт по QR не должен выжечь бесплатную квоту ключа.

        Считаем только моменты вызовов, без адресов и запросов пользователей.
        """
        if not self.gemini:
            return False
        with self._llm_lock:
            now = monotonic()
            while self._llm_calls and now - self._llm_calls[0] > 60:
                self._llm_calls.popleft()
            if len(self._llm_calls) >= LLM_CALLS_PER_MIN:
                return False
            self._llm_calls.append(now)
            return True

    # ---------- разбор реплики ----------

    def _parse(self, text: str, st: dict) -> tuple[dict, str]:
        rules = clean(parse_rules(text))
        if self._rules_enough(text, rules) or not self._llm_allowed():
            return rules, "rules"
        context = {"place": (st["start"] or {}).get("label"), "destination": (st["destination"] or {}).get("label"),
                   "time_budget_min": st["time_budget_min"], "with_kids": st["with_kids"],
                   "interests": st["interests"], "wants": st["wants"]}
        try:
            parsed = clean(llm_mod.parse_query(self.gemini, text, context))
        except llm_mod.LLMError as exc:
            log.warning("Gemini, разбор реплики: %s", exc)
            return rules, "rules"
        parsed["one"] = parsed["one"] or rules["one"]  # «укажи одно заведение» — по правилам надёжнее
        # Интересы по ключевым словам надёжны («по красивым местам» → парки и достопримечательности),
        # а модель их теряла (вернула только достопримечательности). Без отрицания — добавляем к ответу модели
        if not NEGATION.search(text.lower()):
            parsed["interests"] += [g for g in rules["interests"] if g not in parsed["interests"]]
        return parsed, "gemini"

    def _rules_enough(self, text: str, rules: dict) -> bool:
        t = text.lower()
        if len(re.findall(r"\w+", t)) > SHORT_REPLY_WORDS or NEGATION.search(t):
            return False
        return (any(rules.values()) or is_general_request(t) or self.places.find(text) is not None
                or self.places.find(text, destination=True) is not None)

    def _landmark_point(self, key: str | None) -> dict | None:
        lm = self.landmarks.get(key) if key else None
        return {"lat": lm["lat"], "lon": lm["lon"], "label": lm["label"], "poi_id": lm["poi_id"]} if lm else None

    def _find_start(self, text: str, parsed: dict, avoid: list[dict | None] = ()) -> dict | None:
        """Ориентир → место по названию из ответа Gemini → место по названию во всём тексте.

        Шаблоны ориентиров проверяются и при ответе Gemini: дёшево, и модель могла не вернуть ключ.
        avoid — цель пути из этой же реплики: «доведи до Байтерека» не значит «я у Байтерека»,
        даже если модель так решила.
        """
        def candidates():
            yield self._landmark_point(parsed["landmark"] or (find_landmark(text) if text else None))
            if parsed["place"]:
                yield self._named_point(parsed["place"])
            if text:
                yield self.places.find(text)

        for found in candidates():
            if found and not any(a and distance_m(found, a) < SAME_POINT_M for a in avoid):
                return found
        return None

    def _named_point(self, name: str) -> dict | None:
        """Место по названию из ответа Gemini. «Байтерек» — ориентир, а не микрорайон «Байтерек»
        в Косшы за 16 км с тем же названием."""
        key = find_landmark(name)
        return self._landmark_point(key) if key else self.places.find(name, skip_destinations=False)

    def _said_as_location(self, text: str, point: dict) -> bool:
        """Место названо как «где» («у Байтерека», «возле Оперы»), а не «куда» («до Байтерека»)?

        По правилам: ориентир или место в тексте без «до / к / хочу в» перед ним — там же, где point.
        """
        for found in (self._landmark_point(find_landmark(text)), self.places.find(text)):
            if found and distance_m(found, point) < SAME_POINT_M:
                return True
        return False

    @staticmethod
    def _is_kind_of_place(name: str) -> bool:
        """«кино», «бар», «музей», «домой» — вид места или направление, а не название."""
        low = name.lower()
        return (any(re.search(p, low) for p in (*INTEREST_WORDS.values(), *WANT_WORDS.values()))
                or low.strip() in {"дом", "домой", "туда", "обратно", "центр", "здесь", "тут", "рядом"})

    def _dropped(self, text: str, st: dict) -> tuple[list[dict], str]:
        """«Без Ритц Карлтона», «убери второе» → показанные места, которые человек просит убрать, и реплика без этого.

        Сначала среди показанных мест: по номеру в ответе, по названию на любом языке, по описанию (в карточке
        Talan Towers написано «Ritz-Carlton» — так человек его и назвал). Потом — любое место базы по названию:
        «не хочу в Линейный парк» раньше делало его целью пути. Район или улицу («не хочу в Жагалау») из подбора
        не уберёшь, но из реплики убираем: это не старт и не цель.
        """
        shown = [self.places.by_id(i) for i in st["shown_ids"]]
        dropped, rest, stripped = [], text, False
        for m in WITHOUT.finditer(text):
            found = False
            for said in re.split(r"\s+(?:и|или)\s+", m.group(1).lower()):  # «без Ритц Карлтона и второго»
                hits = ([shown[i] for i in ordinal_indexes(said) if i < len(shown)]
                        or [p for p in shown if p and any(mentioned(said, p.get(f)) for f in NAME_FIELDS)]
                        or [p for p in shown if p and mentioned(said, p.get("description"), proper_only=True)])
                named = None if hits else self._named_point(said)
                if named and named.get("poi_id") in self.catalog_ids:
                    hits = [self.places.by_id(named["poi_id"])]
                dropped += [p for p in hits if p and p not in dropped]
                found = found or bool(hits or named)
            if found:
                rest = rest[:m.start()] + " " * (m.end() - m.start()) + rest[m.end():]  # длина та же
                stripped = True
        return dropped, (re.sub(r"\s+", " ", rest).strip(" ,.;!?-—") if stripped else text)

    def _find_via(self, text: str) -> dict | None:
        """«Через Байтерек» → место из базы (нужен его id, чтобы включить в маршрут); None — нет такого."""
        found = self._landmark_point(find_landmark(text, via=True)) or self.places.find(text, via=True)
        return found if found and found.get("poi_id") in self.catalog_ids else None

    def _find_destination(self, text: str, parsed: dict) -> dict | None:
        """Куда человек идёт: «доведи до Байтерека», «хочу в Хан Шатыр», «к Астана Опере».

        parsed["destination"] — ключ ориентира (правила) или название, как его написал человек (Gemini).
        """
        name = parsed["destination"]
        if name:
            found = self._landmark_point(name) or self._named_point(name)
            if found:
                return found
        if not text:
            return None
        return self._landmark_point(find_landmark(text, destination=True)) or self.places.find(text, destination=True)

    def _trusted_start(self, start: dict | None) -> dict | None:
        """Место старта из контекста клиента → из наших данных; свободный текст клиента дальше не идёт.

        Место из базы — координаты и название из базы. Иначе координаты округляются, а подпись — одна
        из наших (точка на карте, ориентир). Дальше 30 км от центра — старт забывается.
        """
        if not start:
            return None
        poi = self.places.by_id(start["poi_id"]) if start["poi_id"] else None
        if poi:
            lm = self._landmark_by_poi.get(poi["id"])
            return {"lat": poi["lat"], "lon": poi["lon"], "label": lm["label"] if lm else label_of(poi),
                    "poi_id": poi["id"]}
        point = {"lat": round(start["lat"], COORD_DIGITS), "lon": round(start["lon"], COORD_DIGITS)}
        center = self.landmarks.get("baiterek")
        if center and distance_m(point, center) > MAX_FROM_CENTER_M:
            return None
        known = {lm["label"] for lm in self.landmarks.values()} | set(POINT_LABELS.values())
        label = start["label"] if start["label"] in known else "выбранная точка"
        return {**point, "label": label, "poi_id": None}

    def _check_in_city(self, point: dict) -> None:
        center = self.landmarks.get("baiterek")
        if center and distance_m(point, center) > MAX_FROM_CENTER_M:
            raise RequestError("пока работаем только по Астане")

    def _at(self, value: str | None) -> datetime:
        now = self.clock()
        if not value:
            return now
        try:
            hh, mm = map(int, value.split(":"))
            at = datetime.combine(now.date(), time(hh, mm), ASTANA_TZ)
        except ValueError as exc:
            raise RequestError("время в формате ЧЧ:ММ") from exc
        if at < now - timedelta(hours=12):
            at += timedelta(days=1)  # «01:00», выбранное в пятницу в 23:50, — ночь на субботу, а не прошлая
        return at

    # ---------- диалог ----------

    def chat(self, message: str = "", state: dict | None = None, point: dict | None = None,
             at: str | None = None) -> dict:
        """Реплика (и/или точка на карте) + контекст → ответ и новый контекст."""
        st = sanitize_state(state)
        st["start"] = self._trusted_start(st["start"])
        st["route_end"] = self._trusted_start(st["route_end"])
        if st["via"] and st["via"]["poi_id"] not in self.catalog_ids:
            st["via"] = None
        had_destination = st["destination"] is not None
        if at is not None:
            at = normalize_at(at)
            if at is None:
                raise RequestError("время в формате ЧЧ:ММ")
        text = expand_aliases((message or "").strip()[:500])  # «с АДП до Ханша» → Абу-Даби Плаза, Хан Шатыр
        said = text
        # «Без Ритц Карлтона, хочу кофе» → убрать показанное место, а разбирать дальше только «хочу кофе»:
        # иначе «без Байтерека» стало бы «я у Байтерека»
        dropped, text = self._dropped(text, st)
        parsed, parser = self._parse(text, st) if text else (clean({}), None)
        prev_at = st["at"]
        destination = self._find_destination(text, parsed) if text else None
        via = self._find_via(text) if text else None  # «через Байтерек»: обязательная точка, не старт
        if destination and via and distance_m(destination, via) < SAME_POINT_M:
            destination = None  # вживую модель поняла «через арку» как «куда: арка» и сменила цель пути
            parsed["destination"] = None
        # Цель названа, а в данных её нет («до набережной на правом берегу») — скажем честно, а не подставим
        # похожую улицу и не промолчим. «Хочу в кино» — не место, а вид мест: это не про цель
        unknown_dest = (parsed["destination"] if text and parsed["destination"] and not destination
                        and not self._is_kind_of_place(parsed["destination"]) else None)
        # «Через набережную» — такого места нет: не строим молча маршрут без неё. «Через час» — это время
        via_said = VIA_TAIL.search(text) if text and not via else None
        unknown_via = (via_said.group(1).strip() if via_said and not parse_minutes(via_said.group(0))
                       and not self._is_kind_of_place(via_said.group(1)) else None)
        prev_start, walk_here = st["start"], None
        if destination and self._said_as_location(text, destination):
            # «хочу прям у Байтерека» — гулять там, а не идти туда: модель приняла это за цель пути
            walk_here, destination = destination, None
        near_there = None
        if not walk_here and destination and not st["start"] and not point and NEAR_THERE.search(text.lower()):
            # «Еду к Хан Шатыру — где рядом площадки?» — где он сейчас, неважно: нужно то, что рядом с целью
            near_there, destination = {**destination, "poi_id": None}, None
            near_there.pop("points", None)

        start_changed = False
        if point:
            start = {"lat": round(float(point["lat"]), COORD_DIGITS), "lon": round(float(point["lon"]), COORD_DIGITS),
                     "label": POINT_LABELS.get(point.get("source"), "точка на карте"), "poi_id": None}
            self._check_in_city(start)
            st["start"], start_changed = start, True
        else:
            found = self._find_start(text, parsed, avoid=[destination, via])
            if found:
                found.pop("points", None)  # старт на улице — её середина; точки вдоль улицы в контекст не берём
                st["start"], start_changed = found, True
            elif near_there:
                st["start"], start_changed = near_there, True
            elif parsed["next"] and st["route_end"]:
                st["start"] = st["route_end"]  # «дальше» — следующая часть маршрута от последнего места
        # Человек назвал, где он, а в данных такого места нет («я на правом берегу») — не подбираем молча
        # от прежней точки на другом берегу, а честно переспрашиваем. Место есть, но это цель или «через»
        # (модель спутала «до / через Байтерек» с «я у Байтерека») — не переспрашиваем, старт прежний
        unknown_start = (parsed["place"] if text and parsed["place"] and not start_changed and not point
                         and not self._is_kind_of_place(parsed["place"])
                         and not self._named_point(parsed["place"]) else None)
        if parsed["reset"]:
            keep, seed = st["start"], st["seed"]
            st = fresh_state()
            st["start"], st["seed"] = keep, seed
        dest_changed = False
        if destination:
            points = destination.pop("points", None)
            if points and st["start"]:  # «до Кабанбай батыра» — до ближайшей части улицы, а не до её середины
                lat, lon = min(points, key=lambda p: distance_m({"lat": p[0], "lon": p[1]}, st["start"]))
                destination.update(lat=lat, lon=lon)
            st["destination"], dest_changed = destination, True
        if st["destination"] and st["start"] and distance_m(st["start"], st["destination"]) < SAME_POINT_M:
            st["destination"] = None  # пришли (или цель — там же, где старт): дальше обычная прогулка
            dest_changed = False
        # «Не хочу в Хан Шатыр» по дороге к Хан Шатыру — цели больше нет
        dest_dropped = bool(st["destination"]) and any(
            p["id"] == st["destination"]["poi_id"] or distance_m(p, st["destination"]) < SAME_POINT_M for p in dropped)
        if dest_dropped:
            st["destination"], dest_changed = None, True
        if via:
            st["via"] = {k: via[k] for k in ("lat", "lon", "label", "poi_id")}
        elif (start_changed or dest_changed or parsed["next"] or (had_destination and not st["destination"])
              or any(st["via"] and p["id"] == st["via"]["poi_id"] for p in dropped)):
            st["via"] = None  # новый план, пришли или идём дальше: «через Байтерек» относилось к прошлому маршруту

        if parsed["at"]:
            st["at"], st["at_user"] = parsed["at"], True
        elif at and not st["at_user"]:
            # время из ссылки (для демо: ?at=15:00) приходит с каждой репликой — но «а в 22:00?» человека важнее
            st["at"] = at
        at_changed = st["at"] != prev_at  # «а в 21:00?» — новая просьба; то же время из ссылки — нет
        prefs_changed = bool(parsed["time_budget_min"] or parsed["with_kids"] or parsed["before_flight"]
                             or parsed["interests"] or parsed["wants"] or at_changed or dest_changed or via)
        if parsed["time_budget_min"]:
            st["time_budget_min"] = parsed["time_budget_min"]
        if parsed["with_kids"]:
            st["with_kids"] = True
        if parsed["before_flight"]:
            st["before_flight"] = True
        if parsed["interests"]:
            st["interests"] = parsed["interests"]
            if not parsed["wants"]:
                # «хочу кофе» → «достопримечательности»: кофе перебивает любые интересы, и снова шли три кафе
                st["wants"] = [w for w in st["wants"] if w not in ("coffee", "socket")]
        if parsed["wants"]:
            st["wants"] = parsed["wants"]

        if parsed["more"] or parsed["next"]:
            st["exclude_ids"] = (st["exclude_ids"] + st["shown_ids"])[-60:]
        elif start_changed or prefs_changed or parsed["reset"]:
            st["exclude_ids"] = []
        if dropped:
            st["skip_ids"] = (st["skip_ids"] + [p["id"] for p in dropped if p["id"] not in st["skip_ids"]])[-20:]
        if parsed["closer"]:
            st["max_walk_min"] = max(5, (st["last_max_leg"] or 20) // 2)
        elif start_changed or prefs_changed or parsed["reset"]:
            st["max_walk_min"] = None  # «ближе» относилось к прошлому ответу, новая просьба — с чистого листа

        labels = {key: lm["label"] for key, lm in self.landmarks.items()}
        dropped_names = ", ".join(f"«{display_name(p)}»" for p in dropped)
        understood = {"text": said, "parser": parser or ("rules" if dropped else None),
                      "parsed": ((describe(parsed, labels) if text else []) + ([f"через: {via['label']}"] if via else [])
                                 + ([f"без {dropped_names}"] if dropped else []))}
        follow_up = (parsed["more"] or parsed["closer"] or parsed["reset"] or parsed["next"] or parsed["one"]
                     or bool(dropped))
        # «Укажи одно заведение» — лучшее из только что показанных, а не новое место
        one_of = st["shown_ids"] if parsed["one"] and not (start_changed or prefs_changed) else []
        acted = start_changed or prefs_changed or follow_up or (bool(text) and is_general_request(text))

        if parsed["thanks"] and not acted:
            return self._message("Пожалуйста! Если захотите ещё куда-нибудь — напишите.", st, understood)
        if not st["start"]:
            greet = "Привет! Подскажу, куда сходить в Астане. " if GREETING.search(text.lower()) else ""
            return self._message(greet + texts.ask_where(describe(parsed, labels)), st, understood)
        if unknown_start:
            return self._message(f"Не нашёл «{unknown_start}» в наших данных. Нажмите на карту, где вы, или назовите "
                                 "место рядом — парк, улицу или ориентир.", st, understood)
        if text and not acted:
            if unknown_via:
                return self._message(f"Не нашёл «{unknown_via}» в наших данных. Назовите место рядом с ним — "
                                     "парк, памятник или ориентир, например «через Байтерек».", st, understood)
            if unknown_dest:
                return self._message(f"Не нашёл «{unknown_dest}» в наших данных. Назовите место рядом с ним — "
                                     "парк, улицу или ориентир, например «до Байтерека».", st, understood)
            return self._message(texts.NOT_UNDERSTOOD, st, understood)
        if st["before_flight"] and (st["time_budget_min"] or 120) - FLIGHT_RESERVE_MIN < 10:
            return self._message(f"До вылета нужно больше {FLIGHT_RESERVE_MIN + 10} минут: час оставляем "
                                 "на дорогу в терминал и регистрацию. Сколько у вас времени?", st, understood)

        lead_notes = []
        if dropped:
            lead_notes.append(f"Убрал {dropped_names} — в этом разговоре больше не предложу."
                              + (" Маршрут туда отменил — вот места рядом с вами." if dest_dropped else ""))
        if unknown_via:
            lead_notes.append(f"«{unknown_via}» в наших данных не нашёл — маршрут без этой точки. Назовите место "
                              "рядом с ней, например «через Байтерек».")
        if unknown_dest:
            lead_notes.append(f"«{unknown_dest}» в наших данных не нашёл — подобрал места рядом с вами. Чтобы идти "
                              "туда, назовите место рядом с ним: парк, улицу или ориентир.")
        if walk_here and prev_start and st["start"] and distance_m(prev_start, st["start"]) > TOUR_NEAR_M:
            km = f"{distance_m(prev_start, st['start']) / 1000:.1f}".replace(".", ",")
            lead_notes.append(f"Прогулка у «{st['start']['label']}»: от точки, где вы были, {km} км — доберитесь "
                              f"на такси или автобусе.{self._lrt_hint(prev_start, st['start'])}")
            st["start"] = {**st["start"], "poi_id": None}  # человек там ещё не был: сам Байтерек — первая остановка
        if st["before_flight"] and "tour" in st["wants"]:
            st["wants"].remove("tour")  # «2 часа до вылета, что посмотреть» — не повод ехать в центр за 12 км
        tour = "tour" in st["wants"] and not st["destination"]
        if tour and (start_changed or prefs_changed) and not parsed["next"]:
            center = self._tour_center(st["start"])
            if center:
                st["start"], note = center
                lead_notes.append(note)

        dest = st["destination"] or {}
        default_budget = 120 if st["before_flight"] else TOUR_BUDGET_MIN if tour else DEFAULT_BUDGET_MIN
        via_id = (st["via"] or {}).get("poi_id")
        q = Query(lat=st["start"]["lat"], lon=st["start"]["lon"], start_label=st["start"]["label"],
                  start_poi_id=st["start"]["poi_id"], time_budget_min=st["time_budget_min"] or default_budget,
                  with_kids=st["with_kids"], before_flight=st["before_flight"], interests=list(st["interests"]),
                  wants=list(st["wants"]), at=self._at(st["at"]),
                  # «ещё варианты» убирает показанное, но не обязательную точку маршрута
                  exclude_ids=[i for i in st["exclude_ids"] + st["skip_ids"] if i != via_id],
                  max_walk_min=st["max_walk_min"], dest_lat=dest.get("lat"), dest_lon=dest.get("lon"),
                  dest_label=dest.get("label"), dest_poi_id=dest.get("poi_id"),
                  # «без Ритц Карлтона» — тот же маршрут без одного места: остальные показанные не штрафуем
                  seen_ids=[i for i in st["seen_ids"] if not ((dropped or one_of) and i in st["shown_ids"])],
                  seed=st["seed"], via_poi_id=via_id, max_items=1 if parsed["one"] else MAX_ITEMS,
                  only_ids=list(one_of))
        budget_assumed = q.has_destination and not st["time_budget_min"]
        if budget_assumed:
            q.time_budget_min = q.direct_min + DEST_STOPS_MIN
        elif not st["time_budget_min"] and not st["before_flight"] and not tour \
                and (q.interests or "night" in q.wants) and not is_options_mode(q):
            # «хочу в кино», «куда вечером», «красивые места» без времени: час не вмещает ни кино (от 110 мин),
            # ни ужин — берём 2,5 часа. Но не дальше 30 мин до места: «холодно, в помещение» вело 59 мин пешком
            q.time_budget_min = FOCUS_BUDGET_MIN
            q.max_walk_min = q.max_walk_min or FOCUS_MAX_WALK_MIN
            lead_notes.append("Время не указано — подобрали примерно на 2,5 часа. Если времени меньше, "
                              "напишите, например, «у меня час».")
        res = self._answer(q, understood, parsed["more"] or parsed["next"], budget_assumed, lead_notes)
        st["shown_ids"] = [i["id"] for i in res["items"]]
        st["seen_ids"] = (st["seen_ids"] + st["shown_ids"])[-60:]
        st["last_max_leg"] = max((i["walk_min"] for i in res["items"]), default=None)
        if res["route"] and res["route"]["mode"] == "route" and res["items"]:
            last = res["items"][-1]
            st["route_end"] = {"lat": last["lat"], "lon": last["lon"], "label": last["name"], "poi_id": last["id"]}
        res["state"] = st
        return res

    def _tour_center(self, start: dict) -> tuple[dict, str] | None:
        """Обзорная прогулка там, где главных мест почти нет (у вокзалов, в спальных районах), —
        честно: «главное — в центре», и маршрут от Байтерека. None — рядом достаточно главных мест."""
        notable = sum(1 for p in self.catalog.pois if (p["wikidata"] or p["description"]) and p["name"]
                      and distance_m(start, p) <= TOUR_NEAR_M)
        center = self._landmark_point("baiterek")
        if notable >= TOUR_MIN_NOTABLE or not center or distance_m(start, center) <= TOUR_NEAR_M:
            return None
        km = f"{distance_m(start, center) / 1000:.1f}".replace(".", ",")
        center["poi_id"] = None  # сам Байтерек — первая остановка обзорной прогулки, а не только точка старта
        note = (f"Рядом с вами главных мест почти нет — они в центре, на левом берегу. Маршрут начинается у Байтерека: "
                f"от вас {km} км, доберитесь на такси или автобусе.{self._lrt_hint(start, center)}")
        return center, note

    def _lrt_hint(self, start: dict, goal: dict) -> str:
        """«На ЛРТ: от станции X (N мин пешком) до станции Y» — если обе станции близко к началу и концу пути."""
        a, b = self.places.nearest_lrt(start), self.places.nearest_lrt(goal)
        if not a or not b or a[0]["id"] == b[0]["id"]:
            return ""
        to_a, from_b = walk_min(a[1]), walk_min(b[1])
        if to_a > LRT_HINT_MAX_MIN or from_b > LRT_HINT_MAX_MIN:
            return ""
        return (f" Можно на ЛРТ: от станции «{display_name(a[0])}» (~{to_a} мин пешком) "
                f"до станции «{display_name(b[0])}» (~{from_b} мин пешком до места).")

    def recommend(self, req: dict) -> dict:
        """Один запрос без истории (отчёт по сценариям, тесты): text + landmark | lat/lon + at."""
        state = None
        if req.get("landmark"):
            if req["landmark"] not in self.landmarks:
                raise RequestError(f"неизвестный ориентир: {req['landmark']}")
            lm = self.landmarks[req["landmark"]]
            state = {"start": {"lat": lm["lat"], "lon": lm["lon"], "label": lm["label"], "poi_id": lm["poi_id"]}}
        point = {"lat": req["lat"], "lon": req["lon"], "source": "geo"} if req.get("lat") is not None else None
        return self.chat(req.get("text") or "", state, point, req.get("at"))

    def _message(self, reply: str, st: dict, understood: dict) -> dict:
        """Ответ без подбора: уточняющий вопрос, «пожалуйста», «не понял»."""
        return {"reply": reply, "start": st["start"], "destination": st["destination"], "items": [], "route": None,
                "notes": [], "state": st, "data_used": None, "understood": understood}

    # ---------- ответ с местами ----------

    def _answer(self, q: Query, understood: dict, asked_more: bool, budget_assumed: bool = False,
                lead_notes: list[str] = ()) -> dict:
        if q.has_destination and budget_assumed and q.direct_min > MAX_DEST_WALK_MIN:
            hours, mins = divmod(q.direct_min, 60)
            reply = (f"Пешком до «{q.dest_label}» около {hours} ч {mins} мин — это далеко. Доберитесь на такси или "
                     "автобусе, а прогулку по пути построю от места, где выйдете: напишите его или нажмите на карту.")
            reply += self._lrt_hint({"lat": q.lat, "lon": q.lon}, q.end)
            return self._response(q, reply, [], None, list(lead_notes), understood, "template")
        if q.has_destination and q.direct_min > q.usable_min:
            reply = (f"Пешком до «{q.dest_label}» напрямую около {q.direct_min} мин — больше, чем ваши "
                     f"{q.usable_min} мин, остановки по пути не поместятся. Быстрее — автобус или такси.")
            return self._response(q, reply, [], None, list(lead_notes), understood, "template")
        cands = self.catalog.candidates(q)
        focused = [c for c in cands if self.catalog.matches_focus(c, q)]
        notes = list(lead_notes)
        start = {"lat": q.lat, "lon": q.lon}
        has_focus = q.with_kids or bool(q.interests) or bool({"coffee", "night"} & set(q.wants))
        # без особой просьбы хватит примечания «ничего не успеть»; по пути к цели «ближайшее» не поможет
        if not focused and has_focus and not q.has_destination:
            match = self._nearest_match(q)
            if match:
                notes.append(f"Под ваш запрос в {q.usable_min} мин ничего не укладывается. Ближайшее подходящее: "
                             f"«{display_name(match)}» ({category_info(match['category'])[0]}) — "
                             f"{distance_m(start, match):.0f} м по прямой.")
        is_route = not is_options_mode(q)
        # «Кофе рядом» — конкретная нужда: арт-объект вместо кафе не поможет, лучше честное «рядом нет»
        pool = focused or (cands if is_route else [])
        via = next((c for c in cands if c["poi"]["id"] == q.via_poi_id), None) if q.via_poi_id else None
        if is_route and via is not None and via not in pool:
            pool = pool + [via]  # «через Байтерек» — даже если он не из той группы мест, что просили
        if not focused and pool:
            notes.append("Пока показываем другие места " + ("по пути." if q.has_destination else "поблизости."))
        chosen = (self.catalog.route if is_route else self.catalog.options)(q, pool)
        items = [self._item(c) for c in chosen]
        route = summary(q, chosen) if chosen else None

        final = ({"name": q.dest_label, "walk_min": route["return_min"]}
                 if route and route["mode"] == "route" and route.get("destination") else None)
        texts.fill(items, is_route, final)
        if q.has_destination and not items:
            reply = texts.no_stops(q.dest_label, q.direct_min, asked_more)
        else:
            reply = texts.intro(items, route, q.round_trip, asked_more)
        text_source = "template"
        if self.gemini and items:
            if not self._llm_allowed():
                notes.append("Лимит запросов к ИИ на эту минуту исчерпан — тексты собраны по шаблону из тех же данных.")
            else:
                try:
                    applied, model_intro = llm_mod.explain(self.gemini, items, understood["text"],
                                                           self._filters(q), is_route, route)
                    text_source = "gemini" if applied == len(items) else "mixed"
                    reply = model_intro or reply
                except llm_mod.LLMError as exc:
                    log.warning("Gemini, тексты: %s", exc)
                    notes.append("Gemini не ответил — тексты собраны по шаблону из тех же данных.")

        notes += self._notes(q, items, nothing_fits=not cands, budget_assumed=budget_assumed)
        if is_route and q.via_poi_id and items and all(i["id"] != q.via_poi_id for i in items):
            notes.append("Промежуточная точка в ваше время не поместилась — маршрут без неё. Напишите, "
                         "сколько у вас времени, например «у меня 2 часа».")
        if PRICE.search(understood["text"].lower()):
            notes.append("Цен в наших данных нет — бесплатно ли, проверьте на месте.")
        return self._response(q, reply, items, route, notes, understood, text_source)

    def _response(self, q: Query, reply: str, items: list[dict], route: dict | None, notes: list[str],
                  understood: dict, text_source: str) -> dict:
        dest = {"lat": q.dest_lat, "lon": q.dest_lon, "label": q.dest_label} if q.has_destination else None
        return {
            "reply": reply,
            "start": {"lat": q.lat, "lon": q.lon, "label": q.start_label},
            "destination": dest,
            "items": items,
            "route": route,
            "notes": notes,
            "understood": understood,
            "data_used": {
                "location": f"{q.start_label}: {q.lat:.3f}, {q.lon:.3f}",
                "destination": q.dest_label if q.has_destination else None,
                "time": f"{WEEKDAYS[q.at.weekday()]}, {q.at:%H:%M} (Астана, UTC+5)",
                "budget_min": q.time_budget_min,
                "filters": self._filters(q),
                "texts": text_source,
                "parser": understood["parser"],
                "sources": ["OpenStreetMap (ODbL)", "Wikidata (CC0)", "Википедия (CC BY-SA 4.0)",
                            "путеводитель Wikivoyage (CC BY-SA 4.0)", "фото Wikimedia Commons (лицензия у каждого фото)"],
                "stored": "ничего: разговор хранится только в вашем браузере, координаты округлены до ~100 м",
            },
        }

    def _item(self, c: dict) -> dict:
        poi = c["poi"]
        status = c["status"]
        warning = None
        if c["closes_soon"]:
            warning = f"когда дойдёте, до закрытия останется {status.minutes_left} мин"
        return {
            "id": poi["id"], "name": display_name(poi), "name_original": poi["name"],
            "category": poi["category"], "category_label": category_info(poi["category"])[0],
            "lat": poi["lat"], "lon": poi["lon"],
            "walk_min": c["leg_min"], "leg_m": round(c["leg_m"]), "from_start_m": round(c["dist_m"]),
            "visit_min": list(c["visit"]), "open": {"state": status.state, "text": status.text}, "warning": warning,
            "description": poi["description"], "description_source": poi["description_source"],
            "wikipedia": poi["wikipedia"], "website": poi["website"], "opening_hours": poi["opening_hours"],
            "image": poi.get("image"), "guide": poi.get("guide"),
            "chain": self.catalog.is_chain(poi), "sources": poi["sources"], "reasons": c["reasons"],
        }

    def _nearest_match(self, q: Query) -> dict | None:
        """Ближайшее место под запрос без ограничения по времени — чтобы сказать, где оно."""
        start, excluded = {"lat": q.lat, "lon": q.lon}, set(q.exclude_ids)
        matching = [p for p in self.catalog.pois if p["id"] not in excluded
                    and self.catalog.recommendable(p, q) and self.catalog.matches_focus({"poi": p}, q)]
        return min(matching, key=lambda p: distance_m(start, p), default=None)

    def _filters(self, q: Query) -> list[str]:
        out = [f"время: {q.time_budget_min} мин", "пешком"]
        if q.has_destination:
            out.append(f"до «{q.dest_label}»: напрямую {q.direct_min} мин пешком, места — по пути")
        if q.max_walk_min:
            out.append(f"до места не дальше {q.max_walk_min} мин")
        if q.with_kids:
            out.append("с ребёнком (шаг медленнее, без баров)")
        if q.before_flight:
            out.append(f"до вылета: {FLIGHT_RESERVE_MIN} мин в запасе, с возвратом к старту")
        out += [f"интерес: {g}" for g in q.interests]
        out += [WANT_LABELS[w] for w in q.wants]
        return out

    def _notes(self, q: Query, items: list[dict], nothing_fits: bool, budget_assumed: bool = False) -> list[str]:
        notes = []
        start = {"lat": q.lat, "lon": q.lon}
        if budget_assumed:
            notes.append(f"Время не указано: заложили {q.time_budget_min} мин — {q.direct_min} мин пешком напрямую "
                         f"и около часа на остановки. Если времени другое, напишите, например, «у меня 2 часа».")
        if q.before_flight:
            notes.append(f"Оставили {FLIGHT_RESERVE_MIN} мин на дорогу в терминал и регистрацию: на прогулку — "
                         f"{q.usable_min} мин с возвратом к старту. На международный рейс закладывайте больше.")
        around = sum(1 for p in self.catalog.pois if p["name"] and distance_m(start, p) <= 1500)
        if around < 5:
            notes.append(f"Рядом с этой точкой в нашей базе мало мест: {around} в радиусе 1,5 км.")
            center = self.landmarks.get("baiterek")
            if center and not q.has_destination:
                dist = distance_m(start, center)
                walk = walk_min(dist, q.with_kids)
                tail = " — пешком не успеть." if walk > q.usable_min else f", ~{walk} мин пешком."
                notes.append(f"До Байтерека {dist / 1000:.1f} км по прямой".replace(".", ",") + tail)
        if nothing_fits and not q.has_destination:  # не «под запрос нет», а вообще ничего не успеть
            near = nearest(self.catalog, q, 2)
            places = "; ".join(f"«{n['name']}» ({n['category_label']}) — {n['dist_m']} м, ~{n['walk_min']} мин"
                               for n in near)
            notes.append(f"За {q.usable_min} мин пешком отсюда ничего не успеть. Ближайшее из базы: {places}.")
        if "socket" in q.wants:
            notes.append("Розетки и Wi-Fi в данных OSM почти не отмечены — уточняйте на месте.")
        if "quiet" in q.wants:
            notes.append("Данных о шуме нет — тишину обещать не можем.")
        if q.is_night and any(i["open"]["state"] == "outdoor" for i in items):
            notes.append("Сейчас вечер или ночь: парки и уличные объекты могут быть закрыты или не освещены — "
                         "часов работы у них в данных нет.")
        if any(i["open"]["state"] == "unknown" for i in items):
            notes.append("У части мест часы работы неизвестны — проверьте перед визитом.")
        if items:
            notes.append("Время пешком — оценка: расстояние по прямой × 1,3.")
        return notes
