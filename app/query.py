"""Разбор запроса своими словами → параметры подбора.

Правила на ключевых словах работают всегда. Если есть Gemini, сначала пробуем его
(app/llm.py), а правила остаются запасным вариантом. Ответ содержит, что именно
поняли, — интерфейс показывает это пользователю («Поняли так: …»).
"""

import re

GROUPS = ["Достопримечательности", "Парки", "Культура", "Кафе и еда", "Бары и ночь", "Для детей",
          "Спорт и активности", "ТРЦ и магазины", "Коворкинги"]
WANTS = ["coffee", "night", "unusual", "quiet", "socket", "tour"]
WANT_LABELS = {"coffee": "кофе", "night": "вечер / ночь", "unusual": "необычное", "quiet": "тихое место",
               "socket": "розетка / поработать", "tour": "обзорная прогулка по главным местам"}

# Начало слова + падежи: «у Байтерека», «в Меге», «на вокзале»; «мегаполис» — не Mega
LANDMARK_PATTERNS = {
    "mega": r"mega\s*silk|megasilkway|\bmega\b|мега\s*силк|\bмег[аеиуо]\b",
    "expo": r"экспо|expo|нур[\s-]?алем|нұр[\s-]?әлем|nur[\s-]?alem",
    "baiterek": r"байтерек|бәйтерек|baiterek|bayterek",
    "khan_shatyr": r"хан[\s-]?шатыр|khan[\s-]?shatyr",
    "airport": r"аэропорт|airport|әуежай",
    "station": r"нурлы[\s-]?жол|нұрлы[\s-]?жол|nurly[\s-]?zhol|вокзал",
    "old_station": r"астана[\s-]?1\b|\bстар\w*\s+вокзал",
}
# Цель пути: «до Байтерека», «к Хан Шатыру», «в сторону Экспо», «хочу в Хан Шатыр»
_TARGET = (r"\bдо|\bк|\bко|в\s+сторону|"
           r"(?<!\bне\s)(?:хочу|хотим|пойти|сходить|зайти|поехать|попасть|съездить|сводить|идти|иду|домой)\s+(?:в|на)")
# «Хочу в центр», «до центра» — цель пути: центр города у Байтерека (не «торговый центр», не «я в центре»)
CENTER = re.compile(r"(?:\bв|\bдо|\bк|\bко)\s+центр[ау]?\b(?!\s+(?:торгов|бизнес|делов|развлеч))")
TARGET = re.compile(rf"(?:{_TARGET})\s+(?:\w+\s+)?$")
# «Через Байтерек» — промежуточная точка маршрута: не старт и не цель
VIA = re.compile(r"\bчерез\s+(?:\w+\s+)?$")
# Всё, что после «через», — чтобы честно сказать «не нашёл», если такого места нет («через час» — время)
VIA_TAIL = re.compile(r"\bчерез\s+([^,.;!?]+)", re.IGNORECASE)
# Не точка старта: цель пути, промежуточная точка и «мимо Байтерека» (по пути, но не цель)
DESTINATION = re.compile(rf"(?:{_TARGET}|\bмимо|\bчерез)\s+(?:\w+\s+)?$")
# Как говорят в городе: «с АДП до Ханша» — с Абу-Даби Плаза до Хан Шатыра
ALIASES = [
    (re.compile(r"\bадп\b", re.IGNORECASE), "Абу-Даби Плаза"),
    (re.compile(r"\bханш(?:а|у|е|ой|ем)?\b", re.IGNORECASE), "Хан Шатыр"),
    # «через арку» — Триумфальная арка «Мангилик Ел», другой арки в базе нет; «Сарыарка», «У Аркадия» — не она
    (re.compile(r"(?<![\w-])(?:триумфальн\w*\s+)?арк(?:а|у|е|ой|и)\b", re.IGNORECASE), "Триумфальная арка"),
]


def expand_aliases(text: str) -> str:
    for pattern, full in ALIASES:
        text = pattern.sub(full, text)
    return text


