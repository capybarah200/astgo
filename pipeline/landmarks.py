"""Справочник ориентиров — точки для кнопок «я здесь» на карте.

Запуск:  python pipeline/landmarks.py   (после build_poi.py; сеть не нужна)
Вход:    data/poi.json, data/wikidata_astana.json
Результат: data/landmarks.json

Список ориентиров задан ниже вручную, координаты и названия берутся из источника.
Аэропорта и вокзала нет в выборке OSM (aeroway и railway не собираем) — они из Wikidata.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POI_PATH = ROOT / "data" / "poi.json"
WIKIDATA_PATH = ROOT / "data" / "wikidata_astana.json"
OUT_PATH = ROOT / "data" / "landmarks.json"

# ключ, подпись кнопки, источник координат
LANDMARKS = [
    # Не MEGA Astana (Q1916973, на севере): в задании — Mega Silk Way у Экспо
    ("mega", "Mega Silk Way", "osm:way/499328514"),
    ("expo", "Экспо", "osm:way/454853233"),
    ("baiterek", "Байтерек", "osm:way/230401645"),
    ("khan_shatyr", "Хан Шатыр", "osm:way/460703779"),
    ("airport", "Аэропорт", "wikidata:Q174233"),
    ("station", "Вокзал Нурлы Жол", "wikidata:Q30642068"),
    # «Старый вокзал» — Астана-1 на севере; без этого «я на старом вокзале» уводило на Нурлы Жол
    ("old_station", "Вокзал Астана-1", "wikidata:Q800403"),
]


def resolve(source: str, pois: list[dict], items: list[dict]) -> dict:
    """Координаты и названия ориентира из источника.

    id OSM ищется и среди склеенных точек: после склейки дублей он может оказаться в merged_from.
    poi_id — место из poi.json, которым ориентир является сам (чтобы не советовать его же), или None.
    """
    kind, ref = source.split(":", 1)
    if kind == "osm":
        for p in pois:
            if p["id"] == source or source in p["merged_from"]:
                return {"lat": p["lat"], "lon": p["lon"], "name_kk": p["name_kk"],
                        "name_en": p["name_en"], "poi_id": p["id"]}
    elif kind == "wikidata":
        for item in items:
            if item["q"] == ref:
                poi_id = next((p["id"] for p in pois if p["wikidata"] == ref), None)
                return {"lat": item["lat"], "lon": item["lon"], "name_kk": item["label_kk"],
                        "name_en": item["label_en"], "poi_id": poi_id}
    raise LookupError(f"ориентир {source} не найден в данных")


def build(pois: list[dict], items: list[dict]) -> list[dict]:
    return [{"key": key, "label": label, **resolve(source, pois, items), "source": source}
            for key, label, source in LANDMARKS]


def main() -> None:
    pois = json.loads(POI_PATH.read_text(encoding="utf-8"))["pois"]
    items = json.loads(WIKIDATA_PATH.read_text(encoding="utf-8"))["items"]
    try:
        landmarks = build(pois, items)
    except LookupError as exc:
        raise SystemExit(f"{exc} — data/landmarks.json не перезаписан")
    meta = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "count": len(landmarks)}
    OUT_PATH.write_text(json.dumps({"meta": meta, "landmarks": landmarks}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    for lm in landmarks:
        print(f"{lm['label']:<18} {lm['lat']:.5f}, {lm['lon']:.5f}  {lm['source']:<22} poi: {lm['poi_id']}")
    print(f"Сохранено {len(landmarks)} ориентиров → {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
