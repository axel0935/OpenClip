"""Export the stable OpenAPI contract used by the frontend generator."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from openclip.api.app import create_app  # noqa: E402


def main() -> None:
    """Write a stable, sorted OpenAPI document."""
    output = ROOT / "docs" / "openapi.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
