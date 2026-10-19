"""Настройки из переменных окружения и файла .env (ключи в коде и в git не храним)."""

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Модели по очереди, через запятую: у каждой своя бесплатная квота, кончилась у одной — отвечает следующая.
# Бесплатный уровень (AI Studio → Rate Limit, 2026-10-18): 3.8-flash и 3.6-flash — 5 в минуту и 20 в день,
# 3.5-flash-lite — 15 в минуту и 500 в день. Порядок — от сильнейшей к слабой: первые реплики дня пишет
# лучшая модель, Flash-Lite с большой квотой — основной запас. Сменить — GEMINI_MODEL в .env
DEFAULT_MODEL = "gemini-3.8-flash,gemini-3.6-flash,gemini-3.5-flash-lite"
DEFAULT_THINKING = "low"  # уровень «размышления» модели; GEMINI_THINKING= (пусто) — не передавать вовсе


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE построчно; комментарии и пустые строки пропускаются, кавычки снимаются."""
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_model: str
    gemini_thinking: str = DEFAULT_THINKING

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key)


def load_settings(env_path: Path = ROOT / ".env") -> Settings:
    values = {**read_env_file(env_path), **{k: v for k, v in os.environ.items() if k.startswith("GEMINI_")}}
    return Settings(gemini_api_key=values.get("GEMINI_API_KEY", ""),
                    gemini_model=values.get("GEMINI_MODEL") or DEFAULT_MODEL,
                    gemini_thinking=values.get("GEMINI_THINKING", DEFAULT_THINKING))
