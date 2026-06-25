from __future__ import annotations

from pathlib import Path

from .. import coordinates
from ..artifacts import attempt_dir, relative_path, save_attempt_json, save_state
from ..config import get_config
from ..llm_conversation import prompt_cache_key, response_id, save_conversation_entry
from ..llm_client import ChartLLMClient
from ..logging_utils import emit_event
from ..models import (
    AttemptRecord,
    AxisIdentificationOutput,
    AxisCalibrationConversationResponse,
    AxisCalibrationOutput,
    CalibratedAxis,
    CalibrationPoint,
    ChartValue,
    IdentifiedAxis,
    NormPoint,
    RunState,
)
from ..openai_schema import strict_json_schema
from ..overlay import render_calibration_overlay
from ..prompts import PromptPack
from .crop import _scrub_request


def run_axis_identification_stage(state: RunState, prompt_pack: PromptPack, client: ChartLLMClient) -> RunState:
    if not state.crop or not state.crop.image:
        raise ValueError("approved crop is required before axis identification")
    cfg = get_config()
    root = cfg.runs_dir / state.run_id
    crop_path = root / state.crop.image.path
    stage = "axis_identification"
    system_prompt = prompt_pack.render("axis_identification.system")
    user_prompt = prompt_pack.render("axis_identification.initial")
    cache_key = prompt_cache_key(state.run_id, stage)
    request_path = None
    response_path = None
    parsed_path = None
    emit_event(root, "STAGE", "Starting axis identification", run_id=state.run_id, stage=stage)
    try:
        if state.settings.mock_mode:
            output = _mock_axis_identification()
            request = {
                "mock": True,
                "system": system_prompt,
                "prompt": user_prompt,
                "source_image_path": relative_path(crop_path, root),
                "prompt_cache_key": cache_key,
            }
            response = {
                "mock": True,
                "json": output.model_dump(mode="json"),
                "response_id": "mock-axis-identification-01",
            }
        else:
            request, response = client.call_structured(
                settings=state.settings,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                image_path=crop_path,
                schema_name="AxisIdentification",
                schema=strict_json_schema(AxisIdentificationOutput),
                prompt_cache_key=cache_key,
            )
            output = AxisIdentificationOutput.model_validate(response["json"])
        output = _normalize_axis_identification(output)
        request_artifact = _scrub_request(request)
        request_artifact["source_image_path"] = relative_path(crop_path, root)
        request_artifact["prompt_cache_key"] = cache_key
        request_path = save_attempt_json(state.run_id, stage, 1, "request.json", request_artifact)
        response_path = save_attempt_json(state.run_id, stage, 1, "response.json", response)
        parsed_path = save_attempt_json(state.run_id, stage, 1, "parsed.json", output.model_dump(mode="json"))
        state.calibration.identified_axes = output.axes
        state.calibration.calibrated_axes = [
            CalibratedAxis(
                axis_id=axis.axis_id,
                direction=axis.direction,
                name=axis.name,
                unit=axis.unit,
                quantity=axis.quantity,
                location_description=axis.location_description,
            )
            for axis in output.axes
            if axis.is_primary_for_digitization
        ]
        state.calibration.x_points = []
        state.calibration.y_points = []
        state.calibration.approved_x = False
        state.calibration.approved_y = False
        state.calibration.warnings.extend(output.warnings + output.unsupported_flags)
        state.attempts.append(
            AttemptRecord(
                id="axis-identification-01",
                stage=stage,
                attempt_number=1,
                status="accepted",
                request_path=request_path,
                response_path=response_path,
                parsed_path=parsed_path,
                validation_status="valid",
                confidence=_average_axis_confidence(output.axes),
                warnings=output.warnings + output.unsupported_flags,
            )
        )
        save_state(state)
        emit_event(root, "STAGE", f"Identified {len(output.axes)} axes; {len(state.calibration.calibrated_axes)} selected for calibration", run_id=state.run_id, stage=stage)
        return state
    except Exception as exc:  # noqa: BLE001
        response_path = save_attempt_json(state.run_id, stage, 1, "error.json", {"error": str(exc)})
        state.attempts.append(
            AttemptRecord(
                id="axis-identification-01",
                stage=stage,
                attempt_number=1,
                status="failed",
                request_path=request_path,
                response_path=response_path,
                parsed_path=parsed_path,
                validation_status="invalid",
                warnings=[str(exc)],
            )
        )
        emit_event(root, "ERROR", f"Axis identification failed: {exc}", run_id=state.run_id, stage=stage, attempt=1)
        save_state(state)
        raise


