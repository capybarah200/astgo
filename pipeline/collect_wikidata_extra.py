"""Дополнение к Wikidata: тип объекта, фото с Commons (автор и лицензия), статья английской Википедии.

Запуск:  python pipeline/collect_wikidata_extra.py   (нужна сеть; после collect_wikidata.py)
Вход:    data/wikidata_astana.json — какие объекты (Q) есть в рамке Астаны
Результат:
  data/raw/wikidata_extra.json — сырые ответы SPARQL, Commons и en.wikipedia
  data/wikidata_extra.json     — по каждому Q: типы (P31), фото (P18) с автором и лицензией,
                                 вводный абзац en.wikipedia — для мест без русской статьи

Зачем: тип (P31) нужен, чтобы добавить в базу главные объекты, которых нет в выборке OSM
(Ак Орда, Дворец мира и согласия), — по типу понятно, музей это, мечеть или монумент. Фото отвечает
на «как это выглядит». Лицензии: Wikidata — CC0; фото — у каждого своя (CC BY-SA, CC0…), автор и
лицензия показываются под фото; текст Википедии — CC BY-SA 4.0, ссылка на статью обязательна.
"""

import html
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline.collect_osm import BBOX, HEADERS  # noqa: E402
from pipeline.collect_wikidata import SPARQL_URL, fetch_intros, fetch_sparql  # noqa: E402

WIKIDATA_PATH = ROOT / "data" / "wikidata_astana.json"
RAW_PATH = ROOT / "data" / "raw" / "wikidata_extra.json"
OUT_PATH = ROOT / "data" / "wikidata_extra.json"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
EN_WIKI_API = "https://en.wikipedia.org/w/api.php"
FILES_PER_REQUEST = 50  # предел API для titles
THUMB_WIDTH = 640


def build_query() -> str:
    south, west, north, east = BBOX
    return f"""
SELECT ?item ?type ?typeRu ?typeEn ?image ?enArticle WHERE {{
  SERVICE wikibase:box {{
    ?item wdt:P625 ?loc .
    bd:serviceParam wikibase:cornerSouthWest "Point({west} {south})"^^geo:wktLiteral .
    bd:serviceParam wikibase:cornerNorthEast "Point({east} {north})"^^geo:wktLiteral .
  }}
  OPTIONAL {{ ?item wdt:P31 ?type .
             OPTIONAL {{ ?type rdfs:label ?typeRu FILTER(LANG(?typeRu) = "ru") }}
             OPTIONAL {{ ?type rdfs:label ?typeEn FILTER(LANG(?typeEn) = "en") }} }}
  OPTIONAL {{ ?item wdt:P18 ?image }}
  OPTIONAL {{ ?enArticle schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> }}
}}"""


def parse_rows(sparql: dict) -> dict[str, dict]:
    """Строки SPARQL → {Q: {types, image_file, enwiki_title, enwiki_url}}; фото — первое по алфавиту."""
    out: dict[str, dict] = {}
    for row in sparql["results"]["bindings"]:
        q = row["item"]["value"].rsplit("/", 1)[1]
        rec = out.setdefault(q, {"types": [], "image_file": None, "enwiki_title": None, "enwiki_url": None})
        if "type" in row:
            tq = row["type"]["value"].rsplit("/", 1)[1]
            if all(t["q"] != tq for t in rec["types"]):
                rec["types"].append({"q": tq, "ru": row.get("typeRu", {}).get("value"),
                                     "en": row.get("typeEn", {}).get("value")})
        if "image" in row:
            name = unquote(row["image"]["value"].rsplit("/", 1)[1]).replace("_", " ")
            if rec["image_file"] is None or name < rec["image_file"]:
                rec["image_file"] = name
        if "enArticle" in row and rec["enwiki_url"] is None:
            url = row["enArticle"]["value"]
            rec["enwiki_url"] = url
            rec["enwiki_title"] = unquote(url.rsplit("/wiki/", 1)[1]).replace("_", " ")
    return out


