"""Отчёт о покрытии полей в собранных POI: какие данные реально есть.

Запуск:  python pipeline/coverage.py [data/poi_osm.json]
Результат: таблица в консоли + reports/osm_coverage.md
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

GROUPS = {
    "Кафе и еда": {"amenity=cafe", "amenity=restaurant", "amenity=fast_food", "amenity=ice_cream",
                   "amenity=food_court"},
    "Бары и ночь": {"amenity=bar", "amenity=pub", "amenity=nightclub"},
    "Культура": {"tourism=museum", "tourism=gallery", "amenity=theatre", "amenity=cinema",
                 "amenity=arts_centre", "amenity=library", "amenity=planetarium",
                 "amenity=exhibition_centre", "amenity=music_venue", "amenity=events_venue",
                 "amenity=community_centre"},
    "Достопримечательности": {"tourism=attraction", "tourism=viewpoint", "tourism=artwork",
                              "amenity=fountain", "amenity=place_of_worship"},
    "Парки": {"leisure=park", "leisure=garden", "leisure=nature_reserve", "tourism=picnic_site"},
    "Для детей": {"leisure=playground", "tourism=zoo", "tourism=aquarium", "tourism=theme_park",
                  "leisure=water_park", "leisure=amusement_arcade", "leisure=trampoline_park",
                  "leisure=indoor_play", "leisure=miniature_golf"},
    "Спорт и активности": {"leisure=sports_centre", "leisure=stadium", "leisure=ice_rink",
                           "leisure=escape_game", "leisure=bowling_alley", "leisure=dance",
                           "leisure=fitness_centre", "leisure=swimming_pool"},
    "ТРЦ и магазины": {"shop=mall", "shop=department_store", "shop=books", "amenity=marketplace"},
    "Коворкинги": {"amenity=coworking_space"},
}

# поле отчёта → функция проверки по тегам OSM
FIELDS = {
    "название": lambda t: "name" in t,
    "name:kk": lambda t: "name:kk" in t,
    "name:en": lambda t: "name:en" in t,
    "часы работы": lambda t: "opening_hours" in t,
    "сайт": lambda t: "website" in t or "contact:website" in t,
    "телефон": lambda t: "phone" in t or "contact:phone" in t,
    "описание": lambda t: any(k.startswith("description") for k in t),
    "wikidata": lambda t: "wikidata" in t,
    "wheelchair": lambda t: "wheelchair" in t,
    "wi-fi": lambda t: "internet_access" in t,
    "розетки": lambda t: any(k.startswith("socket") for k in t),
    "веранда": lambda t: "outdoor_seating" in t,
    "кухня": lambda t: "cuisine" in t,
}


def group_of(category: str) -> str:
    if category.startswith("historic="):
        return "Достопримечательности"
    for group, cats in GROUPS.items():
        if category in cats:
            return group
    return "Прочее"


def pct(n: int, total: int) -> str:
    return f"{100 * n / total:.0f}%" if total else "—"


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "poi_osm.json"
    pois = json.loads(path.read_text(encoding="utf-8"))["pois"]

    by_group = defaultdict(list)
    for p in pois:
        by_group[group_of(p["category"])].append(p["tags"])

    named = [p["tags"] for p in pois if p["name"]]
    lines = [f"# Покрытие полей OSM — Астана", "",
             f"Всего точек: **{len(pois)}**, с названием: **{len(named)}** ({pct(len(named), len(pois))}).", "",
             "## По группам (все точки / с названием)", "",
             "| Группа | Всего | С названием | " + " | ".join(list(FIELDS)[1:]) + " |",
             "|---" * (len(FIELDS) + 2) + "|"]
    order = sorted(by_group, key=lambda g: -len(by_group[g]))
    for group in order:
        tags_list = by_group[group]
        named_list = [t for t in tags_list if "name" in t]
        cells = [pct(sum(check(t) for t in named_list), len(named_list))
                 for check in list(FIELDS.values())[1:]]
        lines.append(f"| {group} | {len(tags_list)} | {len(named_list)} | " + " | ".join(cells) + " |")
    lines += ["", "Проценты полей считаются среди точек **с названием**.", "",
              "## Топ категорий", ""]
    for cat, n in Counter(p["category"] for p in pois).most_common(30):
        lines.append(f"- `{cat}` — {n}")

    report = "\n".join(lines)
    out = ROOT / "reports" / "osm_coverage.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