def run_axis_stage(state: RunState, axis: str | IdentifiedAxis | CalibratedAxis, prompt_pack: PromptPack, client: ChartLLMClient) -> RunState:
    target_axis = _resolve_axis_target(state, axis)
    axis_direction = target_axis.direction
    if not state.crop or not state.crop.image:
        raise ValueError("approved crop is required before calibration")
    cfg = get_config()
    root = cfg.runs_dir / state.run_id
    crop_path = root / state.crop.image.path
    stage = f"axis_{target_axis.axis_id}"
    system_prompt = prompt_pack.render("axis.system")
    target_axis_text = _axis_target_text(target_axis)
    latest: AxisCalibrationOutput | None = None
    previous_response_id: str | None = None
    previous_overlay_path: Path | None = None
    conversation_history: list[dict] = []
    conversation_entries: list[dict] = []
    cache_key = prompt_cache_key(state.run_id, stage)
    state.stage = "x_calibration_review" if axis_direction == "x" else "y_calibration_review"
    if state.active_job and not state.active_step_status:
        state.active_step_status = "Calibrating axis 1 of 1..."
    save_state(state)
    emit_event(root, "STAGE", f"Starting calibration for {target_axis.display_name}", run_id=state.run_id, stage=stage)

    for attempt in range(1, cfg.max_axis_attempts + 1):
        prompt_key = f"axis.{axis_direction}_initial" if attempt == 1 else f"axis.{axis_direction}_confirm"
        user_prompt = prompt_pack.render(prompt_key, target_axis=target_axis_text)
        image_for_model = previous_overlay_path or crop_path
        request_path = None
        response_path = None
        parsed_path = None
        try:
            if state.settings.mock_mode:
                decision = _mock_axis_decision(axis_direction, attempt)
                request = {
                    "mock": True,
                    "system": system_prompt,
                    "prompt": user_prompt,
                    "source_image_path": relative_path(image_for_model, root),
                    "previous_response_id": previous_response_id,
                    "prompt_cache_key": cache_key,
                }
                response = {
                    "mock": True,
                    "json": decision.model_dump(mode="json"),
                    "response_id": f"mock-{stage}-{attempt:02d}",
                }
            else:
                request, response = client.call_structured(
                    settings=state.settings,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    image_path=image_for_model,
                    schema_name=f"{axis_direction.upper()}AxisCalibrationDecision",
                    schema=strict_json_schema(AxisCalibrationConversationResponse),
                    previous_response_id=previous_response_id,
                    prompt_cache_key=cache_key,
                    conversation_history=conversation_history,
                )
                decision = AxisCalibrationConversationResponse.model_validate(response["json"])
            _validate_axis_decision(decision, attempt, axis_direction)
            output = latest if decision.response_kind == "accept_previous" else decision.proposal
            if output is None:
                raise ValueError(f"{axis_direction}-axis response did not provide a usable proposal")
            if output.axis != axis_direction:
                raise ValueError(f"model returned {output.axis}-axis calibration during {axis_direction}-axis stage")
            output.points = coordinates.populate_calibration_pixels(output.points, state.crop.image.width, state.crop.image.height)
            coordinates.validate_axis_geometry(output.points, axis_direction)
            latest = output
            request_artifact = _scrub_request(request)
            request_artifact["source_image_path"] = relative_path(image_for_model, root)
            request_artifact["previous_response_id"] = previous_response_id
            request_artifact["prompt_cache_key"] = cache_key
            request_artifact["target_axis"] = target_axis_text
            request_path = save_attempt_json(state.run_id, stage, attempt, "request.json", request_artifact)
            response_path = save_attempt_json(state.run_id, stage, attempt, "response.json", response)
            parsed_path = save_attempt_json(state.run_id, stage, attempt, "parsed.json", decision.model_dump(mode="json"))
            overlay_path = attempt_dir(state.run_id, stage, attempt) / "overlay.png"
            render_calibration_overlay(crop_path, output.points, overlay_path)
            overlay_rel = relative_path(overlay_path, root)
            emit_event(root, "ARTIFACT", f"Rendered calibration overlay for {target_axis.display_name}", run_id=state.run_id, stage=stage, attempt=attempt, artifact_path=overlay_rel)
            current_response_id = response_id(response)
            conversation_path = save_conversation_entry(
                root=root,
                run_id=state.run_id,
                stage=stage,
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
                },
            )
            warnings = output.warnings + output.unsupported_flags
            if decision.revision_reason:
                warnings = [*warnings, decision.revision_reason]
            is_accepted = decision.response_kind == "accept_previous"
            is_final_attempt = attempt == cfg.max_axis_attempts
            if is_final_attempt and not is_accepted:
                warnings.append(f"Accepted latest valid calibration for {target_axis.display_name} after reaching the confirmation attempt limit")
            state.attempts.append(
                AttemptRecord(
                    id=f"{stage}-{attempt:02d}",
                    stage=stage,
                    attempt_number=attempt,
                    status="accepted" if is_accepted or is_final_attempt else "needs_review",
                    request_path=request_path,
                    response_path=response_path,
                    parsed_path=parsed_path,
                    overlay_path=overlay_rel,
                    validation_status="valid",
                    confidence=output.confidence,
                    warnings=warnings,
                )
            )
            _upsert_calibrated_axis(
                state,
                target_axis.model_copy(update={"points": output.points, "approved": False, "confidence": output.confidence, "warnings": warnings}),
            )
            _sync_legacy_calibration_fields(state)
            save_state(state)
            emit_event(root, "ARTIFACT", f"Updated calibration conversation artifact for {target_axis.display_name}", run_id=state.run_id, stage=stage, attempt=attempt, artifact_path=conversation_path)
            if is_accepted or is_final_attempt:
                emit_event(root, "STAGE", f"Calibration ready for user review: {target_axis.display_name}", run_id=state.run_id, stage=stage)
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
            previous_overlay_path = overlay_path
            emit_event(root, "STAGE", f"Sending calibration overlay back for model review: {target_axis.display_name}", run_id=state.run_id, stage=stage, attempt=attempt, artifact_path=overlay_rel)
        except Exception as exc:  # noqa: BLE001
            response_path = save_attempt_json(state.run_id, stage, attempt, "error.json", {"error": str(exc)})
            state.attempts.append(
                AttemptRecord(
                    id=f"{stage}-{attempt:02d}",
                    stage=stage,
                    attempt_number=attempt,
                    status="failed",
                    request_path=request_path,
                    response_path=response_path,
                    parsed_path=parsed_path,
                    validation_status="invalid",
                    warnings=[str(exc)],
                )
            )
            emit_event(root, "ERROR", f"Calibration attempt failed for {target_axis.display_name}: {exc}", run_id=state.run_id, stage=stage, attempt=attempt)
            save_state(state)

    if latest is None:
        raise RuntimeError(f"{target_axis.display_name} calibration failed without a valid proposal")
    return state