def plain(text: str | None) -> str | None:
    """HTML из extmetadata Commons («<a href…>Автор</a>») → простой текст."""
    if not text:
        return None
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text))).strip() or None


def fetch_images(files: list[str]) -> tuple[dict[str, dict], list[dict]]:
    """Файлы Commons → {имя: {thumb, page, artist, license, license_url}} + сырые ответы."""
    images, raw = {}, []
    for start in range(0, len(files), FILES_PER_REQUEST):
        batch = files[start:start + FILES_PER_REQUEST]
        params = {"action": "query", "format": "json", "formatversion": 2, "prop": "imageinfo",
                  "iiprop": "url|extmetadata", "iiurlwidth": THUMB_WIDTH,
                  "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl",
                  "titles": "|".join(f"File:{f}" for f in batch)}
        resp = requests.get(COMMONS_API, params=params, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        raw.append(data)
        renamed = {n["to"]: n["from"] for n in data.get("query", {}).get("normalized", [])}
        for page in data.get("query", {}).get("pages", []):
            info = (page.get("imageinfo") or [None])[0]
            if not info:
                continue
            meta = info.get("extmetadata", {})
            name = renamed.get(page["title"], page["title"]).removeprefix("File:")
            images[name] = {
                "thumb": info.get("thumburl") or info.get("url"),
                "page": info.get("descriptionurl"),
                "artist": plain(meta.get("Artist", {}).get("value")),
                "license": plain(meta.get("LicenseShortName", {}).get("value")),
                "license_url": meta.get("LicenseUrl", {}).get("value"),
            }
        time.sleep(0.5)
        print(f"  Commons: {min(start + FILES_PER_REQUEST, len(files))}/{len(files)}", file=sys.stderr)
    return images, raw


def main() -> None:
    known = json.loads(WIKIDATA_PATH.read_text(encoding="utf-8"))["items"]
    by_q = {i["q"]: i for i in known}
    print(f"Wikidata: SPARQL ({SPARQL_URL})", file=sys.stderr)
    sparql = fetch_sparql(build_query())
    extra = {q: rec for q, rec in parse_rows(sparql).items() if q in by_q}

    files = sorted({r["image_file"] for r in extra.values() if r["image_file"]})
    images, raw_commons = fetch_images(files)
    # английская статья — только тем, у кого нет русской: иначе описание уже есть
    titles = [r["enwiki_title"] for q, r in extra.items() if r["enwiki_title"] and not by_q[q]["ruwiki_intro"]]
    intros, raw_wiki = fetch_intros(titles, api=EN_WIKI_API)

    items = {}
    for q, rec in sorted(extra.items(), key=lambda kv: int(kv[0][1:])):
        image = images.get(rec["image_file"]) if rec["image_file"] else None
        items[q] = {"types": rec["types"],
                    "image": {"file": rec["image_file"], **image} if image else None,
                    "enwiki_url": rec["enwiki_url"],
                    "enwiki_intro": intros.get(rec["enwiki_title"]) if rec["enwiki_title"] else None}

    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_text(json.dumps({"sparql": sparql, "commons": raw_commons, "wikipedia_en": raw_wiki},
                                   ensure_ascii=False), encoding="utf-8")
    meta = {
        "source": "Wikidata (CC0); Wikimedia Commons (лицензия у каждого фото своя); en.wikipedia.org (CC BY-SA 4.0)",
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(items),
        "with_type": sum(1 for i in items.values() if i["types"]),
        "with_image": sum(1 for i in items.values() if i["image"]),
        "with_en_intro": sum(1 for i in items.values() if i["enwiki_intro"]),
    }
    OUT_PATH.write_text(json.dumps({"meta": meta, "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Сохранено {meta['count']} объектов: с типом {meta['with_type']}, с фото {meta['with_image']}, "
          f"с английской статьёй {meta['with_en_intro']} → {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
