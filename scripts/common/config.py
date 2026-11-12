"""Shared helpers: project root, config loading."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_config():
    with open(ROOT / "config" / "floodguard.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def p(*parts):
    """Path relative to project root."""
    return ROOT.joinpath(*parts)