def approve_axis(state: RunState, axis: str, points: list[CalibrationPoint]) -> RunState:
    if not state.crop or not state.crop.image:
        raise ValueError("approved crop is required before calibration approval")
    cfg = get_config()
    root = cfg.runs_dir / state.run_id
    target_axis = _resolve_axis_target(state, axis)
    points = coordinates.populate_calibration_pixels(points, state.crop.image.width, state.crop.image.height)
    coordinates.validate_axis_geometry(points, target_axis.direction)
    _upsert_calibrated_axis(state, target_axis.model_copy(update={"points": points, "approved": True}))
    _sync_legacy_calibration_fields(state)
    state.pending_series = []
    if state.calibration.all_usable_axes_approved():
        state.stage = "series_ready"
    save_state(state)
    emit_event(root, "USER", f"User approved calibration for {target_axis.display_name}", run_id=state.run_id, stage=f"axis_{target_axis.axis_id}")
    return state


def approve_all_axes(state: RunState, axes: list[CalibratedAxis]) -> RunState:
    if not state.crop or not state.crop.image:
        raise ValueError("approved crop is required before calibration approval")
    cfg = get_config()
    root = cfg.runs_dir / state.run_id
    if not axes:
        raise ValueError("at least one calibrated axis is required")
    for axis in axes:
        points = coordinates.populate_calibration_pixels(axis.points, state.crop.image.width, state.crop.image.height)
        coordinates.validate_axis_geometry(points, axis.direction)
        _upsert_calibrated_axis(state, axis.model_copy(update={"points": points, "approved": True}))
    _sync_legacy_calibration_fields(state)
    state.pending_series = []
    if not state.calibration.has_approved_direction("x") or not state.calibration.has_approved_direction("y"):
        raise ValueError("approved calibration requires at least one x-axis and one y-axis")
    state.stage = "series_ready"
    save_state(state)
    emit_event(root, "USER", "User approved axis calibration", run_id=state.run_id, stage="axis_calibration")
    return state


