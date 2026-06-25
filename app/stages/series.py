from __future__ import annotations

import uuid

from .. import coordinates
from ..artifacts import attempt_dir, relative_path, save_attempt_json, save_state
from ..color_utils import normalize_color
from ..config import get_config
from ..llm_conversation import prompt_cache_key, response_id, save_conversation_entry
from ..llm_client import ChartLLMClient
from ..logging_utils import emit_event
from ..models import (
    AttemptRecord,
    RunState,
    SeriesDigitizationConversationResponse,
    SeriesDigitizationOutput,
    SeriesIdentification,
    SeriesIdentificationOutput,
    SeriesPoint,
    SeriesPointProposal,
    SeriesState,
)
from ..openai_schema import strict_json_schema
from ..overlay import COLORS, render_series_overlay
from ..prompts import PromptPack
from ..series_point_limits import series_data_point_limits
from .crop import _scrub_request


def run_series_stage(state: RunState, prompt_pack: PromptPack, client: ChartLLMClient) -> RunState:
    return run_series_identification_stage(state, prompt_pack, client)


def run_series_identification_stage(state: RunState, prompt_pack: PromptPack, client: ChartLLMClient) -> RunState:
    if not state.crop or not state.crop.image:
        raise ValueError("approved crop is required before series extraction")
    if not state.calibration.has_approved_direction("x") or not state.calibration.has_approved_direction("y"):
        raise ValueError("approved x and y calibration are required before series extraction")
    cfg = get_config()
    root = cfg.runs_dir / state.run_id
    crop_path = root / state.crop.image.path
    existing_auto_count = sum(1 for series in state.series if series.source == "llm")
    if existing_auto_count:
        state.series = [series for series in state.series if series.source != "llm"]
        emit_event(root, "USER", f"Cleared {existing_auto_count} previous auto-digitised series before rerun", run_id=state.run_id, stage="series")
    state.pending_series = []
    state.stage = "series_ready"
    state.active_step_status = "Identifying series..."
    save_state(state)

    identification = _identify_all_series(state, prompt_pack, client, root, crop_path)
    state.warnings.extend(identification.warnings + identification.unsupported_flags)
    state.pending_series = identification.series

    if not identification.series:
        emit_event(root, "WARN", "No line series were identified", run_id=state.run_id, stage="series")
    else:
        emit_event(root, "STAGE", "Waiting for user series selection", run_id=state.run_id, stage="series")
    state.stage = "series_ready"
    save_state(state)
    return state


def run_selected_series_stage(state: RunState, prompt_pack: PromptPack, client: ChartLLMClient, selected_indexes: list[int]) -> RunState:
    if not state.crop or not state.crop.image:
        raise ValueError("approved crop is required before series extraction")
    if not state.calibration.has_approved_direction("x") or not state.calibration.has_approved_direction("y"):
        raise ValueError("approved x and y calibration are required before series extraction")
    cfg = get_config()
    root = cfg.runs_dir / state.run_id
    crop_path = root / state.crop.image.path
    candidates = list(state.pending_series)
    if not candidates:
        state.stage = "series_ready" if not state.series else "series_review"
        save_state(state)
        emit_event(root, "WARN", "No pending series selection was available", run_id=state.run_id, stage="series")
        return state

    selected: list[SeriesIdentification] = []
    for index in selected_indexes:
        if 0 <= index < len(candidates):
            selected.append(candidates[index])

    if not selected:
        state.pending_series = []
        state.stage = "series_ready" if not state.series else "series_review"
        save_state(state)
        emit_event(root, "USER", "User cancelled series digitisation selection", run_id=state.run_id, stage="series")
        return state

    state.stage = "series_review"
    save_state(state)
    for series_index, series_description in enumerate(selected, start=1):
        state.active_step_status = f"Digitising series {series_index} of {len(selected)}..."
        save_state(state)
        _digitize_identified_series(
            state=state,
            prompt_pack=prompt_pack,
            client=client,
            root=root,
            crop_path=crop_path,
            series_description=series_description,
            series_index=series_index,
            series_total=len(selected),
        )

    state.pending_series = []
    state.stage = "series_review" if state.series else "series_ready"
    save_state(state)
    return state