# Старый вокзал (Астана-1) — свой ориентир, а не Нурлы Жол: «на старом вокзале», «со старого вокзала»
OTHER_STATION = re.compile(r"астана[\s-]?1\b|\bстар\w*\s+вокзал")
INTEREST_WORDS = {
    "Культура": r"музе|галере|выставк|театр|культур|концерт|кино|помещени|под\s+крыш",
    "Парки": r"парк|сквер|сад\b|природ|погулять на воздух|зелен|красив|живописн|собак",
    "Кафе и еда": r"поесть|еда|ресторан|кафе|обед|ужин|завтрак|перекус|голод",
    "Достопримечательности": (r"достопримечат|памятник|монумент|смотров|вид на город|фото|архитектур|красив|живописн"
                              r"|сверху|панорам|с\s+высоты"),
    # «холодно, хочу в помещение» — под крышу: ТРЦ и культура, а не парк
    "ТРЦ и магазины": r"шопинг|магазин|трц|торгов|книжн|помещени|под\s+крыш|холодно|мороз|погреться",
    "Спорт и активности": r"спорт|каток|боулинг|квест",  # не «актив»: «интерактив», «ночная активность»
    "Бары и ночь": r"\bбар|паб|клуб|выпить",
}
WANT_WORDS = {
    "coffee": r"кофе|кофейн|капучино|латте",
    "night": r"ноч|поздн|клуб|тусов",  # не «вечер»: «вечером погулять в парке» — не про бары
    "unusual": r"необычн|уникальн|интересн|особенн|удиви",
    "quiet": r"тих|спокойн|без шума",
    "socket": r"розетк|заряд|ноутбук|поработ",
    # «пройти всю Астану», «все достопримечательности», «обзорная», «экскурсия по городу»
    "tour": r"вс[юеё]\s+астан|весь\s+город|всему\s+городу|вс[еи]\w*\s+достопримечат|главн\w*\s+(?:мест|достопримечат)"
            r"|вс[её]\s+главн|самое\s+главн|обзорн|экскурси",
}
# Уточнения в диалоге. «Ещё час» — это время, а не «ещё варианты»
MORE = (r"\bещ[её]\b(?!\s+(?:\d|час|полчас|минут|пару|пара|один|одну|два|две|три))"
        r"|\bдруги[ехм]\b|\bдругое\b|\bиначе\b|\bне то\b|не нравится")
# Общая просьба без деталей («что посоветуете?», «погулять») — тоже запрос: подбираем по тому, что уже известно
GENERAL = (r"погуля|прогул|посовет|подскаж|куда|сходить|пойти|покажи|что\s+(?:есть|рядом|посмотреть|интересн)|идеи"
           r"|маршрут|доведи|проведи")
CLOSER = r"\bближе\b|\bпоближе\b"
# «Дальше» — продолжить маршрут от последнего места (следующие 1–3 места), а не «ещё варианты» с того же старта
CONTINUE = r"\bдальше\b|\bпродолж|\bследующ\w*\s+(?:мест|точк|част)|\bа\s+потом\b"
RESET = r"\bзаново\b|\bсначала\b|\bсброс"
# «Укажи одно заведение», «выбери одно», «какое лучше» — одно лучшее место из показанных.
# Не «я одна» и не «один час»: нужно слово про место или вариант
ONE = (r"\b(?:одн[оуа]|один)\s+(?:мест|заведен|вариант|кафе|кофейн|ресторан|бар|точк)\w*|\bтолько\s+одн|\bвыбери\b"
       r"|\b(?:какое|какой|какую|что)\s+(?:из\s+них\s+)?лучше\b|\bлучший\s+вариант")
# «Без Ритц Карлтона», «убери второе», «не хочу в Talan Towers» — убрать одно из показанных мест.
# Что именно убрать, решает app/service.py: сравнивает слова после «без» с показанными местами
WITHOUT = re.compile(r"(?:\bбез|\bкроме|\bубер\w*|\bне\s+(?:хочу|надо|нужно|нужен|нужна|нравится|пойду|пойд[её]м)"
                     r"(?:\s+(?:в|во|на))?)\s+([^,.;!?]+)", re.IGNORECASE)