def _mock_axis_decision(axis: str, attempt: int) -> AxisCalibrationConversationResponse:
    if attempt > 1:
        return AxisCalibrationConversationResponse(response_kind="accept_previous", revision_reason=None, proposal=None)
    return AxisCalibrationConversationResponse(response_kind="proposal", revision_reason="Initial mock axis proposal.", proposal=_mock_axis(axis))


def _mock_axis(axis: str) -> AxisCalibrationOutput:
    if axis == "x":
        return AxisCalibrationOutput(
            axis="x",
            points=[
                CalibrationPoint(
                    label="x1",
                    crop_image_norm=NormPoint(x=140, y=835),
                    chart_value=ChartValue(value_raw="0", value_type="number", parsed_value=0),
                ),
                CalibrationPoint(
                    label="x2",
                    crop_image_norm=NormPoint(x=880, y=835),
                    chart_value=ChartValue(value_raw="100", value_type="number", parsed_value=100),
                ),
            ],
            confidence=0.78,
        )
    return AxisCalibrationOutput(
        axis="y",
        points=[
            CalibrationPoint(
                label="y1",
                crop_image_norm=NormPoint(x=140, y=835),
                chart_value=ChartValue(value_raw="0", value_type="number", parsed_value=0),
            ),
            CalibrationPoint(
                label="y2",
                crop_image_norm=NormPoint(x=140, y=150),
                chart_value=ChartValue(value_raw="100", value_type="number", parsed_value=100),
            ),
        ],
        confidence=0.78,
    )


def _validate_axis_decision(decision: AxisCalibrationConversationResponse, attempt: int, axis: str) -> None:
    if attempt == 1 and decision.response_kind != "proposal":
        raise ValueError(f"first {axis}-axis attempt must return response_kind='proposal'")
    if attempt > 1 and decision.response_kind not in {"accept_previous", "revise_previous"}:
        raise ValueError(f"{axis}-axis review attempts must return accept_previous or revise_previous")


