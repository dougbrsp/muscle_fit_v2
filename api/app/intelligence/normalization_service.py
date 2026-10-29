from __future__ import annotations

import json
from typing import Any


class NormalizationService:
    @classmethod
    def normalize_text(cls, value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        if not text:
            return ""

        fixed = text
        for _ in range(4):
            if not any(marker in fixed for marker in ("Ã", "Â", "â€", "�")):
                break
            changed = False
            for encoding in ("latin1", "cp1252"):
                try:
                    candidate = fixed.encode(encoding).decode("utf-8")
                    if candidate and candidate != fixed:
                        fixed = candidate
                        changed = True
                        break
                except Exception:
                    pass
            if not changed:
                break

        return (
            fixed.replace("â€“", "–")
            .replace("â€”", "—")
            .replace("â€˜", "‘")
            .replace("â€™", "’")
            .replace("â€œ", '“')
            .replace("â€\x9d", '”')
            .replace("Âº", "º")
            .replace("Âª", "ª")
            .replace("Â", "")
            .strip()
        )

    @classmethod
    def normalize_percent(cls, value: Any) -> float | None:
        if value is None:
            return None
        try:
            numeric = float(value)
        except Exception:
            return None
        if numeric < 0:
            return None
        if 0 < numeric <= 1:
            numeric *= 100.0
        if numeric > 100:
            return None
        return round(numeric, 2)

    @classmethod
    def normalize_json_payload(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}
