from __future__ import annotations

from pathlib import Path

from .. import coordinates
from ..artifacts import attempt_dir, relative_path, save_attempt_json, save_state
from ..config import get_config
from ..image_io import crop_image
from ..llm_conversation import prompt_cache_key, response_id, save_conversation_entry
from ..llm_client import ChartLLMClient
from ..logging_utils import emit_event
from ..models import AttemptRecord, CropConversationResponse, CropProposal, CropState, NormBBox, PixelBBox, RunState
from ..openai_schema import strict_json_schema
from ..overlay import render_crop_overlay
from ..prompts import PromptPack


def run_crop_stage(state: RunState, prompt_pack: PromptPack, client: ChartLLMClient) -> RunState:
    if not state.canonical_image:
        raise ValueError("run does not have a selected canonical image")
    cfg = get_config()
    root = cfg.runs_dir / state.run_id
    image_path = root / state.canonical_image.path
    system_prompt = prompt_pack.render("crop.system")
    latest: CropProposal | None = None
    previous_response_id: str | None = None
    previous_tool_call_id: str | None = None
    previous_tool_output: dict | None = None
    previous_overlay_path: Path | None = None
    conversation_history: list[dict] = []
    conversation_entries: list[dict] = []
    cache_key = prompt_cache_key(state.run_id, "crop")

    state.stage = "crop_ready"
    save_state(state)
    emit_event(root, "STAGE", "Starting crop-to-chart loop", run_id=state.run_id, stage="crop")

    for attempt in range(1, cfg.max_crop_attempts + 1):
        user_prompt = prompt_pack.render("crop.initial" if attempt == 1 else "crop.confirm")
        image_for_model = previous_overlay_path or image_path
        request_path = None
        response_path = None
        parsed_path = None
        try:
            if state.settings.mock_mode:
                decision = _mock_crop_decision(attempt)
                request = {
                    "mock": True,
                    "system": system_prompt,
                    "prompt": user_prompt,
                    "source_image_path": relative_path(image_for_model, root),
                    "previous_response_id": previous_response_id,
                    "previous_tool_call_id": previous_tool_call_id,
                    "previous_tool_output": previous_tool_output,
                    "prompt_cache_key": cache_key,
                }
                response = {
                    "mock": True,
                    "arguments": decision.model_dump(mode="json"),
                    "response_id": f"mock-crop-{attempt:02d}",
                    "function_call_id": f"mock-crop-call-{attempt:02d}",
                }
            else:
                request, response = client.call_crop_tool(
                    settings=state.settings,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    image_path=image_for_model,
                    schema=strict_json_schema(CropConversationResponse),
                    previous_response_id=previous_response_id,
                    previous_tool_call_id=previous_tool_call_id,
                    previous_tool_output=previous_tool_output,
                    prompt_cache_key=cache_key,
                    conversation_history=conversation_history,
                )
                decision = CropConversationResponse.model_validate(response["arguments"])
            _validate_crop_decision(decision, attempt)
            proposal = latest if decision.response_kind == "accept_previous" else decision.proposal
            if proposal is None:
                raise ValueError("crop response did not provide a usable proposal")
            latest = proposal
            request_artifact = _scrub_request(request)
            request_artifact["source_image_path"] = relative_path(image_for_model, root)
            request_artifact["previous_response_id"] = previous_response_id
            request_artifact["previous_tool_call_id"] = previous_tool_call_id
            request_artifact["previous_tool_output"] = previous_tool_output
            request_artifact["prompt_cache_key"] = cache_key
            request_path = save_attempt_json(state.run_id, "crop", attempt, "request.json", request_artifact)
            response_path = save_attempt_json(state.run_id, "crop", attempt, "response.json", response)
            parsed_path = save_attempt_json(state.run_id, "crop", attempt, "parsed.json", decision.model_dump(mode="json"))
            bbox_px = coordinates.norm_to_px_bbox(proposal.bbox, state.canonical_image.width, state.canonical_image.height)
            overlay_path = attempt_dir(state.run_id, "crop", attempt) / "overlay.png"
            render_crop_overlay(image_path, bbox_px, overlay_path)
            overlay_rel = relative_path(overlay_path, root)
            emit_event(root, "ARTIFACT", "Rendered crop overlay", run_id=state.run_id, stage="crop", attempt=attempt, artifact_path=overlay_rel)
            current_response_id = response_id(response)
            current_tool_call_id = response.get("function_call_id")
            current_tool_output = {
                "status": "overlay_rendered",
                "overlay_path": overlay_rel,
                "bbox_full_norm": proposal.bbox.model_dump(mode="json"),
                "message": "The crop overlay was rendered successfully and is attached in the next user message for review.",
            }
            conversation_path = save_conversation_entry(
                root=root,
                run_id=state.run_id,
                stage="crop",
                entries=conversation_entries,
                entry={
                    "attempt": attempt,
                    "response_kind": decision.response_kind,
                    "revision_reason": decision.revision_reason,
                    "source_image_path": relative_path(image_for_model, root),
                    "overlay_path": overlay_rel,
                    "request_path": request_path,
                    "response_path": response_path,
                    "parsed_path": parsed_path,
                    "previous_response_id": previous_response_id,
                    "response_id": current_response_id,
                    "function_call_id": current_tool_call_id,
                    "function_call_output": current_tool_output,
                },
            )
            warnings = proposal.warnings + proposal.unsupported_flags
            if decision.revision_reason:
                warnings = [*warnings, decision.revision_reason]
            is_accepted = decision.response_kind == "accept_previous"
            is_final_attempt = attempt == cfg.max_crop_attempts
            if is_final_attempt and not is_accepted:
                warnings.append("Accepted latest valid crop after reaching the confirmation attempt limit")
            state.attempts.append(
                AttemptRecord(
                    id=f"crop-{attempt:02d}",
                    stage="crop",
                    attempt_number=attempt,
                    status="accepted" if is_accepted or is_final_attempt else "needs_review",
                    request_path=request_path,
                    response_path=response_path,
                    parsed_path=parsed_path,
                    overlay_path=overlay_rel,
                    validation_status="valid",
                    confidence=proposal.confidence,
                    warnings=warnings,
                )
            )
            save_state(state)
            emit_event(root, "ARTIFACT", "Updated crop conversation artifact", run_id=state.run_id, stage="crop", attempt=attempt, artifact_path=conversation_path)
            if is_accepted or is_final_attempt:
                state.crop = CropState(bbox_full_norm=proposal.bbox, bbox_full_px=bbox_px, approved=False, warnings=proposal.warnings)
                state.stage = "crop_review"
                save_state(state)
                emit_event(root, "STAGE", "Crop ready for user review", run_id=state.run_id, stage="crop")
                return state
            if response.get("text"):
                conversation_history.append(
                    {
                        "user_prompt": user_prompt,
                        "image_path": str(image_for_model),
                        "model_text": response["text"],
                    }
                )
            previous_response_id = current_response_id
            previous_tool_call_id = current_tool_call_id
            previous_tool_output = current_tool_output
            previous_overlay_path = overlay_path
            emit_event(root, "STAGE", "Sending crop overlay back for model review", run_id=state.run_id, stage="crop", attempt=attempt, artifact_path=overlay_rel)
        except Exception as exc:  # noqa: BLE001
            response_path = save_attempt_json(state.run_id, "crop", attempt, "error.json", {"error": str(exc)})
            state.attempts.append(
                AttemptRecord(
                    id=f"crop-{attempt:02d}",
                    stage="crop",
                    attempt_number=attempt,
                    status="failed",
                    request_path=request_path,
                    response_path=response_path,
                    parsed_path=parsed_path,
                    validation_status="invalid",
                    warnings=[str(exc)],
                )
            )
            emit_event(root, "ERROR", f"Crop attempt failed: {exc}", run_id=state.run_id, stage="crop", attempt=attempt)
            save_state(state)

    if latest is None:
        raise RuntimeError("crop failed without a valid proposal")
    return state


