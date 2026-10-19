"""Районы, ЖК, улицы и станции Астаны из OpenStreetMap — чтобы понимать «до Жагалау», «я на Кенесары».

Запуск:  python pipeline/collect_areas.py              (нужна сеть)
         python pipeline/collect_areas.py --from-raw   (пересобрать из data/raw/areas_astana.json, без сети)
Результат:
  data/raw/areas_astana.json — сырой ответ Overpass
  data/areas_osm.json        — районы, ЖК, улицы, вокзалы и станции ЛРТ: название, точка; у улиц — точки
                               вдоль улицы; у станций — признак ЛРТ (подсказка «до центра — на ЛРТ»)

Это не места для прогулки (их собирает collect_osm.py), а названия частей города: где человек
и куда идёт. Одна улица в OSM — много отрезков: склеиваем их по названию, но далёкие друг от друга
части (одно название в разных концах города) остаются разными записями.
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.build_poi import distance_m  # noqa: E402
from pipeline.collect_osm import BBOX, fetch  # noqa: E402

RAW_PATH = ROOT / "data" / "raw" / "areas_astana.json"
OUT_PATH = ROOT / "data" / "areas_osm.json"

PLACES = "suburb|quarter|neighbourhood|city_block|locality|square"
HIGHWAYS = "trunk|primary|secondary|tertiary|unclassified|residential|living_street|pedestrian"
SAME_STREET_GAP_M = 1500  # отрезки одного названия дальше друг от друга — разные улицы
MAX_POINTS = 80            # точек на улицу: хватает, чтобы найти ближайшую часть улицы


def build_query() -> str:
    bbox = ",".join(str(x) for x in BBOX)
    return (f'[out:json][timeout:180];\n(\n'
            f'  nwr["place"~"^({PLACES})$"]["name"]({bbox});\n'
            f'  wr["landuse"="residential"]["name"]({bbox});\n'
            f'  wr["building"~"^(apartments|residential)$"]["name"]({bbox});\n'
            f'  way["highway"~"^({HIGHWAYS})$"]["name"]({bbox});\n'
            f'  nwr["railway"="station"]["name"]({bbox});\n'
            f');\nout center tags;')


def kind_of(tags: dict) -> str | None:
    """district — микрорайон или часть города; complex — жилой комплекс; street; square — площадь;
    station — вокзал или станция ЛРТ (проверяется первым: у здания станции бывает и тег building)."""
    if tags.get("railway") == "station":
        return "station"
    if tags.get("place") == "square":
        return "square"
    if tags.get("place") or tags.get("landuse") == "residential":
        return "district"
    if tags.get("building"):
        return "complex"
    if tags.get("highway"):
        return "street"
    return None


def _clusters(points: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Точки одного названия → группы, где соседние точки ближе SAME_STREET_GAP_M."""
    groups: list[list[tuple[float, float]]] = []
    for p in points:
        near = [g for g in groups if any(distance_m(_pt(p), _pt(q)) <= SAME_STREET_GAP_M for q in g)]
        merged = [p] + [q for g in near for q in g]
        groups = [g for g in groups if not any(g is n for n in near)] + [merged]
    return groups


def _thin(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Не больше MAX_POINTS точек равномерно по всей улице, а не первые по списку."""
    step = -(-len(points) // MAX_POINTS)
    return points[::step]


def _pt(p: tuple[float, float]) -> dict:
    return {"lat": p[0], "lon": p[1]}


def normalize(elements: list[dict]) -> list[dict]:
    """Элементы Overpass → записи: одно название одного вида в одной части города — одна запись."""
    by_name: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for el in elements:
        tags = el.get("tags", {})
        name = (tags.get("name") or "").strip()
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        kind = kind_of(tags)
        if not kind or lat is None or lon is None or len(name) < 3 or (name[0].isdigit() and len(name) <= 6):
            continue  # «5», «12А», «5/1» — номер дома, а не название
        by_name[(kind, name.lower())].append({**el, "lat": lat, "lon": lon})

    areas = []
    for (kind, _), els in sorted(by_name.items()):
        points = [(round(e["lat"], 5), round(e["lon"], 5)) for e in els]
        for group in _clusters(points):
            in_group = set(group)
            first = next(e for e in els if (round(e["lat"], 5), round(e["lon"], 5)) in in_group)
            tags = first.get("tags", {})
            lat = sum(p[0] for p in group) / len(group)
            lon = sum(p[1] for p in group) / len(group)
            # точка самой улицы ближе всего к середине: у изогнутой улицы среднее может лечь мимо неё
            rep = min(group, key=lambda p: distance_m(_pt(p), {"lat": lat, "lon": lon}))
            areas.append({
                "id": f"osm:{first['type']}/{first['id']}",
                "kind": kind,
                "name": tags.get("name"),
                "name_ru": tags.get("name:ru"),
                "name_kk": tags.get("name:kk"),
                "name_en": tags.get("name:en"),
                "lat": rep[0],
                "lon": rep[1],
                "points": _thin(sorted(in_group)) if kind == "street" else [],
                "parts": len(group),
                "lrt": tags.get("station") == "light_rail" or tags.get("light_rail") == "yes",
                "sources": ["osm"],
            })
    return areas


def main() -> None:
    if "--from-raw" in sys.argv:
        data = json.loads(RAW_PATH.read_text(encoding="utf-8"))
    else:
        data = fetch(build_query())
        RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
        RAW_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    areas = normalize(data.get("elements", []))
    counts = defaultdict(int)
    for a in areas:
        counts[a["kind"]] += 1
    meta = {
        "source": "OpenStreetMap contributors (ODbL)",
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bbox": BBOX,
        "count": len(areas),
        "by_kind": dict(counts),
    }
    OUT_PATH.write_text(json.dumps({"meta": meta, "areas": areas}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Сохранено {len(areas)} записей {dict(counts)} → {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