def _mock_axis_identification() -> AxisIdentificationOutput:
    return AxisIdentificationOutput(
        axes=[
            IdentifiedAxis(
                axis_id="x_flow",
                direction="x",
                name="Flow",
                unit="L/min",
                quantity="flow",
                location_description="Bottom horizontal flow axis closest to the chart body.",
                is_primary_for_digitization=True,
                confidence=0.8,
            ),
            IdentifiedAxis(
                axis_id="y_head",
                direction="y",
                name="Head",
                unit="m",
                quantity="head",
                location_description="Left vertical head axis closest to the chart body.",
                is_primary_for_digitization=True,
                confidence=0.8,
            ),
        ]
    )


def _normalize_axis_identification(output: AxisIdentificationOutput) -> AxisIdentificationOutput:
    seen: set[str] = set()
    normalized: list[IdentifiedAxis] = []
    for index, axis in enumerate(output.axes, start=1):
        axis_id = _safe_axis_id(axis.axis_id or f"{axis.direction}_{index}")
        base = axis_id
        suffix = 2
        while axis_id in seen:
            axis_id = f"{base}_{suffix}"
            suffix += 1
        seen.add(axis_id)
        normalized.append(axis.model_copy(update={"axis_id": axis_id}))
    return output.model_copy(update={"axes": normalized})


def _safe_axis_id(value: str) -> str:
    safe = "".join(char.lower() if char.isalnum() else "_" for char in value.strip())
    safe = "_".join(part for part in safe.split("_") if part)
    return safe or "axis"


def _resolve_axis_target(state: RunState, axis: str | IdentifiedAxis | CalibratedAxis) -> CalibratedAxis:
    if isinstance(axis, CalibratedAxis):
        return axis
    if isinstance(axis, IdentifiedAxis):
        return CalibratedAxis(
            axis_id=axis.axis_id,
            direction=axis.direction,
            name=axis.name,
            unit=axis.unit,
            quantity=axis.quantity,
            location_description=axis.location_description,
        )
    if axis not in {"x", "y"}:
        existing = state.calibration.axis_by_id(axis)
        if existing:
            return existing
        identified = next((item for item in state.calibration.identified_axes if item.axis_id == axis), None)
        if identified:
            return _resolve_axis_target(state, identified)
        raise ValueError(f"unknown axis_id: {axis}")
    existing_direction = next((item for item in state.calibration.usable_axes() if item.direction == axis), None)
    if existing_direction:
        return existing_direction
    return CalibratedAxis(
        axis_id=axis,
        direction=axis,  # type: ignore[arg-type]
        name=f"{axis.upper()} axis",
        location_description=f"Default {axis}-axis calibration",
    )


def _upsert_calibrated_axis(state: RunState, axis: CalibratedAxis) -> None:
    for index, existing in enumerate(state.calibration.calibrated_axes):
        if existing.axis_id == axis.axis_id:
            state.calibration.calibrated_axes[index] = axis
            return
    state.calibration.calibrated_axes.append(axis)


def _sync_legacy_calibration_fields(state: RunState) -> None:
    first_x = next((axis for axis in state.calibration.calibrated_axes if axis.direction == "x"), None)
    first_y = next((axis for axis in state.calibration.calibrated_axes if axis.direction == "y"), None)
    state.calibration.x_points = first_x.points if first_x else []
    state.calibration.y_points = first_y.points if first_y else []
    state.calibration.approved_x = bool(first_x and first_x.approved)
    state.calibration.approved_y = bool(first_y and first_y.approved)


def _axis_target_text(axis: CalibratedAxis) -> str:
    return "\n".join(
        [
            f"Axis ID: {axis.axis_id}",
            f"Direction: {axis.direction}",
            f"Name: {axis.name}",
            f"Unit: {axis.unit or 'unknown'}",
            f"Quantity: {axis.quantity or 'unknown'}",
            f"Location: {axis.location_description}",
        ]
    )


def _average_axis_confidence(axes: list[IdentifiedAxis]) -> float | None:
    values = [axis.confidence for axis in axes if axis.confidence is not None]
    if not values:
        return None
    return sum(values) / len(values)
