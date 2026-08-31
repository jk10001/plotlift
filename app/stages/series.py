from __future__ import annotations

import json
import uuid

from .. import coordinates
from ..artifacts import (
    attempt_dir,
    next_series_retry_round,
    relative_path,
    save_attempt_json,
    save_json,
    save_state,
    series_retry_dir,
)
from ..color_utils import normalize_color
from ..config import get_config
from ..llm_conversation import prompt_cache_key, response_id, save_conversation_entry
from ..llm_client import ChartLLMClient
from ..logging_utils import emit_event
from ..models import (
    AttemptRecord,
    RunState,
    ScatterSeriesDigitizationConversationResponse,
    ScatterSeriesDigitizationOutput,
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
from ..series_point_limits import line_series_data_point_limits, scatter_series_max_data_points
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
        emit_event(root, "WARN", "No line or scatter series were identified", run_id=state.run_id, stage="series")
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
        emit_event(root, "STAGE", f"Identified {len(output.series)} series", run_id=state.run_id, stage="series")
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
    retry_round: int | None = None,
    retry_mode: str | None = None,
    baseline_series: SeriesState | None = None,
    baseline_overlay_path=None,
) -> SeriesState:
    cfg = get_config()
    is_scatter = series_description.series_type == "scatter"
    prompt_prefix = "scatter_digitization" if is_scatter else "line_digitization"
    system_prompt = prompt_pack.render(f"series.{prompt_prefix}_system")
    target_series = _series_description_text(series_description, series_index, series_total)
    is_refinement = retry_mode == "refine"
    if is_refinement and (baseline_series is None or baseline_overlay_path is None):
        raise ValueError("refinement requires a baseline series and overlay")
    latest = _series_state_to_output(baseline_series) if baseline_series is not None and is_refinement else None
    latest_series_state = baseline_series.model_copy(deep=True) if baseline_series is not None and is_refinement else None
    previous_response_id: str | None = None
    previous_overlay_path = None
    conversation_history: list[dict] = []
    conversation_entries: list[dict] = []
    series_id = series_id or uuid.uuid4().hex[:8]
    cache_key = prompt_cache_key(state.run_id, "series", series_id)
    completed = False
    valid_decision_received = False
    emit_event(root, "STAGE", f"Starting digitisation for {series_description.series_name}", run_id=state.run_id, stage="series")

    for attempt in range(1, cfg.max_series_attempts + 1):
        if attempt == 1 and is_refinement:
            prompt_key = f"series.{prompt_prefix}_refine_initial"
            user_prompt = prompt_pack.render(
                prompt_key,
                target_series=target_series,
                baseline_json=json.dumps(latest.model_dump(mode="json"), indent=2, ensure_ascii=False),
            )
            images_for_model = [crop_path, baseline_overlay_path]
        else:
            prompt_key = f"series.{prompt_prefix}_{'initial' if attempt == 1 else 'confirm'}"
            user_prompt = prompt_pack.render(prompt_key, target_series=target_series)
            images_for_model = [previous_overlay_path or crop_path]
        image_for_model = images_for_model[0]
        request_path = None
        response_path = None
        parsed_path = None
        try:
            if state.settings.mock_mode:
                decision = _mock_series_digitization_decision(attempt, series_description.series_type, refine=is_refinement)
                request = {
                    "mock": True,
                    "system": system_prompt,
                    "prompt": user_prompt,
                    "target_series": target_series,
                    "source_image_paths": [relative_path(path, root) for path in images_for_model],
                    "previous_response_id": previous_response_id,
                    "prompt_cache_key": cache_key,
                }
                if len(images_for_model) == 1:
                    request["source_image_path"] = relative_path(image_for_model, root)
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
                    image_path=image_for_model if len(images_for_model) == 1 else None,
                    image_paths=images_for_model if len(images_for_model) > 1 else None,
                    schema_name="ScatterSeriesDigitizationDecision" if is_scatter else "SeriesDigitizationDecision",
                    schema=strict_json_schema(
                        ScatterSeriesDigitizationConversationResponse if is_scatter else SeriesDigitizationConversationResponse
                    ),
                    previous_response_id=previous_response_id,
                    prompt_cache_key=cache_key,
                    conversation_history=conversation_history,
                )
                decision = (
                    ScatterSeriesDigitizationConversationResponse.model_validate(response["json"])
                    if is_scatter
                    else SeriesDigitizationConversationResponse.model_validate(response["json"])
                )
            _validate_series_decision(decision, attempt, allow_initial_review=is_refinement)
            valid_decision_received = True
            request_artifact = _scrub_request(request)
            request_artifact["source_image_paths"] = [relative_path(path, root) for path in images_for_model]
            if len(images_for_model) == 1:
                request_artifact["source_image_path"] = relative_path(image_for_model, root)
            request_artifact["previous_response_id"] = previous_response_id
            request_artifact["prompt_cache_key"] = cache_key
            request_artifact["target_series"] = target_series
            request_path = save_attempt_json(state.run_id, "series", attempt, "request.json", request_artifact, series_id=series_id, retry_round=retry_round)
            response_path = save_attempt_json(state.run_id, "series", attempt, "response.json", response, series_id=series_id, retry_round=retry_round)
            parsed_path = save_attempt_json(state.run_id, "series", attempt, "parsed.json", decision.model_dump(mode="json"), series_id=series_id, retry_round=retry_round)
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
            overlay_path = attempt_dir(state.run_id, "series", attempt, series_id=series_id, retry_round=retry_round) / "overlay.png"
            render_series_overlay(crop_path, [series_state], overlay_path)
            overlay_rel = relative_path(overlay_path, root)
            emit_event(root, "ARTIFACT", "Rendered current-series overlay", run_id=state.run_id, stage="series", attempt=attempt, artifact_path=overlay_rel)
            conversation_path = save_conversation_entry(
                root=root,
                run_id=state.run_id,
                stage="series",
                series_id=series_id,
                retry_round=retry_round,
                entries=conversation_entries,
                entry={
                    "attempt": attempt,
                    "response_kind": decision.response_kind,
                    "revision_reason": decision.revision_reason,
                    "target_series": target_series,
                    "source_image_paths": [relative_path(path, root) for path in images_for_model],
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
                    id=_series_attempt_id(series_id, attempt, retry_round),
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
                    retry_round=retry_round,
                    retry_mode=retry_mode,
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
                        "image_paths": [str(path) for path in images_for_model],
                        "model_text": response["text"],
                    }
                )
            previous_response_id = current_response_id
            previous_overlay_path = overlay_path
            emit_event(root, "STAGE", "Sending current-series overlay back for model confirmation", run_id=state.run_id, stage="series", attempt=attempt, artifact_path=overlay_rel)
        except Exception as exc:  # noqa: BLE001
            response_path = save_attempt_json(state.run_id, "series", attempt, "error.json", {"error": str(exc)}, series_id=series_id, retry_round=retry_round)
            state.attempts.append(
                AttemptRecord(
                    id=_series_attempt_id(series_id, attempt, retry_round),
                    stage="series",
                    attempt_number=attempt,
                    status="failed",
                    request_path=request_path,
                    response_path=response_path,
                    parsed_path=parsed_path,
                    validation_status="invalid",
                    warnings=[str(exc)],
                    retry_round=retry_round,
                    retry_mode=retry_mode,
                )
            )
            emit_event(root, "ERROR", f"Series attempt failed: {exc}", run_id=state.run_id, stage="series", attempt=attempt)
            save_state(state)
    if not valid_decision_received:
        raise RuntimeError(f"series extraction failed without a valid response for {series_description.series_name}")
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


