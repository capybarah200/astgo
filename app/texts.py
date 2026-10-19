"""Тексты «почему» и «что делать» по шаблонам — без LLM.

Используются, когда ключа Gemini нет, Gemini не ответил или его текст не прошёл проверку.
Шаблоны собирают фразу только из фактов, которые посчитал движок: ничего не выдумывают.
"""

from pipeline.coverage import group_of

REASONS = {
    "coffee": "Здесь можно выпить кофе",
    "kids": "Подходит для прогулки с ребёнком",
    "night": "Работает вечером",
    "unusual": "Необычное место — не сетевое и не рядовое",
    "tour": "Одно из главных мест города",
    "socket": "Можно посидеть подольше — розетку уточните на месте",
}
# Причина по группе места — когда особых причин нет (обычная прогулка) или интерес совпал
GROUP_REASONS = {
    "Достопримечательности": "Стоит увидеть",
    "Парки": "Зелёное место для прогулки",
    "Культура": "Культурная программа",
    "Кафе и еда": "Можно перекусить",
    "Бары и ночь": "Место для вечера",
    "Для детей": "Место для детей",
    "Спорт и активности": "Активный отдых",
    "ТРЦ и магазины": "Магазины и развлечения под крышей",
    "Коворкинги": "Можно поработать",
}
TODO = {
    "tourism=museum": "Пройдите основную экспозицию",
    "tourism=gallery": "Посмотрите текущую выставку",
    "tourism=attraction": "Осмотрите место и сделайте фото",
    "tourism=viewpoint": "Посмотрите на город сверху",
    "tourism=artwork": "Остановитесь посмотреть и сфотографировать",
    "tourism=aquarium": "Посмотрите аквариумы и шоу по расписанию на месте",
    "tourism=theme_park": "Выберите аттракционы по возрасту",
    "tourism=picnic_site": "Устройте короткий пикник",
    "leisure=park": "Пройдитесь по аллеям парка",
    "leisure=garden": "Прогуляйтесь по скверу",
    "leisure=playground": "Дайте ребёнку поиграть на площадке",
    "leisure=water_park": "Возьмите купальные вещи — аквапарк на полдня",
    "leisure=indoor_play": "Ребёнок поиграет в игровой зоне",
    "leisure=stadium": "Посмотрите на арену снаружи; матчи — по расписанию",
    "leisure=ice_rink": "Покатайтесь на коньках (прокат уточните на месте)",
    "amenity=cafe": "Возьмите кофе с собой или посидите",
    "amenity=restaurant": "Пообедайте или поужинайте",
    "amenity=fast_food": "Быстро перекусите",
    "amenity=ice_cream": "Возьмите мороженое",
    "amenity=bar": "Посидите за напитком",
    "amenity=pub": "Посидите за напитком",
    "amenity=nightclub": "Проверьте программу вечера на месте",
    "leisure=escape_game": "Пройдите квест — время игры обычно бронируют заранее",
    "leisure=bowling_alley": "Сыграйте партию в боулинг",
    "leisure=amusement_arcade": "Поиграйте на автоматах",
    "amenity=cinema": "Выберите сеанс — расписание на сайте кинотеатра",
    "amenity=theatre": "Проверьте афишу: спектакли идут по расписанию",
    "amenity=place_of_worship": "Посмотрите архитектуру; внутри соблюдайте правила места",
    "amenity=community_centre": "Посмотрите здание снаружи; вход внутрь — по правилам места",
    "amenity=marketplace": "Пройдитесь по рядам и попробуйте местные продукты",
    "amenity=library": "Загляните в читальный зал",
    "amenity=fountain": "Передохните у фонтана",
    "shop=mall": "Загляните в торговый центр",
    "shop=books": "Полистайте книги",
}


def why(item: dict) -> str:
    """Причина без повтора фактов: минуты и часы работы уже стоят строкой выше в карточке."""
    reasons = [REASONS[r] for r in item["reasons"] if r in REASONS]
    reasons += [GROUP_REASONS[r.split(":", 1)[1]] for r in item["reasons"] if r.startswith("interest:")]
    reason = reasons[0] if reasons else GROUP_REASONS.get(group_of(item["category"]), "Рядом с вами")
    return f"{reason}."


def todo(item: dict, next_item: dict | None) -> str:
    cat = item["category"]
    action = TODO.get(cat) or ("Осмотрите памятник" if cat.startswith("historic=") else "Загляните и осмотритесь")
    lo, hi = item["visit_min"]
    text = f"{action}: {lo}–{hi} мин."
    if next_item:
        text += f" Потом — «{next_item['name']}», {next_item['walk_min']} мин пешком."
    return text


NOT_UNDERSTOOD = ("Не понял, что подобрать. Напишите, что хотите: «погулять в парке», «кофе», «с ребёнком», "
                  "«ещё варианты» или «ближе».")


def ask_where(understood: list[str]) -> str:
    """Уточняющий вопрос, когда неизвестно, где человек."""
    head = f"Запомнил: {', '.join(understood)}. " if understood else ""
    return (head + "Где вы сейчас? Напишите место — например, «я у Байтерека» или «возле Астана Оперы» — "
            "или нажмите на карту.")


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def intro(items: list[dict], route: dict | None, round_trip: bool, asked_more: bool) -> str:
    """Первая фраза ответа по фактам маршрута (с ключом Gemini её пишет модель)."""
    if not items:
        return "Других вариантов поблизости нет." if asked_more else "Под такой запрос рядом ничего не нашлось."
    n = len(items)
    if route and route.get("mode") == "options":
        if n == 1:  # «укажи одно заведение»
            return (f"Лучший вариант по пути до «{route['destination']}»:" if route.get("destination")
                    else "Лучший вариант рядом:")
        if route.get("destination"):  # «кофе по пути к Байтереку»: зайти в одно и идти дальше к цели
            return (f"{n} {_plural(n, 'вариант', 'варианта', 'вариантов')} по пути до «{route['destination']}» — "
                    "зайдите в один и идите дальше:")
        return f"{n} {_plural(n, 'вариант', 'варианта', 'вариантов')} рядом — выберите один:"
    lo, hi = route["total_min"]
    total = f"{lo}–{hi}" if hi > lo else f"{lo}"
    walk = route["walk_min"] + route["return_min"]
    if route.get("destination"):
        dest = route["destination"]
        if n == 1:
            return f"По пути до «{dest}» — одно место: {total} мин вместе с дорогой, из них пешком {walk}:"
        return f"Маршрут до «{dest}» через {n} {_plural(n, 'место', 'места', 'мест')}: {total} мин, из них пешком {walk}:"
    back = ", с возвратом к старту" if round_trip else ""
    if n == 1:
        return f"Одно место — {total} мин вместе с дорогой{back}:"
    return f"Маршрут из {n} мест на {total} мин, из них пешком {walk}{back}:"


def no_stops(dest: str, direct_min: int, asked_more: bool) -> str:
    """Цель пути есть, а мест по пути под запрос нет — честно: идите напрямую."""
    head = "Других мест по пути нет" if asked_more else "По пути подходящих мест в нашей базе нет"
    return f"{head} — до «{dest}» напрямую около {direct_min} мин пешком."


def fill(items: list[dict], is_route: bool = True, final: dict | None = None) -> None:
    """is_route=False — варианты на выбор: без «Потом — …». final — цель пути после последнего места."""
    for i, item in enumerate(items):
        item["why"] = why(item)
        nxt = (items[i + 1] if i + 1 < len(items) else final) if is_route else None
        item["todo"] = todo(item, nxt)
        item["text_source"] = "template"