def approve_crop(state: RunState, bbox: NormBBox) -> RunState:
    if not state.canonical_image:
        raise ValueError("run does not have a canonical image")
    cfg = get_config()
    root = cfg.runs_dir / state.run_id
    full_image_path = root / state.canonical_image.path
    bbox_px = _bbox_px_for_approved_crop(bbox, state.canonical_image.width, state.canonical_image.height)
    crop_path = root / "crop" / "approved_crop.png"
    crop_asset = crop_image(full_image_path, bbox_px, crop_path)
    crop_asset.path = relative_path(crop_path, root)
    state.crop = CropState(bbox_full_norm=bbox, bbox_full_px=bbox_px, image=crop_asset, approved=True)
    from ..models import CalibrationState

    state.calibration = CalibrationState()
    state.pending_series = []
    state.series = []
    state.stage = "calibration_ready"
    save_state(state)
    emit_event(root, "USER", "User approved crop", run_id=state.run_id, stage="crop", artifact_path=crop_asset.path)
    return state


def _bbox_px_for_approved_crop(bbox: NormBBox, width: int, height: int) -> PixelBBox:
    if bbox.left == 0 and bbox.top == 0 and bbox.right == 999 and bbox.bottom == 999:
        return PixelBBox(left=0, top=0, right=width, bottom=height)
    return coordinates.norm_to_px_bbox(bbox, width, height)


def _mock_crop_decision(attempt: int) -> CropConversationResponse:
    if attempt > 1:
        return CropConversationResponse(response_kind="accept_previous", revision_reason=None, proposal=None)
    return CropConversationResponse(
        response_kind="proposal",
        revision_reason="Initial mock crop proposal.",
        proposal=CropProposal(
            bbox=NormBBox(left=45, top=60, right=955, bottom=930),
            confidence=0.82,
            warnings=[],
            unsupported_flags=[],
        ),
    )


def _validate_crop_decision(decision: CropConversationResponse, attempt: int) -> None:
    if attempt == 1 and decision.response_kind != "proposal":
        raise ValueError("first crop attempt must return response_kind='proposal'")
    if attempt > 1 and decision.response_kind not in {"accept_previous", "revise_previous"}:
        raise ValueError("crop review attempts must return accept_previous or revise_previous")


def _scrub_request(request: dict) -> dict:
    scrubbed = dict(request)
    # Base64 images are huge; keep prompt/settings but replace image payloads with placeholders.
    for item in scrubbed.get("input", []):
        for content in item.get("content", []):
            if content.get("type") == "input_image":
                content["image_url"] = "<data-url omitted; see attempt image artifacts>"
    return scrubbed