def cancel_series_selection(state: RunState) -> RunState:
    cfg = get_config()
    root = cfg.runs_dir / state.run_id
    state.pending_series = []
    state.stage = "series_ready" if not state.series else "series_review"
    save_state(state)
    emit_event(root, "USER", "User cancelled series digitisation selection", run_id=state.run_id, stage="series")
    return state


def _identify_all_series(
    state: RunState,
    prompt_pack: PromptPack,
    client: ChartLLMClient,
    root,
    crop_path,
) -> SeriesIdentificationOutput:
    system_prompt = prompt_pack.render("series.identification_system")
    user_prompt = prompt_pack.render("series.identification_initial", available_axes=_available_axes_text(state))
    cache_key = prompt_cache_key(state.run_id, "series_identification")
    request_path = None
    response_path = None
    parsed_path = None
    try:
        if state.settings.mock_mode:
            output = _mock_series_identification()
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
                "response_id": "mock-series-identification-01",
            }
        else:
            request, response = client.call_structured(
                settings=state.settings,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                image_path=crop_path,
                schema_name="SeriesIdentification",
                schema=strict_json_schema(SeriesIdentificationOutput),
                prompt_cache_key=cache_key,
            )
            output = SeriesIdentificationOutput.model_validate(response["json"])
        request_artifact = _scrub_request(request)
        request_artifact["source_image_path"] = relative_path(crop_path, root)
        request_artifact["prompt_cache_key"] = cache_key
        request_artifact["available_axes"] = _available_axes_text(state)
        request_path = save_attempt_json(state.run_id, "series_identification", 1, "request.json", request_artifact)
        response_path = save_attempt_json(state.run_id, "series_identification", 1, "response.json", response)
        parsed_path = save_attempt_json(state.run_id, "series_identification", 1, "parsed.json", output.model_dump(mode="json"))
        current_response_id = response_id(response)
        conversation_path = save_conversation_entry(
            root=root,
            run_id=state.run_id,
            stage="series_identification",
            entries=[],
            entry={
                "attempt": 1,
                "series_count": len(output.series),
                "source_image_path": relative_path(crop_path, root),
                "request_path": request_path,
                "response_path": response_path,
                "parsed_path": parsed_path,
                "prompt_cache_key": cache_key,
                "response_id": current_response_id,
            },
        )
        state.attempts.append(
            AttemptRecord(
                id="series-identification-01",
                stage="series_identification",
                attempt_number=1,
                status="accepted",
                request_path=request_path,
                response_path=response_path,
                parsed_path=parsed_path,
                validation_status="valid",
                confidence=_average_confidence(output.series),
                warnings=output.warnings + output.unsupported_flags,
            )
        )
        save_state(state)
        emit_event(root, "ARTIFACT", "Updated series identification conversation artifact", run_id=state.run_id, stage="series", attempt=1, artifact_path=conversation_path)
        output = _validate_identified_series_axes(output, state)
        emit_event(root, "STAGE", f"Identified {len(output.series)} line series", run_id=state.run_id, stage="series")
        return output
    except Exception as exc:  # noqa: BLE001
        response_path = save_attempt_json(state.run_id, "series_identification", 1, "error.json", {"error": str(exc)})
        state.attempts.append(
            AttemptRecord(
                id="series-identification-01",
                stage="series_identification",
                attempt_number=1,
                status="failed",
                request_path=request_path,
                response_path=response_path,
                parsed_path=parsed_path,
                validation_status="invalid",
                warnings=[str(exc)],
            )
        )
        emit_event(root, "ERROR", f"Series identification failed: {exc}", run_id=state.run_id, stage="series", attempt=1)
        save_state(state)
        raise