ORDINALS = {"перв": 0, "втор": 1, "трет": 2}
THANKS = r"\bспасибо\b|\bблагодар|\bспс\b"
AT_CLOCK = re.compile(r"\bв\s+(\d{1,2})[:.](\d{2})\b")
AT_WORDS = re.compile(r"\bв\s+(\d{1,2})\s*(?:час[а-я]*\s+)?(утра|дня|вечера|ночи)\b")
KIDS = r"ребён|ребен|дет[иья]|детск|сын|доч|малыш|семь[её]й"
FLIGHT = r"вылет|рейс|самол[её]т|перед полет|до полет"
NUMBERS = {"один": 1, "одну": 1, "два": 2, "две": 2, "пару": 2, "пара": 2, "три": 3, "четыре": 4, "пять": 5}
NUM = r"(\d+(?:[.,]\d+)?|" + "|".join(NUMBERS) + r")"


def _num(token: str) -> float:
    return NUMBERS.get(token) or float(token.replace(",", "."))


def parse_minutes(text: str) -> int | None:
    """«30–60 минут» → 60, «1-2 часа» → 120, «полтора часа» → 90, «пару часов» → 120. Берём верхнюю границу."""
    t = text.lower()
    if re.search(r"полтора\s+час|час[а-я]*\s+полтора", t):
        return 90
    if "полчаса" in t:
        return 30
    m = re.search(r"\b" + NUM + r"\s*(?:[-–]|до)\s*" + NUM + r"\s*(мин|час|ч\b)", t)
    if m:
        value = _num(m.group(2))
        return round(value * (1 if m.group(3) == "мин" else 60))
    # \b — число целиком, а не «0» из «10»; «в 10 часов» — время суток, не длительность
    m = re.search(r"(?<!в )\b" + NUM + r"\s*(мин|час|ч\b)", t)
    if m:
        value = _num(m.group(1))
        return round(value * (1 if m.group(2) == "мин" else 60))
    # разговорный порядок: «минут 40», «часа 2»; не «часть 2», не «минимум 2»
    m = re.search(r"\b(минут[а-я]*|мин|часа|часов|часик[а-я]*)\s+" + NUM + r"\b", t)
    if m:
        value = _num(m.group(2))
        return round(value * (1 if m.group(1).startswith("мин") else 60))
    if re.search(r"\bчас\b|\bчасик", t):
        return 60
    return None


def find_landmark(text: str, destination: bool = False, via: bool = False) -> str | None:
    """Где человек сейчас. «Хочу дойти до Байтерека» — это цель, а не место старта.

    destination=True — наоборот, ориентир, к которому человек идёт («доведи до Байтерека»);
    via=True — через который идёт («через Байтерек»).
    """
    t = text.lower()
    for key, pattern in LANDMARK_PATTERNS.items():
        for m in re.finditer(pattern, t):
            before = t[:m.start()]
            if via:
                if not VIA.search(before):
                    continue
            elif (not TARGET.search(before)) if destination else DESTINATION.search(before):
                continue
            if m.group(0) == "вокзал" and OTHER_STATION.search(t):
                continue  # «вокзал Астана-1» — другой вокзал, его нет в ориентирах
            return key
    if destination and CENTER.search(t):
        return "baiterek"  # «хочу в центр погулять» — центр города, Байтерек
    return None


def is_general_request(text: str) -> bool:
    return bool(re.search(GENERAL, text.lower()))


