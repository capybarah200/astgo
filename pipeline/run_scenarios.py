"""Прогон тестовых сценариев через ассистента → reports/scenarios.md.

Запуск:  python pipeline/run_scenarios.py   (сеть не нужна; Gemini не используется — тексты по шаблону,
         чтобы отчёт был воспроизводимым)
Время зафиксировано: суббота, 17 октября 2026 — часы работы зависят от дня недели.
"""

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # чтобы `python pipeline/...py` видел пакеты app и pipeline
from app.engine import ASTANA_TZ, Catalog, load_landmarks  # noqa: E402
from app.places import load_areas  # noqa: E402
from app.service import Assistant  # noqa: E402

OUT_PATH = ROOT / "reports" / "scenarios.md"
FIXED_DAY = datetime(2026, 10, 17, 15, 0, tzinfo=ASTANA_TZ)

# (название, запрос, чего ждём) — первые пять из примеров задания
SCENARIOS = [
    ("1. Прогулка у Mega Silk Way",
     {"text": "Я сейчас возле Mega Silk Way, хочу прогуляться 30–60 минут — что посоветуете?", "at": "15:00"},
     "1–3 места в пешей доступности, маршрут укладывается в 60 мин"),
    ("2. Тихое место с кофе и розеткой",
     {"landmark": "baiterek", "text": "Хочу тихое место с кофе и розеткой недалеко от меня.", "at": "15:00"},
     "кафе на выбор; честно: данных о розетках и шуме нет"),
    ("3. С ребёнком: площадки или музей",
     {"landmark": "khan_shatyr", "text": "Еду с ребёнком — где рядом есть детские площадки или музей с интерактивами?",
      "at": "15:00"},
     "детские места и парки, без баров; шаг медленнее"),
    ("4. Турист, 2 часа до вылета",
     {"landmark": "airport", "text": "Я — турист, у меня 2 часа перед вылетом: что можно посмотреть поблизости?",
      "at": "15:00"},
     "час в запасе на регистрацию, возврат к старту; честно: рядом с аэропортом мест мало"),
    ("5. Необычная ночная активность",
     {"landmark": "khan_shatyr", "text": "Ищу необычную ночную активность (последние пару часов).", "at": "23:00"},
     "места, открытые в 23:00, с часами работы из данных"),
    ("6. Пример из задания: Экспо, необычное, 1–2 часа",
     {"text": "Я у здания Экспо 2017, хочу что-то необычное на 1–2 часа, пешком.", "at": "15:00"},
     "заметные места (Wikidata/Википедия), мини-маршрут до 120 мин"),
    ("7. Кофе у вокзала Нурлы Жол",
     {"text": "Я на вокзале Нурлы Жол, хочу кофе, есть 45 минут", "at": "15:00"},
     "кафе в пешей доступности нет — сказать, где ближайшее, а не выдумывать"),
    ("8. С ребёнком у Байтерека",
     {"text": "Я у Байтерека с ребёнком, часа полтора", "at": "15:00"},
     "детская площадка (даже без названия — так и подписать), парк, мороженое"),
    ("9. Вечер у Хан Шатыра в 21:30",
     {"text": "Я у Хан Шатыра, что открыто на вечер и ночь, пара часов", "at": "21:30"},
     "бары, кино, рестораны, открытые на момент прихода; предупреждение, если скоро закроется"),
    ("10. Любая точка города: возле Национального музея",
     {"text": "Стою возле Национального музея, есть час", "at": "15:00"},
     "место старта — любое из 1872 мест базы по названию, не только ориентиры"),
    ("13. Район как цель: из аэропорта до Жагалау",
     {"text": "Я в аэропорту, доведи до Жагалау", "at": "15:00"},
     "цель — микрорайон Жагалау (районы, ЖК и улицы из OSM), а не ресторан «Жагалау»; "
     "честно: пешком больше 4 часов — доехать, прогулку построить от места, где выйдете"),
]