def _digitize_identified_series(
    *,
    state: RunState,
    prompt_pack: PromptPack,
    client: ChartLLMClient,
    root,
    crop_path,
    series_description: SeriesIdentification,
    series_index: int,
    series_total: int,
    series_id: str | None = None,
    append_to_state: bool = True,
) -> SeriesState:
    cfg = get_config()
    system_prompt = prompt_pack.render("series.digitization_system")
    target_series = _series_description_text(series_description, series_index, series_total)
    latest: SeriesDigitizationOutput | None = None
    latest_series_state: SeriesState | None = None
    previous_response_id: str | None = None
    previous_overlay_path = None
    conversation_history: list[dict] = []
    conversation_entries: list[dict] = []
    series_id = series_id or uuid.uuid4().hex[:8]
    cache_key = prompt_cache_key(state.run_id, "series", series_id)
    completed = False
    emit_event(root, "STAGE", f"Starting digitisation for {series_description.series_name}", run_id=state.run_id, stage="series")

    for attempt in range(1, cfg.max_series_attempts + 1):
        prompt_key = "series.digitization_initial" if attempt == 1 else "series.digitization_confirm"
        user_prompt = prompt_pack.render(prompt_key, target_series=target_series)
        image_for_model = previous_overlay_path or crop_path
        request_path = None
        response_path = None
        parsed_path = None
        try:
            if state.settings.mock_mode:
                decision = _mock_series_digitization_decision(attempt)
                request = {
                    "mock": True,
                    "system": system_prompt,
                    "prompt": user_prompt,
                    "target_series": target_series,
                    "source_image_path": relative_path(image_for_model, root),
                    "previous_response_id": previous_response_id,
                    "prompt_cache_key": cache_key,
                }
                response = {
                    "mock": True,
                    "json": decision.model_dump(mode="json"),
                    "response_id": f"mock-series-{series_id}-{attempt:02d}",
                }
            else:
                request, response = client.call_structured(
                    settings=state.settings,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    image_path=image_for_model,
                    schema_name="SeriesDigitizationDecision",
                    schema=strict_json_schema(SeriesDigitizationConversationResponse),
                    previous_response_id=previous_response_id,
                    prompt_cache_key=cache_key,
                    conversation_history=conversation_history,
                )
                decision = SeriesDigitizationConversationResponse.model_validate(response["json"])
            _validate_series_decision(decision, attempt)
            request_artifact = _scrub_request(request)
            request_artifact["source_image_path"] = relative_path(image_for_model, root)
            request_artifact["previous_response_id"] = previous_response_id
            request_artifact["prompt_cache_key"] = cache_key
            request_artifact["target_series"] = target_series
            request_path = save_attempt_json(state.run_id, "series", attempt, "request.json", request_artifact, series_id=series_id)
            response_path = save_attempt_json(state.run_id, "series", attempt, "response.json", response, series_id=series_id)
            parsed_path = save_attempt_json(state.run_id, "series", attempt, "parsed.json", decision.model_dump(mode="json"), series_id=series_id)
            current_response_id = response_id(response)

            if decision.response_kind == "accept_previous":
                if latest_series_state is None:
                    raise ValueError("series review accepted a previous attempt, but no previous proposal exists")
                output = latest
                series_state = latest_series_state
            else:
                output = decision.proposal
                if output is None:
                    raise ValueError("series response did not provide a usable proposal")
                latest = output
                series_state = _series_output_to_state(output, series_description, series_id, state)
                latest_series_state = series_state
            if output is None:
                raise ValueError("series response did not provide a usable proposal")
            _upsert_series_preview(state, series_state, allow_insert=append_to_state)
            if state.series:
                state.stage = "series_review"
            overlay_path = attempt_dir(state.run_id, "series", attempt, series_id=series_id) / "overlay.png"
            render_series_overlay(crop_path, [series_state], overlay_path)
            overlay_rel = relative_path(overlay_path, root)
            emit_event(root, "ARTIFACT", "Rendered current-series overlay", run_id=state.run_id, stage="series", attempt=attempt, artifact_path=overlay_rel)
            conversation_path = save_conversation_entry(
                root=root,
                run_id=state.run_id,
                stage="series",
                series_id=series_id,
                entries=conversation_entries,
                entry={
                    "attempt": attempt,
                    "response_kind": decision.response_kind,
                    "revision_reason": decision.revision_reason,
                    "target_series": target_series,
                    "source_image_path": relative_path(image_for_model, root),
                    "overlay_path": overlay_rel,
                    "request_path": request_path,
                    "response_path": response_path,
                    "parsed_path": parsed_path,
                    "previous_response_id": previous_response_id,
                    "response_id": current_response_id,
                },
            )
            should_accept = decision.response_kind == "accept_previous"
            is_final_attempt = attempt == cfg.max_series_attempts
            if is_final_attempt and not should_accept:
                series_state.warnings.append("Accepted latest valid series after reaching the confirmation attempt limit")
            warnings = output.warnings + output.unsupported_flags
            if decision.revision_reason:
                warnings = [*warnings, decision.revision_reason]
            state.attempts.append(
                AttemptRecord(
                    id=f"series-{series_id}-{attempt:02d}",
                    stage="series",
                    attempt_number=attempt,
                    status="accepted" if should_accept or is_final_attempt else "needs_review",
                    request_path=request_path,
                    response_path=response_path,
                    parsed_path=parsed_path,
                    overlay_path=overlay_rel,
                    validation_status="valid",
                    confidence=series_state.confidence,
                    warnings=warnings,
                )
            )
            save_state(state)
            emit_event(root, "ARTIFACT", "Updated series conversation artifact", run_id=state.run_id, stage="series", attempt=attempt, artifact_path=conversation_path)
            if should_accept or is_final_attempt:
                completed = True
                if append_to_state:
                    _upsert_series_preview(state, series_state, allow_insert=True)
                    save_state(state)
                    emit_event(root, "STAGE", f"Added series {series_state.name}", run_id=state.run_id, stage="series")
                break
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
            emit_event(root, "STAGE", "Sending current-series overlay back for model confirmation", run_id=state.run_id, stage="series", attempt=attempt, artifact_path=overlay_rel)
        except Exception as exc:  # noqa: BLE001
            response_path = save_attempt_json(state.run_id, "series", attempt, "error.json", {"error": str(exc)}, series_id=series_id)
            state.attempts.append(
                AttemptRecord(
                    id=f"series-{series_id}-{attempt:02d}",
                    stage="series",
                    attempt_number=attempt,
                    status="failed",
                    request_path=request_path,
                    response_path=response_path,
                    parsed_path=parsed_path,
                    validation_status="invalid",
                    warnings=[str(exc)],
                )
            )
            emit_event(root, "ERROR", f"Series attempt failed: {exc}", run_id=state.run_id, stage="series", attempt=attempt)
            save_state(state)
    if latest is None:
        raise RuntimeError(f"series extraction failed without a valid proposal for {series_description.series_name}")
    if not completed and latest_series_state:
        latest_series_state.warnings.append("Accepted latest valid series after confirmation attempts failed")
        _upsert_series_preview(state, latest_series_state, allow_insert=append_to_state)
        save_state(state)
        emit_event(root, "WARN", f"Accepted latest valid series after confirmation errors: {latest_series_state.name}", run_id=state.run_id, stage="series")
    if latest_series_state is None:
        raise RuntimeError(f"series extraction failed without a valid proposal for {series_description.series_name}")
    return latest_series_state


