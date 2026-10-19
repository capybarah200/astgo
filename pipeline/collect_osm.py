"""Сбор POI Астаны из OpenStreetMap через Overpass API.

Запуск:  python pipeline/collect_osm.py
Результат:
  data/raw/osm_astana.json  — сырой ответ Overpass
  data/poi_osm.json         — нормализованные точки (одна схема для всех источников)
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
RAW_PATH = ROOT / "data" / "raw" / "osm_astana.json"
OUT_PATH = ROOT / "data" / "poi_osm.json"

# Город с пригородами и аэропортом: юг, запад, север, восток
BBOX = (50.98, 71.20, 51.28, 71.75)

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
HEADERS = {"User-Agent": "astana-walk-assistant/0.1 (hackathon prototype)"}

SELECTORS = {
    "amenity": "cafe|restaurant|fast_food|ice_cream|food_court|bar|pub|nightclub|cinema|theatre|"
               "arts_centre|library|planetarium|exhibition_centre|music_venue|events_venue|"
               "community_centre|fountain|place_of_worship|marketplace|coworking_space",
    "tourism": "attraction|museum|gallery|viewpoint|zoo|aquarium|theme_park|artwork|picnic_site",
    "leisure": "park|garden|nature_reserve|playground|water_park|amusement_arcade|trampoline_park|"
               "indoor_play|miniature_golf|sports_centre|stadium|ice_rink|escape_game|"
               "bowling_alley|dance|fitness_centre|swimming_pool",
    "historic": ".*",
    "shop": "mall|department_store|books",
}

# Порядок важен: первый совпавший ключ задаёт основную категорию
CATEGORY_KEYS = ["tourism", "historic", "leisure", "amenity", "shop"]


def build_query() -> str:
    bbox = ",".join(str(x) for x in BBOX)
    parts = [f'  nwr["{key}"~"^({values})$"]({bbox});' for key, values in SELECTORS.items()]
    return "[out:json][timeout:180];\n(\n" + "\n".join(parts) + "\n);\nout center tags;"


def fetch(query: str) -> dict:
    for url in ENDPOINTS:
        try:
            print(f"Overpass: {url}", file=sys.stderr)
            resp = requests.post(url, data={"data": query}, headers=HEADERS, timeout=240)
            resp.raise_for_status()
            data = resp.json()
            # Таймаут или нехватку памяти Overpass возвращает с кодом 200: remark + обрезанный список
            if data.get("remark"):
                raise ValueError(f"ответ неполный: {data['remark']}")
            if not data.get("elements"):
                raise ValueError("пустой ответ")
            return data
        except (requests.RequestException, ValueError) as exc:
            print(f"  не получилось: {exc}", file=sys.stderr)
            time.sleep(2)
    raise SystemExit("Все Overpass-серверы недоступны")


def primary_category(tags: dict) -> str | None:
    """Первый ключ из CATEGORY_KEYS, значение которого прошло фильтр SELECTORS.

    Ресторан в отеле (tourism=hotel + amenity=restaurant) — это amenity=restaurant,
    а не tourism=hotel: отели мы не собираем.
    """
    for key in CATEGORY_KEYS:
        value = tags.get(key)
        if value is not None and re.fullmatch(SELECTORS[key], value):
            return f"{key}={value}"
    return None


def normalize(element: dict) -> dict | None:
    tags = element.get("tags", {})
    lat = element.get("lat") or element.get("center", {}).get("lat")
    lon = element.get("lon") or element.get("center", {}).get("lon")
    category = primary_category(tags)
    if lat is None or lon is None or category is None:
        return None
    return {
        "id": f"osm:{element['type']}/{element['id']}",
        "name": tags.get("name"),
        "name_kk": tags.get("name:kk"),
        "name_ru": tags.get("name:ru"),
        "name_en": tags.get("name:en"),
        "lat": lat,
        "lon": lon,
        "category": category,
        "opening_hours": tags.get("opening_hours"),
        "website": tags.get("website") or tags.get("contact:website"),
        "phone": tags.get("phone") or tags.get("contact:phone"),
        "description": tags.get("description:ru") or tags.get("description"),
        "wikidata": tags.get("wikidata"),
        "wheelchair": tags.get("wheelchair"),
        "tags": tags,
        "sources": ["osm"],
    }


def main() -> None:
    data = fetch(build_query())
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    pois = [p for p in (normalize(e) for e in data.get("elements", [])) if p]
    meta = {
        "source": "OpenStreetMap contributors (ODbL)",
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bbox": BBOX,
        "count": len(pois),
    }
    OUT_PATH.write_text(json.dumps({"meta": meta, "pois": pois}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"Сохранено {len(pois)} точек → {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