# Диалоги: контекст (место, время, пожелания, цель пути) переходит из реплики в реплику
DIALOGS = [
    ("11. Диалог в несколько реплик",
     ["Хочу погулять часа полтора", "Я у Астана Оперы", "Ещё варианты", "А с ребёнком?"],
     "уточняющий вопрос «где вы?», затем маршрут; «ещё» — новые места; «с ребёнком» — без баров"),
    ("12. Из точки А в точку Б: от Экспо до Байтерека",
     ["Я у Экспо", "Доведи до Байтерека по красивым местам"],
     "старт остаётся у Экспо, Байтерек — цель: места по пути, по ходу движения, маршрут заканчивается у Байтерека"),
    ("14. Обзорная прогулка со старого вокзала",
     ["Я на старом вокзале, хочу пройти всю Астану по всем достопримечательностям", "Дальше"],
     "старый вокзал — Астана-1, а не Нурлы Жол; рядом главного нет — честно «доезжайте в центр», маршрут на 3 часа "
     "от Байтерека по главным местам; «дальше» — следующие места от последнего, без повторов"),
]


GENERIC_NOTES = ("Время пешком — оценка", "У части мест часы работы неизвестны")


def render_dialog(title: str, messages: list[str], expected: str, assistant: Assistant) -> list[str]:
    lines = [f"## {title}", "", f"**Ожидаем:** {expected}", ""]
    state = None
    for message in messages:
        res = assistant.chat(message, state, at="15:00")
        state = res["state"]
        lines += [f"> **Человек:** {message}", ">", f"> **Ассистент:** {res['reply']}"]
        for i, item in enumerate(res["items"], 1):
            lines.append(f"> {i}. {item['name']} — {item['category_label']}, {item['walk_min']} мин пешком; "
                         f"{item['todo']}")
        # примечания, кроме тех, что есть почти в каждом ответе («время пешком — оценка», «часы неизвестны»)
        lines += [f"> _Примечание:_ {n}" for n in res["notes"] if not n.startswith(GENERIC_NOTES)]
        lines.append("")
    return lines


def render(title: str, req: dict, expected: str, res: dict) -> list[str]:
    du = res["data_used"]
    lines = [f"## {title}", "", f"**Запрос:** {req.get('text') or '—'}  ",
             f"**Ориентир:** {req.get('landmark') or 'из текста'}  ",
             f"**Время:** {du['time']}  ", f"**Ожидаем:** {expected}", "",
             f"Поняли так: {', '.join(res['understood']['parsed']) or '—'}. Учли: {', '.join(du['filters'])}.", "",
             f"Ответ: {res['reply']}", ""]
    route = res["route"]
    if route and route["mode"] == "route":
        lines.append(f"Мини-маршрут: пешком {route['walk_min'] + route['return_min']} мин, всего "
                     f"{route['total_min'][0]}–{route['total_min'][1]} мин, закончите ~{route['finish_at'][0]}; "
                     f"укладывается в {route['usable_min']} мин: {'да' if route['fits_budget'] else 'нет'}.")
    elif route:
        lines.append("Варианты на выбор (идти в одно место).")
    lines.append("")
    for i, item in enumerate(res["items"], 1):
        lines.append(f"{i}. **{item['name']}** — {item['category_label']}, {item['walk_min']} мин пешком "
                     f"({item['leg_m']} м), {item['visit_min'][0]}–{item['visit_min'][1]} мин на месте, "
                     f"{item['open']['text']}" + (f", ⚠ {item['warning']}" if item["warning"] else ""))
        lines.append(f"   - Почему: {item['why']}")
        lines.append(f"   - Что делать: {item['todo']}")
        if item["description"]:
            lines.append(f"   - Описание ({item['description_source']}): {item['description']}")
    if not res["items"]:
        lines.append("_Мест не предложено._")
    if res["notes"]:
        lines += ["", "Примечания:"] + [f"- {n}" for n in res["notes"]]
    return lines + [""]


def main() -> None:
    assistant = Assistant(Catalog.load(), load_landmarks(), gemini=None, clock=lambda: FIXED_DAY, areas=load_areas())
    lines = ["# Тестовые сценарии: входные данные и ответы", "",
             "Сгенерировано `python pipeline/run_scenarios.py`. Тексты «почему / что делать» — по шаблону "
             "(без Gemini), чтобы отчёт был воспроизводимым; с ключом Gemini формулировки живее, факты те же.",
             "Дата зафиксирована: суббота, 17 октября 2026.", ""]
    for title, req, expected in SCENARIOS:
        lines += render(title, req, expected, assistant.recommend(req))
    for dialog in DIALOGS:
        lines += render_dialog(*dialog, assistant)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