def _upsert_series_preview(state: RunState, series_state: SeriesState, *, allow_insert: bool) -> None:
    for index, existing in enumerate(state.series):
        if existing.id == series_state.id:
            state.series[index] = series_state
            return
    if allow_insert:
        state.series.append(series_state)


def retry_series_digitization(state: RunState, prompt_pack: PromptPack, client: ChartLLMClient, series_id: str) -> RunState:
    if not state.crop or not state.crop.image:
        raise ValueError("approved crop is required before series extraction")
    if not state.calibration.has_approved_direction("x") or not state.calibration.has_approved_direction("y"):
        raise ValueError("approved x and y calibration are required before series extraction")
    target_index = next((index for index, series in enumerate(state.series) if series.id == series_id), None)
    if target_index is None:
        raise ValueError(f"series {series_id!r} does not exist")
    original = state.series[target_index]
    root = get_config().runs_dir / state.run_id
    crop_path = root / state.crop.image.path
    state.stage = "series_review"
    save_state(state)
    replacement = _digitize_identified_series(
        state=state,
        prompt_pack=prompt_pack,
        client=client,
        root=root,
        crop_path=crop_path,
        series_description=SeriesIdentification(
            series_name=original.llm_series_name or original.name,
            visual_description=original.visual_description or f"Retry digitisation for existing series {original.name}.",
            line_color=original.line_color,
            line_style=original.line_style,
            x_axis_id=original.x_axis_id,
            y_axis_id=original.y_axis_id,
            axis_selection_reason=original.axis_selection_reason,
            confidence=original.confidence,
        ),
        series_index=target_index + 1,
        series_total=len(state.series),
        series_id=original.id,
        append_to_state=False,
    )
    state.series[target_index] = replacement
    state.stage = "series_review"
    save_state(state)
    emit_event(root, "STAGE", f"Replaced series {original.name} with retry result {replacement.name}", run_id=state.run_id, stage="series")
    return state


