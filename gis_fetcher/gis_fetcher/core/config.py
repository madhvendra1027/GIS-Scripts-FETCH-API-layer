"""
Loads config/providers.yaml, which holds per-provider settings: API
keys (for sources that need them), retry counts, min request intervals,
and default query params. Keeping this in YAML (instead of hardcoding
into provider classes) means you can add/rotate API keys or tune rate
limits without touching Python code.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml


def load_config(path: str = "config/providers.yaml") -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"providers": {}}

    with open(p, "r") as f:
        raw = yaml.safe_load(f) or {}

    # Allow ${ENV_VAR} placeholders in the YAML so API keys don't have
    # to be committed to disk in plaintext.
    raw = _expand_env_vars(raw)
    raw.setdefault("providers", {})
    return raw


def _expand_env_vars(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _expand_env_vars(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_expand_env_vars(v) for v in node]
    if isinstance(node, str) and node.startswith("${") and node.endswith("}"):
        env_name = node[2:-1]
        return os.environ.get(env_name, "")
    return node
