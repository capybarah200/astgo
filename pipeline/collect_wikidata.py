"""Сбор объектов Wikidata в рамке Астаны и вводных абзацев статей русской Википедии.

Запуск:  python pipeline/collect_wikidata.py
Результат:
  data/raw/wikidata_astana.json  — сырые ответы SPARQL и API Википедии
  data/wikidata_astana.json      — объекты: Q, координаты, подписи ru/en/kk, описание, статья ru.wikipedia

Лицензии: Wikidata — CC0; текст Википедии — CC BY-SA 4.0 (в интерфейсе нужна ссылка на статью).
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # чтобы `python pipeline/...py` видел пакет pipeline
from pipeline.collect_osm import BBOX, HEADERS  # noqa: E402

RAW_PATH = ROOT / "data" / "raw" / "wikidata_astana.json"
OUT_PATH = ROOT / "data" / "wikidata_astana.json"

SPARQL_URL = "https://query.wikidata.org/sparql"
WIKI_API = "https://ru.wikipedia.org/w/api.php"
EXTRACTS_PER_REQUEST = 20  # предел API для exintro


def build_query() -> str:
    south, west, north, east = BBOX
    return f"""
SELECT ?item ?loc ?ru ?en ?kk ?descRu ?article WHERE {{
  SERVICE wikibase:box {{
    ?item wdt:P625 ?loc .
    bd:serviceParam wikibase:cornerSouthWest "Point({west} {south})"^^geo:wktLiteral .
    bd:serviceParam wikibase:cornerNorthEast "Point({east} {north})"^^geo:wktLiteral .
  }}
  OPTIONAL {{ ?item rdfs:label ?ru FILTER(LANG(?ru) = "ru") }}
  OPTIONAL {{ ?item rdfs:label ?en FILTER(LANG(?en) = "en") }}
  OPTIONAL {{ ?item rdfs:label ?kk0 FILTER(LANG(?kk0) = "kk") }}
  OPTIONAL {{ ?item rdfs:label ?kk1 FILTER(LANG(?kk1) = "kk-cyrl") }}
  OPTIONAL {{ ?item rdfs:label ?kk2 FILTER(LANG(?kk2) = "kk-kz") }}
  BIND(COALESCE(?kk0, ?kk1, ?kk2) AS ?kk)
  OPTIONAL {{ ?item schema:description ?descRu FILTER(LANG(?descRu) = "ru") }}
  OPTIONAL {{ ?article schema:about ?item ; schema:isPartOf <https://ru.wikipedia.org/> }}
}}"""


def fetch_sparql(query: str) -> dict:
    resp = requests.get(SPARQL_URL, params={"query": query, "format": "json"},
                        headers=HEADERS, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("results", {}).get("bindings"):
        raise SystemExit("Wikidata вернула пустой ответ — файлы не перезаписаны")
    return data


def parse_items(sparql: dict) -> list[dict]:
    """Строки SPARQL → по одному объекту на Q.

    У объекта бывает несколько координат (у ледового дворца «Алау» две, в 55 м). Порядок строк SPARQL
    не гарантирован, поэтому берём наименьшую пару (широта, долгота) — одну и ту же при любом порядке.
    """
    items = {}
    for row in sparql["results"]["bindings"]:
        q = row["item"]["value"].rsplit("/", 1)[1]
        m = re.match(r"Point\(([-\d.]+) ([-\d.]+)\)", row["loc"]["value"])
        if not m:
            continue
        lat, lon = float(m.group(2)), float(m.group(1))
        item = items.setdefault(q, {
            "q": q, "lat": lat, "lon": lon,
            "label_ru": None, "label_en": None, "label_kk": None,
            "description_ru": None, "ruwiki_title": None, "ruwiki_url": None, "ruwiki_intro": None,
        })
        if (lat, lon) < (item["lat"], item["lon"]):
            item["lat"], item["lon"] = lat, lon
        for key, field in (("ru", "label_ru"), ("en", "label_en"), ("kk", "label_kk"),
                           ("descRu", "description_ru")):
            if key in row and item[field] is None:
                item[field] = row[key]["value"]
        if "article" in row and item["ruwiki_url"] is None:
            url = row["article"]["value"]
            item["ruwiki_url"] = url
            item["ruwiki_title"] = unquote(url.rsplit("/wiki/", 1)[1]).replace("_", " ")
    return sorted(items.values(), key=lambda i: int(i["q"][1:]))


def fetch_intros(titles: list[str], api: str = WIKI_API) -> tuple[dict[str, str], list[dict]]:
    """Вводные абзацы статей простым текстом: {заголовок: текст} + сырые ответы. api — какой Википедии."""
    intros, raw = {}, []
    for start in range(0, len(titles), EXTRACTS_PER_REQUEST):
        batch = titles[start:start + EXTRACTS_PER_REQUEST]
        params = {"action": "query", "format": "json", "formatversion": 2, "prop": "extracts",
                  "exintro": 1, "explaintext": 1, "exlimit": EXTRACTS_PER_REQUEST, "redirects": 1,
                  "titles": "|".join(batch)}
        cont = {}
        while True:
            resp = requests.get(api, params={**params, **cont}, headers=HEADERS, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            raw.append(data)
            query = data.get("query", {})
            # запрошенный заголовок → итоговый (после нормализации и редиректов)
            renamed = {r["from"]: r["to"] for r in query.get("normalized", []) + query.get("redirects", [])}
            by_title = {p["title"]: p.get("extract") for p in query.get("pages", [])}
            for title in batch:
                final = renamed.get(title, title)
                final = renamed.get(final, final)  # нормализация, затем редирект
                if by_title.get(final):
                    intros[title] = by_title[final]
            if "continue" not in data:
                break
            cont = data["continue"]
        time.sleep(0.5)
        print(f"  Википедия: {min(start + EXTRACTS_PER_REQUEST, len(titles))}/{len(titles)}", file=sys.stderr)
    return intros, raw


def main() -> None:
    print("Wikidata: SPARQL", file=sys.stderr)
    sparql = fetch_sparql(build_query())
    items = parse_items(sparql)

    titles = [i["ruwiki_title"] for i in items if i["ruwiki_title"]]
    intros, raw_wiki = fetch_intros(titles)
    for item in items:
        if item["ruwiki_title"]:
            item["ruwiki_intro"] = intros.get(item["ruwiki_title"])

    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_text(json.dumps({"sparql": sparql, "wikipedia": raw_wiki}, ensure_ascii=False),
                        encoding="utf-8")
    meta = {
        "source": "Wikidata (CC0), ru.wikipedia.org (CC BY-SA 4.0)",
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bbox": BBOX,
        "count": len(items),
        "with_ruwiki_intro": sum(1 for i in items if i["ruwiki_intro"]),
    }
    OUT_PATH.write_text(json.dumps({"meta": meta, "items": items}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"Сохранено {len(items)} объектов, вводных абзацев {meta['with_ruwiki_intro']} из {len(titles)}"
          f" → {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
