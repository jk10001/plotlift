from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

from app.models import RunSettings
from app.openai_client import OpenAIChartClient


def test_crop_tool_continuation_includes_function_output_and_omits_cache_retention(tmp_path: Path) -> None:
    image_path = tmp_path / "chart.png"
    Image.new("RGB", (10, 10), "white").save(image_path)
    fake_client = FakeClient()
    client = OpenAIChartClient(api_key="test-key", mock_mode=False)
    client._client = fake_client

    client.call_crop_tool(
        settings=RunSettings(model_id="gpt-5.4-mini", image_detail="low", mock_mode=False),
        system_prompt="system",
        user_prompt="review",
        image_path=image_path,
        schema={"type": "object", "properties": {}, "additionalProperties": False},
        previous_response_id="resp_previous",
        previous_tool_call_id="call_previous",
        previous_tool_output={"status": "overlay_rendered"},
        prompt_cache_key="cache-key",
    )

    request = fake_client.responses.last_request
    assert request["previous_response_id"] == "resp_previous"
    assert request["prompt_cache_key"] == "cache-key"
    assert "prompt_cache_retention" not in request
    assert request["input"][0]["type"] == "function_call_output"
    assert request["input"][0]["call_id"] == "call_previous"
    assert json.loads(request["input"][0]["output"]) == {"status": "overlay_rendered"}
    assert request["input"][1]["role"] == "user"


class FakeClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()


class FakeResponses:
    def __init__(self) -> None:
        self.last_request: dict[str, Any] = {}

    def create(self, **request: Any) -> "FakeResponse":
        self.last_request = request
        return FakeResponse()


class FakeResponse:
    def model_dump(self, mode: str) -> dict[str, Any]:
        return {
            "id": "resp_next",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call_next",
                    "arguments": json.dumps(
                        {
                            "response_kind": "accept_previous",
                            "revision_reason": None,
                            "proposal": None,
                        }
                    ),
                }
            ],
        }
