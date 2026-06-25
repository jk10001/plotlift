from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from PIL import Image

from app import artifacts
from app.models import Asset, PromptMetadata, RunSettings, RunState
from app.prompts import load_prompt_pack
from app.stages import crop as crop_stage


def test_crop_stage_passes_conversation_history_for_confirmation(tmp_path: Path, monkeypatch) -> None:
    class DummyConfig:
        runs_dir = tmp_path
        max_crop_attempts = 2

    cfg = DummyConfig()
    monkeypatch.setattr(artifacts, "get_config", lambda: cfg)
    monkeypatch.setattr(crop_stage, "get_config", lambda: cfg)

    run_id = "crophistory"
    root = tmp_path / run_id
    image_path = root / "uploads" / "chart.png"
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (100, 80), "white").save(image_path)
    state = RunState(
        run_id=run_id,
        settings=RunSettings(model_id="gemini-3.5-flash", image_detail="high", mock_mode=False),
        prompt_metadata=PromptMetadata(prompt_version="test", prompt_hash="hash"),
        upload_filename="chart.png",
        original_path="uploads/chart.png",
        canonical_image=Asset(path="uploads/chart.png", width=100, height=80),
    )
    client = FakeCropClient()

    crop_stage.run_crop_stage(state, load_prompt_pack(), client)

    assert len(client.calls) == 2
    assert client.calls[0]["conversation_history"] == []
    assert client.calls[1]["conversation_history"] == [
        {
            "user_prompt": client.calls[0]["user_prompt"],
            "image_path": str(image_path),
            "model_text": client.first_model_text,
        }
    ]


class FakeCropClient:
    first_model_text = '{"response_kind":"proposal","revision_reason":"initial","proposal":{"bbox":{"left":100,"top":100,"right":900,"bottom":900},"confidence":0.8,"warnings":[],"unsupported_flags":[]}}'

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def call_crop_tool(self, **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        self.calls.append(copy.deepcopy(kwargs))
        attempt = len(self.calls)
        request = {
            "provider": "gemini",
            "contents": [],
            "prompt_cache_key": kwargs.get("prompt_cache_key"),
        }
        if attempt == 1:
            return request, {
                "raw": {"id": "resp_1"},
                "text": self.first_model_text,
                "arguments": {
                    "response_kind": "proposal",
                    "revision_reason": "initial",
                    "proposal": {
                        "bbox": {"left": 100, "top": 100, "right": 900, "bottom": 900},
                        "confidence": 0.8,
                        "warnings": [],
                        "unsupported_flags": [],
                    },
                },
                "response_id": "resp_1",
                "function_call_id": None,
            }
        return request, {
            "raw": {"id": "resp_2"},
            "text": '{"response_kind":"accept_previous","revision_reason":null,"proposal":null}',
            "arguments": {
                "response_kind": "accept_previous",
                "revision_reason": None,
                "proposal": None,
            },
            "response_id": "resp_2",
            "function_call_id": None,
        }
