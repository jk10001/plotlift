from __future__ import annotations

import hashlib
import json
from pathlib import Path
from string import Template
from typing import Any

import yaml

from .config import PROMPTS_PATH
from .series_point_limits import series_data_point_prompt_context


class PromptPack:
    def __init__(self, path: Path = PROMPTS_PATH) -> None:
        self.path = path
        self.raw_text = path.read_text(encoding="utf-8")
        self.data = yaml.safe_load(self.raw_text)
        self.version = str(self.data.get("version", "unknown"))
        self.app_context = series_data_point_prompt_context()
        hash_payload = {"prompt_text": self.raw_text, "app_context": self.app_context}
        self.hash = hashlib.sha256(json.dumps(hash_payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]

    def render(self, key: str, **variables: Any) -> str:
        node: Any = self.data
        for part in key.split("."):
            node = node[part]
        text = str(node)
        shared = self.data.get("shared", {})
        context = {**shared, **self.app_context, **{name: str(value) for name, value in variables.items()}}
        for name, value in context.items():
            text = text.replace("{{ " + name + " }}", str(value))
        return Template(text).safe_substitute(**context)


def load_prompt_pack() -> PromptPack:
    return PromptPack()
