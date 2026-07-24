"""JSON export: the full provider record, including nested evidence, with
nothing collapsed into delimited strings (unlike the CSV export, JSON can
represent the evidence lists natively).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from sil_research.models import ProviderRecord


def _default(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value)} is not JSON serialisable")


def export_providers_json(providers: list[ProviderRecord], output_path: str | Path) -> int:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(provider) for provider in providers]
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=_default)
    return len(providers)