def _series_output_to_state(
    output: SeriesDigitizationOutput,
    series_description: SeriesIdentification,
    series_id: str,
    state: RunState,
) -> SeriesState:
    if not state.crop or not state.crop.image:
        raise ValueError("crop image is required")
    points: list[SeriesPoint] = []
    x_axis_id = series_description.x_axis_id or state.calibration.default_axis_id("x")
    y_axis_id = series_description.y_axis_id or state.calibration.default_axis_id("y")
    x_axis = state.calibration.axis_by_id(x_axis_id)
    y_axis = state.calibration.axis_by_id(y_axis_id)
    if not x_axis or not y_axis:
        raise ValueError(f"series {series_description.series_name!r} refers to unknown axes x={x_axis_id!r}, y={y_axis_id!r}")
    for proposal in output.points:
        crop_image_px = coordinates.chart_space_to_image_point_for_axes(proposal.chart_x, proposal.chart_y, x_axis, y_axis)
        point = SeriesPoint(
            point_index=proposal.point_index,
            segment_index=proposal.segment_index,
            crop_image_norm=coordinates.crop_px_to_crop_norm(crop_image_px, state.crop.image.width, state.crop.image.height, clamp=True),
            crop_image_px=crop_image_px,
            chart_x=proposal.chart_x,
            chart_y=proposal.chart_y,
        )
        points.append(point)
    points.sort(key=lambda item: (item.segment_index, item.point_index))
    return SeriesState(
        id=series_id,
        name=series_description.series_name or f"Series {len(state.series) + 1}",
        llm_series_name=series_description.series_name,
        visual_description=series_description.visual_description,
        line_color=normalize_color(series_description.line_color, COLORS["series"][len(state.series) % len(COLORS["series"])]),
        line_style=series_description.line_style,
        x_axis_id=x_axis_id,
        y_axis_id=y_axis_id,
        axis_selection_reason=series_description.axis_selection_reason,
        confidence=output.confidence if output.confidence is not None else series_description.confidence,
        points=points,
        warnings=output.warnings + output.unsupported_flags,
    )


def _mock_series_identification() -> SeriesIdentificationOutput:
    return SeriesIdentificationOutput(
        series=[
            SeriesIdentification(
                series_name="Mock series A",
                visual_description="A rising blue line used for local pipeline testing.",
                line_color="#0891b2",
                line_style="solid",
                x_axis_id="x_flow",
                y_axis_id="y_head",
                axis_selection_reason="Mock series uses the default flow and head axes.",
                confidence=0.81,
            )
        ]
    )


