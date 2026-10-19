"""python -m app [--host 127.0.0.1] [--port 8000]

На хостинге адрес и порт берутся из переменных окружения HOST и PORT (их задаёт платформа).
"""

import argparse
import os

import uvicorn

from app.server import create_app

parser = argparse.ArgumentParser(description="Ассистент прогулок по Астане")
parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"),
                    help="0.0.0.0 — открыть с телефона в той же сети")
parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
args = parser.parse_args()

# access_log выключен: в журнале не остаются адреса и время запросов пользователей
uvicorn.run(create_app(), host=args.host, port=args.port, access_log=False)