def _series_attempt_id(series_id: str, attempt: int, retry_round: int | None) -> str:
    if retry_round is None:
        return f"series-{series_id}-{attempt:02d}"
    return f"series-{series_id}-retry-{retry_round:02d}-{attempt:02d}"


def retry_series_digitization(
    state: RunState,
    prompt_pack: PromptPack,
    client: ChartLLMClient,
    series_id: str,
    mode: str = "restart",
) -> RunState:
    if not state.crop or not state.crop.image:
        raise ValueError("approved crop is required before series extraction")
    if not state.calibration.has_approved_direction("x") or not state.calibration.has_approved_direction("y"):
        raise ValueError("approved x and y calibration are required before series extraction")
    target_index = next((index for index, series in enumerate(state.series) if series.id == series_id), None)
    if target_index is None:
        raise ValueError(f"series {series_id!r} does not exist")
    if mode not in {"restart", "refine"}:
        raise ValueError("retry mode must be 'restart' or 'refine'")
    original = state.series[target_index].model_copy(deep=True)
    root = get_config().runs_dir / state.run_id
    crop_path = root / state.crop.image.path
    retry_round = next_series_retry_round(state.run_id, series_id)
    retry_root = series_retry_dir(state.run_id, series_id, retry_round)
    save_json(
        retry_root / "retry.json",
        {"series_id": series_id, "retry_round": retry_round, "retry_mode": mode},
    )
    baseline_overlay_path = None
    if mode == "refine":
        baseline = _series_state_to_output(original)
        save_json(retry_root / "baseline.json", baseline.model_dump(mode="json"))
        baseline_overlay_path = retry_root / "baseline_overlay.png"
        render_series_overlay(crop_path, [original], baseline_overlay_path)
        emit_event(
            root,
            "ARTIFACT",
            "Saved refinement baseline and overlay",
            run_id=state.run_id,
            stage="series",
            artifact_path=relative_path(baseline_overlay_path, root),
        )
    state.stage = "series_review"
    save_state(state)
    try:
        digitized = _digitize_identified_series(
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
                series_type=original.series_type,
                marker_style=original.marker_style,
                estimated_total_points=original.estimated_total_points,
                x_axis_id=original.x_axis_id,
                y_axis_id=original.y_axis_id,
                axis_selection_reason=original.axis_selection_reason,
                confidence=original.confidence,
            ),
            series_index=target_index + 1,
            series_total=len(state.series),
            series_id=original.id,
            append_to_state=False,
            retry_round=retry_round,
            retry_mode=mode,
            baseline_series=original if mode == "refine" else None,
            baseline_overlay_path=baseline_overlay_path,
        )
        replacement = _merge_retry_result(original, digitized)
        state.series[target_index] = replacement
        emit_event(
            root,
            "STAGE",
            f"Completed {mode} retry for series {original.name}",
            run_id=state.run_id,
            stage="series",
        )
    except Exception as exc:  # noqa: BLE001
        warning = f"Retry did not produce a valid result for {original.name}; restored the previous series. {exc}"
        state.series[target_index] = original
        state.warnings.append(warning)
        emit_event(root, "WARN", warning, run_id=state.run_id, stage="series")
    state.stage = "series_review"
    save_state(state)
    return state


