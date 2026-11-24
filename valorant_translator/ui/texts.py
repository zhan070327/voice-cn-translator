"""Load the default Chinese UI copy from a separate resource."""
from __future__ import annotations

import json
from pathlib import Path

_RESOURCE = Path(__file__).resolve().parents[2] / "assets" / "i18n" / "zh_CN.json"
_STRINGS = json.loads(_RESOURCE.read_text(encoding="utf-8"))


def tr(key: str, **values) -> str:
    template = _STRINGS[key]
    return template.format(**values) if values else template