def normalize_at(value: str | None) -> str | None:
    """«9:00» → «09:00»; None — не время суток."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", value or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def parse_at(text: str) -> str | None:
    """«в 23:00» → «23:00», «в 9 вечера» → «21:00»; время суток, а не длительность."""
    t = text.lower()
    m = AT_CLOCK.search(t)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
    else:
        m = AT_WORDS.search(t)
        if not m:
            return None
        hh, mm = int(m.group(1)), 0
        if m.group(2) in ("дня", "вечера") and hh < 12:
            hh += 12
        if m.group(2) == "ночи" and hh == 12:
            hh = 0
    return f"{hh:02d}:{mm:02d}" if hh < 24 and mm < 60 else None


def parse_rules(text: str) -> dict:
    """Запрос → параметры подбора и уточнения диалога; None или [] — не сказано."""
    t = text.lower()
    return {
        "landmark": find_landmark(t),
        "place": None,  # название места ищет app/places.py по всему тексту
        "destination": find_landmark(t, destination=True),  # ориентир-цель; цель по названию — app/places.py
        "time_budget_min": parse_minutes(t),
        "at": parse_at(t),
        "with_kids": True if re.search(KIDS, t) else None,
        "before_flight": True if re.search(FLIGHT, t) else None,
        "interests": [g for g, pattern in INTEREST_WORDS.items() if re.search(pattern, t)],
        "wants": [w for w, pattern in WANT_WORDS.items() if re.search(pattern, t)],
        "more": bool(re.search(MORE, t)),
        "closer": bool(re.search(CLOSER, t)),
        "next": bool(re.search(CONTINUE, t)),
        "reset": bool(re.search(RESET, t)),
        "thanks": bool(re.search(THANKS, t)),
        "one": bool(re.search(ONE, t)),
    }


def ordinal_indexes(text: str) -> list[int]:
    """«второе» → [1], «первого и третьего» → [0, 2], «2» → [1]; «2 часа» — время, а не номер места."""
    t = text.lower().strip()
    m = re.fullmatch(r"(?:№\s*)?([1-3])", t)
    if m:
        return [int(m.group(1)) - 1]
    return [i for w in re.findall(r"\w+", t) for st, i in ORDINALS.items()
            if w.startswith(st) and len(w) <= len(st) + 4]


def clean(parsed: dict) -> dict:
    """Приводит ответ (в том числе от LLM) к допустимым значениям; лишнее отбрасывает."""
    budget = parsed.get("time_budget_min")
    if not isinstance(budget, (int, float)) or isinstance(budget, bool) or not 10 <= budget <= 600:
        budget = None
    landmark = parsed.get("landmark")
    place, destination = parsed.get("place"), parsed.get("destination")
    at = parsed.get("at")
    if not (isinstance(at, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at)):
        at = None
    return {
        "landmark": landmark if landmark in LANDMARK_PATTERNS else None,
        "place": place.strip()[:100] if isinstance(place, str) and place.strip() else None,
        "destination": destination.strip()[:100] if isinstance(destination, str) and destination.strip() else None,
        "time_budget_min": int(budget) if budget else None,
        "at": at,
        "with_kids": True if parsed.get("with_kids") is True else None,
        "before_flight": True if parsed.get("before_flight") is True else None,
        "interests": [g for g in parsed.get("interests") or [] if g in GROUPS],
        "wants": [w for w in parsed.get("wants") or [] if w in WANTS],
        "more": parsed.get("more") is True,
        "closer": parsed.get("closer") is True,
        "next": parsed.get("next") is True,
        "reset": parsed.get("reset") is True,
        "thanks": parsed.get("thanks") is True,
        "one": parsed.get("one") is True,
    }


def describe(parsed: dict, landmark_labels: dict[str, str] | None = None) -> list[str]:
    """Что поняли из текста — для блока «Поняли так»."""
    out = []
    labels = landmark_labels or {}
    if parsed["landmark"]:
        out.append(f"место: {labels.get(parsed['landmark'], parsed['landmark'])}")
    if parsed.get("destination"):
        out.append(f"куда: {labels.get(parsed['destination'], parsed['destination'])}")
    if parsed["time_budget_min"]:
        out.append(f"время: {parsed['time_budget_min']} мин")
    if parsed.get("at"):
        out.append(f"на {parsed['at']}")
    if parsed["with_kids"]:
        out.append("с ребёнком")
    if parsed["before_flight"]:
        out.append("до вылета")
    out += [f"интерес: {g}" for g in parsed["interests"]]
    out += [WANT_LABELS[w] for w in parsed["wants"]]
    if parsed.get("one"):
        out.append("одно место")
    return out