def _series_state_to_output(series: SeriesState) -> SeriesDigitizationOutput | ScatterSeriesDigitizationOutput:
    points = [
        SeriesPointProposal(
            point_index=point.point_index,
            segment_index=point.segment_index,
            chart_x=point.chart_x,
            chart_y=point.chart_y,
        )
        for point in series.points
        if point.chart_x is not None and point.chart_y is not None
    ]
    if len(points) != len(series.points):
        raise ValueError("all baseline points must have chart-space coordinates")
    common = {
        "points": points,
        "confidence": series.confidence,
        "warnings": list(series.warnings),
        "unsupported_flags": [],
    }
    if series.series_type == "scatter":
        return ScatterSeriesDigitizationOutput.model_construct(
            **common,
            series_truncated=series.series_truncated,
            estimated_total_points=series.estimated_total_points,
        )
    return SeriesDigitizationOutput.model_construct(**common)


def _merge_retry_result(original: SeriesState, digitized: SeriesState) -> SeriesState:
    """Retain user-facing identity and appearance while replacing model-derived results."""
    return original.model_copy(
        deep=True,
        update={
            "points": digitized.points,
            "confidence": digitized.confidence,
            "warnings": digitized.warnings,
            "series_truncated": digitized.series_truncated,
            "estimated_total_points": digitized.estimated_total_points,
        },
    )