def _mock_series_digitization_decision(attempt: int) -> SeriesDigitizationConversationResponse:
    if attempt > 1:
        return SeriesDigitizationConversationResponse(response_kind="accept_previous", revision_reason=None, proposal=None)
    min_points, _ = series_data_point_limits()
    points = []
    for index in range(min_points):
        ratio = index / max(1, min_points - 1)
        points.append(
            SeriesPointProposal(
                point_index=index,
                chart_x={"value_raw": f"{(100 + ratio * 900):g}", "value_type": "number"},
                chart_y={"value_raw": f"{(60 - ratio * 30 + (2 if index % 2 else 0)):g}", "value_type": "number"},
            )
        )
    return SeriesDigitizationConversationResponse(
        response_kind="proposal",
        revision_reason="Initial mock series proposal.",
        proposal=SeriesDigitizationOutput(
            confidence=0.81,
            points=points,
        ),
    )


def _validate_series_decision(decision: SeriesDigitizationConversationResponse, attempt: int) -> None:
    if attempt == 1 and decision.response_kind != "proposal":
        raise ValueError("first series digitisation attempt must return proposal")
    if attempt > 1 and decision.response_kind not in {"accept_previous", "revise_previous"}:
        raise ValueError("series review attempts must return accept_previous or revise_previous")


def _series_description_text(series: SeriesIdentification, series_index: int, series_total: int) -> str:
    return "\n".join(
        [
            f"Series {series_index} of {series_total}",
            f"Name: {series.series_name or 'unnamed series'}",
            f"Colour: {series.line_color or 'unknown'}",
            f"Line style: {series.line_style or 'unknown'}",
            f"X axis ID: {series.x_axis_id or 'default x-axis'}",
            f"Y axis ID: {series.y_axis_id or 'default y-axis'}",
            f"Axis selection reason: {series.axis_selection_reason or 'not provided'}",
            f"Visual description: {series.visual_description or 'no visual description provided'}",
            f"Identification confidence: {_confidence_text(series.confidence)}",
        ]
    )


def _confidence_text(confidence: float | None) -> str:
    return "unknown" if confidence is None else f"{confidence:.2f}"


def _average_confidence(series: list[SeriesIdentification]) -> float | None:
    values = [item.confidence for item in series if item.confidence is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _available_axes_text(state: RunState) -> str:
    axes = state.calibration.approved_usable_axes()
    if not axes:
        return "No calibrated axes are available."
    lines: list[str] = []
    for axis in axes:
        lines.append(
            " | ".join(
                [
                    f"axis_id={axis.axis_id}",
                    f"direction={axis.direction}",
                    f"name={axis.name}",
                    f"unit={axis.unit or 'unknown'}",
                    f"quantity={axis.quantity or 'unknown'}",
                    f"location={axis.location_description}",
                ]
            )
        )
    return "\n".join(lines)


def _validate_identified_series_axes(output: SeriesIdentificationOutput, state: RunState) -> SeriesIdentificationOutput:
    x_ids = {axis.axis_id for axis in state.calibration.approved_usable_axes() if axis.direction == "x"}
    y_ids = {axis.axis_id for axis in state.calibration.approved_usable_axes() if axis.direction == "y"}
    default_x = state.calibration.default_axis_id("x")
    default_y = state.calibration.default_axis_id("y")
    normalized: list[SeriesIdentification] = []
    warnings = list(output.warnings)
    for series in output.series:
        x_axis_id = series.x_axis_id or default_x
        y_axis_id = series.y_axis_id or default_y
        if x_axis_id not in x_ids:
            warnings.append(f"Series {series.series_name or 'unnamed'} used invalid x_axis_id {x_axis_id!r}; defaulted to {default_x!r}")
            x_axis_id = default_x
        if y_axis_id not in y_ids:
            warnings.append(f"Series {series.series_name or 'unnamed'} used invalid y_axis_id {y_axis_id!r}; defaulted to {default_y!r}")
            y_axis_id = default_y
        normalized.append(series.model_copy(update={"x_axis_id": x_axis_id, "y_axis_id": y_axis_id}))
    return output.model_copy(update={"series": normalized, "warnings": warnings})
