"""Часы работы из OSM (тег opening_hours): открыто ли место в заданный момент.

Поддерживается то, что реально встречается в данных Астаны: «24/7», правила через «;»
(и через «,» после времени: «Mo-Fr 09:00-18:00, Sa-Su 10:00-20:00»), дни и диапазоны дней,
в том числе через конец недели («Su-Th», «We-Mo»), несколько интервалов, переход через
полночь («11:00-03:00»), «off»/«closed». Праздники (PH) пропускаем — мы их не знаем.
Всё остальное («10:00+», месяцы, даты) — «неизвестно»: задание требует не утверждать
уверенно «открыто», если данных нет.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
RANGE = re.compile(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})")
DAY_TOKEN = re.compile(r"[A-Z]{1,2}[a-z]?(?:-[A-Z][a-z])?(?:,[A-Z]{1,2}[a-z]?(?:-[A-Z][a-z])?)*")

Week = dict[int, list[tuple[int, int]]]  # день недели 0..6 → интервалы в минутах от полуночи этого дня


@dataclass(frozen=True)
class Status:
    state: str                       # "open" | "closed" | "unknown"
    text: str                        # для человека: «открыто до 22:00», «закрыто, откроется в 10:00»
    minutes_left: int | None = None  # до закрытия, если открыто (None — круглосуточно)


def _days(selector: str) -> set[int] | None:
    """«Mo-Fr,Su» → {0,1,2,3,4,6}. PH/SH (праздники, каникулы) пропускаются. None — не разобрали."""
    days = set()
    for part in selector.split(","):
        if part in ("PH", "SH"):
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            if a not in DAYS or b not in DAYS:
                return None
            i, j = DAYS.index(a), DAYS.index(b)
            days |= set(range(i, j + 1)) if i <= j else set(range(i, 7)) | set(range(0, j + 1))
        elif part in DAYS:
            days.add(DAYS.index(part))
        else:
            return None
    return days


def _ranges(spec: str) -> list[tuple[int, int]] | None:
    """«09:00-13:00,14:00-02:00» → [(540, 780), (840, 1560)]: конец после полуночи — больше 1440."""
    out = []
    for part in spec.split(","):
        m = RANGE.fullmatch(part)
        if not m:
            return None
        h1, m1, h2, m2 = map(int, m.groups())
        start, end = h1 * 60 + m1, h2 * 60 + m2
        if start > 1440 or end > 1440 or m1 > 59 or m2 > 59:
            return None
        if end <= start:
            end += 1440
        out.append((start, end))
    return out


@lru_cache(maxsize=None)  # одна и та же строка разбирается один раз; результат не изменяется вызывающими
def parse(opening_hours: str | None) -> Week | None:
    """Строка OSM → расписание на неделю. None — формат не поддерживается.

    Как в OSM: дни, не упомянутые ни в одном правиле, — выходные; более позднее правило
    перекрывает более раннее для своих дней.
    """
    if not opening_hours:
        return None
    text = opening_hours.strip().replace("–", "-")
    if text == "24/7":
        return {d: [(0, 1440)] for d in range(7)}
    if text.lower() == "closed":
        return {d: [] for d in range(7)}
    if "||" in text or "+" in text:
        return None
    text = re.sub(r"(?<=\d)\s*,\s*(?=[A-Z])", ";", text)  # «18:00, Sa-Su …» — это новое правило
    text = re.sub(r"\s*,\s*", ",", text)
    text = re.sub(r"\s*-\s*", "-", text)
    week: Week = {}
    for rule in filter(None, (r.strip() for r in text.split(";"))):
        parts = rule.split()
        if DAY_TOKEN.fullmatch(parts[0]):
            days, rest = _days(parts[0]), parts[1:]
            if days is None:
                return None
            if not days:  # правило только для праздников
                continue
        else:
            days, rest = set(range(7)), parts
        if [r.lower() for r in rest] in (["off"], ["closed"]):
            ranges = []
        elif len(rest) == 1:
            ranges = _ranges(rest[0])
            if ranges is None:
                return None
        else:
            return None
        for d in days:
            week[d] = ranges
    return week if week else None


def _hhmm(minutes: int) -> str:
    minutes %= 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def status_at(opening_hours: str | None, at: datetime) -> Status:
    week = parse(opening_hours)
    if week is None:
        text = "часы работы неизвестны" if not opening_hours else f"часы работы: «{opening_hours}» — проверьте"
        return Status("unknown", text)
    day, minute = at.weekday(), at.hour * 60 + at.minute
    # сегодняшние интервалы и вчерашние, перешедшие через полночь
    intervals = list(week.get(day, [])) + [(s - 1440, e - 1440) for s, e in week.get((day - 1) % 7, []) if e > 1440]
    for start, end in intervals:
        if start <= minute < end:
            if end - start >= 1440:
                return Status("open", "открыто круглосуточно")
            return Status("open", f"открыто до {_hhmm(end)}", end - minute)
    later = sorted(s for s, _ in week.get(day, []) if s > minute)
    if later:
        return Status("closed", f"закрыто, откроется в {_hhmm(later[0])}")
    return Status("closed", "сегодня уже закрыто" if week.get(day) else "сегодня выходной")