def _series_output_to_state(
    output: SeriesDigitizationOutput | ScatterSeriesDigitizationOutput,
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
    series_truncated = False
    estimated_total_points = series_description.estimated_total_points
    if series_description.series_type == "scatter":
        scatter_limit = scatter_series_max_data_points()
        points.sort(
            key=lambda item: (
                item.crop_image_px.x if item.crop_image_px else float("inf"),
                item.crop_image_px.y if item.crop_image_px else float("inf"),
            )
        )
        raw_point_count = len(points)
        points = points[:scatter_limit]
        for point_index, point in enumerate(points):
            point.point_index = point_index
            point.segment_index = 0
        output_estimate = output.estimated_total_points if isinstance(output, ScatterSeriesDigitizationOutput) else None
        estimates = [value for value in (estimated_total_points, output_estimate, raw_point_count) if value is not None]
        estimated_total_points = max(estimates) if estimates else raw_point_count
        series_truncated = (
            raw_point_count > scatter_limit
            or estimated_total_points > scatter_limit
            or (isinstance(output, ScatterSeriesDigitizationOutput) and output.series_truncated)
        )
    else:
        points.sort(key=lambda item: (item.segment_index, item.point_index))
        estimated_total_points = None
    warnings = output.warnings + output.unsupported_flags
    if series_truncated:
        warnings.append(
            f"Scatter series truncated to the first {scatter_series_max_data_points()} markers in visual reading order"
        )
    return SeriesState(
        id=series_id,
        name=series_description.series_name or f"Series {len(state.series) + 1}",
        series_type=series_description.series_type,
        llm_series_name=series_description.series_name,
        visual_description=series_description.visual_description,
        line_color=normalize_color(series_description.line_color, COLORS["series"][len(state.series) % len(COLORS["series"])]),
        line_style=series_description.line_style,
        marker_style=series_description.marker_style,
        series_truncated=series_truncated,
        estimated_total_points=estimated_total_points,
        x_axis_id=x_axis_id,
        y_axis_id=y_axis_id,
        axis_selection_reason=series_description.axis_selection_reason,
        confidence=output.confidence if output.confidence is not None else series_description.confidence,
        points=points,
        warnings=warnings,
    )


def _mock_series_identification() -> SeriesIdentificationOutput:
    return SeriesIdentificationOutput(
        series=[
            SeriesIdentification(
                series_name="Mock series A",
                visual_description="A rising blue line used for local pipeline testing.",
                line_color="#0891b2",
                line_style="solid",
                series_type="line",
                x_axis_id="x_flow",
                y_axis_id="y_head",
                axis_selection_reason="Mock series uses the default flow and head axes.",
                confidence=0.81,
            ),
            SeriesIdentification(
                series_name="Mock scatter B",
                series_type="scatter",
                visual_description="Four orange circular markers used for local pipeline testing.",
                line_color="#ea580c",
                marker_style="filled circle",
                estimated_total_points=4,
                x_axis_id="x_flow",
                y_axis_id="y_head",
                axis_selection_reason="Mock scatter uses the default flow and head axes.",
                confidence=0.79,
            ),
        ]
    )


def _mock_series_digitization_decision(
    attempt: int,
    series_type: str = "line",
    *,
    refine: bool = False,
) -> SeriesDigitizationConversationResponse | ScatterSeriesDigitizationConversationResponse:
    if attempt > 1 or refine:
        response_type = ScatterSeriesDigitizationConversationResponse if series_type == "scatter" else SeriesDigitizationConversationResponse
        return response_type(response_kind="accept_previous", revision_reason=None, proposal=None)
    min_points, _ = line_series_data_point_limits()
    point_count = min(4, scatter_series_max_data_points()) if series_type == "scatter" else min_points
    points = []
    for index in range(point_count):
        ratio = index / max(1, point_count - 1)
        points.append(
            SeriesPointProposal(
                point_index=index,
                chart_x={"value_raw": f"{(100 + ratio * 900):g}", "value_type": "number"},
                chart_y={"value_raw": f"{(60 - ratio * 30 + (2 if index % 2 else 0)):g}", "value_type": "number"},
            )
        )
    if series_type == "scatter":
        return ScatterSeriesDigitizationConversationResponse(
            response_kind="proposal",
            revision_reason="Initial mock scatter proposal.",
            proposal=ScatterSeriesDigitizationOutput(
                confidence=0.79,
                points=points,
                series_truncated=False,
                estimated_total_points=point_count,
            ),
        )
    return SeriesDigitizationConversationResponse(
        response_kind="proposal",
        revision_reason="Initial mock series proposal.",
        proposal=SeriesDigitizationOutput(
            confidence=0.81,
            points=points,
        ),
    )


def _validate_series_decision(
    decision: SeriesDigitizationConversationResponse | ScatterSeriesDigitizationConversationResponse,
    attempt: int,
    *,
    allow_initial_review: bool = False,
) -> None:
    if attempt == 1:
        allowed = {"accept_previous", "revise_previous"} if allow_initial_review else {"proposal"}
        if decision.response_kind not in allowed:
            expected = "accept_previous or revise_previous" if allow_initial_review else "proposal"
            raise ValueError(f"first series digitisation attempt must return {expected}")
    if attempt > 1 and decision.response_kind not in {"accept_previous", "revise_previous"}:
        raise ValueError("series review attempts must return accept_previous or revise_previous")


def _series_description_text(series: SeriesIdentification, series_index: int, series_total: int) -> str:
    return "\n".join(
        [
            f"Series {series_index} of {series_total}",
            f"Name: {series.series_name or 'unnamed series'}",
            f"Series type: {series.series_type}",
            f"Colour: {series.line_color or 'unknown'}",
            f"Line style: {series.line_style or 'unknown'}",
            f"Marker style: {series.marker_style or 'unknown'}",
            f"Estimated total points: {series.estimated_total_points if series.estimated_total_points is not None else 'unknown'}",
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
