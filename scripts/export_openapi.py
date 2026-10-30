"""Выгружает OpenAPI-спецификацию приложения в docs/openapi.yaml."""
from pathlib import Path

import yaml

from app.main import app

ROOT = Path(__file__).resolve().parent.parent
spec = app.openapi()
spec["servers"] = [{"url": "http://109.123.242.26:8000", "description": "Демо-стенд"},
                   {"url": "http://127.0.0.1:8000", "description": "Локальный запуск"}]
(ROOT / "docs" / "openapi.yaml").write_text(
    yaml.safe_dump(spec, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
print("docs/openapi.yaml:", len(spec["paths"]), "paths")
