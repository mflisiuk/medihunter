"""Configuration loader for Medicover monitor."""

import json
from pathlib import Path

import yaml

CONFIG_DIR = Path.home() / ".config" / "medicover"
SPECIALTY_MAP_FILE = Path(__file__).parent / "data" / "specialty_map.json"
CONFIG_YAML = Path(__file__).parent / "config.yaml"


def load_specialty_map() -> dict[str, list[str]]:
    """Load specialty name -> list of specialty IDs mapping."""
    if SPECIALTY_MAP_FILE.exists():
        return json.loads(SPECIALTY_MAP_FILE.read_text())
    return {}


def load_config() -> dict:
    """Load config.yaml if it exists."""
    if CONFIG_YAML.exists():
        with open(CONFIG_YAML) as f:
            return yaml.safe_load(f) or {}
    return {}


def get_profile(profile_name: str) -> dict | None:
    """Get a monitoring profile from config.yaml."""
    config = load_config()
    profiles = config.get("monitoring_profiles", {})
    return profiles.get(profile_name)


def resolve_specialty_ids(specialty_name: str) -> list[int]:
    """Resolve a specialty name to a list of IDs using the specialty map.

    Returns empty list if not found.
    """
    smap = load_specialty_map()
    ids = smap.get(specialty_name, [])
    return [int(i) for i in ids]


def find_specialty_name(query: str) -> list[str]:
    """Find specialty names matching a query (case-insensitive substring)."""
    smap = load_specialty_map()
    return [name for name in smap if query.lower() in name.lower()]
