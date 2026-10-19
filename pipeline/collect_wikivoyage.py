"""Путеводитель Wikivoyage об Астане: описания достопримечательностей «глазами путешественника».

Запуск:  python pipeline/collect_wikivoyage.py   (нужна сеть)
Результат:
  data/raw/wikivoyage_astana.json — исходный текст статьи (вики-разметка)
  data/wikivoyage_astana.json     — места из разделов «See» и «Do»: название, Q Wikidata, координаты, описание

Английская статья — основная: в русской почти ничего нет. Текст — CC BY-SA, ссылка на статью обязательна.
Часы работы и цены из путеводителя не берём: они быстро устаревают, а часы мы честно берём из OSM.
Описание — материал для рассказа гида (Gemini пересказывает его по-русски), а не факт для подбора.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline.collect_osm import HEADERS  # noqa: E402

RAW_PATH = ROOT / "data" / "raw" / "wikivoyage_astana.json"
OUT_PATH = ROOT / "data" / "wikivoyage_astana.json"
API = "https://en.wikivoyage.org/w/api.php"
PAGE = "Astana"
PAGE_URL = "https://en.wikivoyage.org/wiki/Astana"
LISTING_TYPES = ("see", "do")
CONTENT_MAX = 700


def templates(wikitext: str, names: tuple[str, ...]) -> list[tuple[str, str]]:
    """Шаблоны {{see …}} с учётом вложенных {{…}} → [(тип, тело шаблона)]."""
    out = []
    for m in re.finditer(r"\{\{\s*(" + "|".join(names) + r")\b", wikitext):
        depth, i = 0, m.start()
        while i < len(wikitext):
            if wikitext.startswith("{{", i):
                depth, i = depth + 1, i + 2
            elif wikitext.startswith("}}", i):
                depth, i = depth - 1, i + 2
                if depth == 0:
                    out.append((m.group(1), wikitext[m.end():i - 2]))
                    break
            else:
                i += 1
    return out


def params(body: str) -> dict[str, str]:
    """«| name=… | content=…» → словарь; «|» внутри [[ссылок|…]] и {{шаблонов|…}} не делит параметры."""
    parts, depth, current = [], 0, ""
    for i, ch in enumerate(body):
        two = body[i:i + 2]
        if two in ("[[", "{{"):
            depth += 1
        elif two in ("]]", "}}") and depth:
            depth -= 1
        if ch == "|" and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    out = {}
    for part in parts:
        if "=" in part:
            key, value = part.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def plain(text: str) -> str:
    """Вики-разметка → текст: [[a|b]] → b, ''курсив'' → курсив, служебные шаблоны — прочь."""
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", text)
    while re.search(r"\{\{[^{}]*\}\}", text):
        text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def listings(wikitext: str) -> list[dict]:
    out = []
    for kind, body in templates(wikitext, LISTING_TYPES):
        p = params(body)
        content = plain(p.get("content", ""))
        name = plain(p.get("name", ""))
        if not name or not content:
            continue
        try:
            lat, lon = float(p.get("lat") or "nan"), float(p.get("long") or "nan")
        except ValueError:
            lat = lon = float("nan")
        if len(content) > CONTENT_MAX:
            content = content[:CONTENT_MAX].rsplit(" ", 1)[0] + "…"
        out.append({"type": kind, "name": name, "alt": plain(p.get("alt", "")) or None,
                    "wikidata": p.get("wikidata") or None,
                    "lat": None if lat != lat else lat, "lon": None if lon != lon else lon,
                    "content": content})
    return out


def main() -> None:
    resp = requests.get(API, params={"action": "parse", "page": PAGE, "prop": "wikitext|revid",
                                     "format": "json", "formatversion": 2}, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    data = resp.json()["parse"]
    items = listings(data["wikitext"])
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    meta = {"source": f"Wikivoyage, статья «{PAGE}» (CC BY-SA)", "url": PAGE_URL, "revid": data.get("revid"),
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "count": len(items)}
    OUT_PATH.write_text(json.dumps({"meta": meta, "listings": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Сохранено {len(items)} описаний (с Q Wikidata: {sum(1 for i in items if i['wikidata'])}) "
          f"→ {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
