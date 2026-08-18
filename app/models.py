from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .series_point_limits import series_data_point_limits


ProviderName = Literal["openai", "gemini"]
ImageDetail = Literal["high", "medium", "auto", "low", "original", "ultra_high"]
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]
ValueType = Literal["number", "datetime", "unknown"]
AxisDirection = Literal["x", "y"]
StageName = Literal[
    "uploaded",
    "page_selected",
    "crop_ready",
    "crop_review",
    "calibration_ready",
    "x_calibration_review",
    "y_calibration_review",
    "series_ready",
    "series_review",
    "complete",
    "error",
]
EventCategory = Literal["API", "USER", "ARTIFACT", "WARN", "ERROR", "STAGE", "SYSTEM"]
StageDecision = Literal["proposal", "accept_previous", "revise_previous"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ModelOption(BaseModel):
    id: str
    label: str
    family: str
    provider: ProviderName = "openai"
    default: bool = False
    enabled: bool = True
    reasoning_efforts: list[ReasoningEffort] = Field(default_factory=list)
    default_reasoning_effort: ReasoningEffort | None = None
    image_detail_options: list[ImageDetail] = Field(default_factory=list)
    default_image_detail: ImageDetail | None = None


class ModelsFile(BaseModel):
    models: list[ModelOption]

    def enabled_models(self) -> list[ModelOption]:
        return [model for model in self.models if model.enabled]

    def default_model(self) -> ModelOption:
        for model in self.enabled_models():
            if model.default:
                return model
        enabled = self.enabled_models()
        if not enabled:
            raise ValueError("models.json does not contain any enabled models")
        return enabled[0]

    def get_enabled(self, model_id: str) -> ModelOption:
        for model in self.enabled_models():
            if model.id == model_id:
                return model
        raise ValueError(f"Unsupported or disabled model: {model_id}")


class RunSettings(BaseModel):
    model_id: str
    image_detail: ImageDetail = "high"
    reasoning_effort: ReasoningEffort | None = "medium"
    mock_mode: bool = False


class NormPoint(BaseModel):
    x: int = Field(ge=0, le=999)
    y: int = Field(ge=0, le=999)


class PixelPoint(BaseModel):
    x: float
    y: float


class NormBBox(BaseModel):
    left: int = Field(ge=0, le=999)
    top: int = Field(ge=0, le=999)
    right: int = Field(ge=0, le=999)
    bottom: int = Field(ge=0, le=999)

    @model_validator(mode="after")
    def validate_order(self) -> "NormBBox":
        if self.right <= self.left:
            raise ValueError("right must be greater than left")
        if self.bottom <= self.top:
            raise ValueError("bottom must be greater than top")
        return self


class PixelBBox(BaseModel):
    left: float
    top: float
    right: float
    bottom: float

    @model_validator(mode="after")
    def validate_order(self) -> "PixelBBox":
        if self.right <= self.left:
            raise ValueError("right must be greater than left")
        if self.bottom <= self.top:
            raise ValueError("bottom must be greater than top")
        return self

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top


class ChartValue(BaseModel):
    value_raw: str
    value_type: ValueType = "number"
    parsed_value: float | None = None
    parsed_datetime: str | None = None
    unit: str | None = None

    @model_validator(mode="after")
    def validate_parsed(self) -> "ChartValue":
        if self.value_type == "number" and self.parsed_value is None:
            try:
                self.parsed_value = float(self.value_raw.replace(",", ""))
            except ValueError as exc:
                raise ValueError("numeric chart values require parsed_value") from exc
        if self.value_type == "datetime" and not self.parsed_datetime:
            raise ValueError("datetime chart values require parsed_datetime")
        return self


class Asset(BaseModel):
    path: str
    width: int
    height: int
    label: str | None = None


class EventRecord(BaseModel):
    ts: str = Field(default_factory=utc_now_iso)
    category: EventCategory
    message: str
    run_id: str | None = None
    stage: str | None = None
    attempt: int | None = None
    artifact_path: str | None = None


class AttemptRecord(BaseModel):
    id: str
    stage: str
    attempt_number: int
    status: Literal["accepted", "revised", "failed", "needs_review", "mocked"]
    request_path: str | None = None
    response_path: str | None = None
    parsed_path: str | None = None
    overlay_path: str | None = None
    validation_status: Literal["valid", "invalid", "skipped"] = "skipped"
    confidence: float | None = None
    warnings: list[str] = Field(default_factory=list)


class CropProposal(BaseModel):
    bbox: NormBBox
    confidence: float | None = Field(default=None, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)
    unsupported_flags: list[str] = Field(default_factory=list)


class CropConversationResponse(BaseModel):
    response_kind: StageDecision
    revision_reason: str | None
    proposal: CropProposal | None

    @model_validator(mode="after")
    def validate_decision(self) -> "CropConversationResponse":
        if self.response_kind in {"proposal", "revise_previous"} and self.proposal is None:
            raise ValueError("proposal is required for proposal or revise_previous responses")
        if self.response_kind == "accept_previous" and self.proposal is not None:
            raise ValueError("proposal must be null when accepting the previous attempt")
        return self


class CropState(BaseModel):
    bbox_full_norm: NormBBox
    bbox_full_px: PixelBBox
    image: Asset | None = None
    approved: bool = False
    warnings: list[str] = Field(default_factory=list)


class CalibrationPoint(BaseModel):
    label: Literal["x1", "x2", "y1", "y2"]
    crop_image_norm: NormPoint
    crop_image_px: PixelPoint | None = None
    chart_value: ChartValue


class IdentifiedAxis(BaseModel):
    axis_id: str
    direction: AxisDirection
    name: str
    unit: str | None = None
    quantity: str | None = None
    location_description: str
    is_primary_for_digitization: bool = True
    ignore_reason: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("axis_id")
    @classmethod
    def validate_axis_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("axis_id is required")
        return value.strip()


class AxisIdentificationOutput(BaseModel):
    axes: list[IdentifiedAxis] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unsupported_flags: list[str] = Field(default_factory=list)


class AxisCalibrationOutput(BaseModel):
    axis: AxisDirection
    points: list[CalibrationPoint] = Field(min_length=2, max_length=2)
    confidence: float | None = Field(default=None, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)
    unsupported_flags: list[str] = Field(default_factory=list)

    @field_validator("points")
    @classmethod
    def validate_labels(cls, points: list[CalibrationPoint]) -> list[CalibrationPoint]:
        labels = {point.label for point in points}
        valid = {"x1", "x2"} if any(point.label.startswith("x") for point in points) else {"y1", "y2"}
        if labels != valid:
            raise ValueError(f"expected point labels {sorted(valid)}")
        return points


class AxisCalibrationConversationResponse(BaseModel):
    response_kind: StageDecision
    revision_reason: str | None
    proposal: AxisCalibrationOutput | None

    @model_validator(mode="after")
    def validate_decision(self) -> "AxisCalibrationConversationResponse":
        if self.response_kind in {"proposal", "revise_previous"} and self.proposal is None:
            raise ValueError("proposal is required for proposal or revise_previous responses")
        if self.response_kind == "accept_previous" and self.proposal is not None:
            raise ValueError("proposal must be null when accepting the previous attempt")
        return self


class CalibratedAxis(BaseModel):
    axis_id: str
    direction: AxisDirection
    name: str
    unit: str | None = None
    quantity: str | None = None
    location_description: str
    points: list[CalibrationPoint] = Field(default_factory=list)
    approved: bool = False
    confidence: float | None = Field(default=None, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)

    @property
    def display_name(self) -> str:
        unit = f" ({self.unit})" if self.unit else ""
        return f"{self.name}{unit}"


class CalibrationState(BaseModel):
    identified_axes: list[IdentifiedAxis] = Field(default_factory=list)
    calibrated_axes: list[CalibratedAxis] = Field(default_factory=list)
    x_points: list[CalibrationPoint] = Field(default_factory=list)
    y_points: list[CalibrationPoint] = Field(default_factory=list)
    approved_x: bool = False
    approved_y: bool = False
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def migrate_legacy_axes(self) -> "CalibrationState":
        if not self.calibrated_axes:
            if self.x_points:
                self.calibrated_axes.append(
                    CalibratedAxis(
                        axis_id="x",
                        direction="x",
                        name="X axis",
                        location_description="Legacy x-axis calibration",
                        points=self.x_points,
                        approved=self.approved_x,
                    )
                )
            if self.y_points:
                self.calibrated_axes.append(
                    CalibratedAxis(
                        axis_id="y",
                        direction="y",
                        name="Y axis",
                        location_description="Legacy y-axis calibration",
                        points=self.y_points,
                        approved=self.approved_y,
                    )
                )
        if not self.x_points:
            first_x = next((axis for axis in self.calibrated_axes if axis.direction == "x"), None)
            if first_x:
                self.x_points = first_x.points
                self.approved_x = first_x.approved
        if not self.y_points:
            first_y = next((axis for axis in self.calibrated_axes if axis.direction == "y"), None)
            if first_y:
                self.y_points = first_y.points
                self.approved_y = first_y.approved
        return self

    def usable_axes(self) -> list[CalibratedAxis]:
        axes = self._calibrated_or_legacy_axes()
        return [
            axis
            for axis in axes
            if any(item.axis_id == axis.axis_id and item.is_primary_for_digitization for item in self.identified_axes)
            or not self.identified_axes
        ]

    def approved_usable_axes(self) -> list[CalibratedAxis]:
        return [axis for axis in self.usable_axes() if axis.approved and len(axis.points) == 2]

    def has_approved_direction(self, direction: AxisDirection) -> bool:
        return any(axis.direction == direction for axis in self.approved_usable_axes())

    def all_usable_axes_approved(self) -> bool:
        usable = self.usable_axes()
        if not usable:
            return False
        return all(axis.approved and len(axis.points) == 2 for axis in usable) and self.has_approved_direction("x") and self.has_approved_direction("y")

    def axis_by_id(self, axis_id: str | None) -> CalibratedAxis | None:
        if not axis_id:
            return None
        return next((axis for axis in self._calibrated_or_legacy_axes() if axis.axis_id == axis_id), None)

    def default_axis_id(self, direction: AxisDirection) -> str | None:
        for axis in self.approved_usable_axes():
            if axis.direction == direction:
                return axis.axis_id
        for axis in self._calibrated_or_legacy_axes():
            if axis.direction == direction:
                return axis.axis_id
        return None

    def _calibrated_or_legacy_axes(self) -> list[CalibratedAxis]:
        if self.calibrated_axes:
            return self.calibrated_axes
        axes: list[CalibratedAxis] = []
        if self.x_points:
            axes.append(
                CalibratedAxis(
                    axis_id="x",
                    direction="x",
                    name="X axis",
                    location_description="Legacy x-axis calibration",
                    points=self.x_points,
                    approved=self.approved_x,
                )
            )
        if self.y_points:
            axes.append(
                CalibratedAxis(
                    axis_id="y",
                    direction="y",
                    name="Y axis",
                    location_description="Legacy y-axis calibration",
                    points=self.y_points,
                    approved=self.approved_y,
                )
            )
        return axes


class SeriesPointProposal(BaseModel):
    point_index: int = Field(ge=0)
    segment_index: int = Field(default=0, ge=0)
    chart_x: ChartValue
    chart_y: ChartValue


class SeriesPoint(BaseModel):
    point_index: int = Field(ge=0)
    segment_index: int = Field(default=0, ge=0)
    crop_image_norm: NormPoint
    crop_image_px: PixelPoint | None = None
    chart_x: ChartValue | None = None
    chart_y: ChartValue | None = None


class SeriesIdentification(BaseModel):
    series_name: str | None = None
    visual_description: str | None = None
    line_color: str | None = Field(
        default=None,
        description="Line colour as either a Pillow named colour accepted by PIL.ImageColor, or a hex colour such as #2563eb.",
    )
    line_style: str | None = None
    x_axis_id: str | None = None
    y_axis_id: str | None = None
    axis_selection_reason: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_series(self) -> "SeriesIdentification":
        if not self.series_name:
            raise ValueError("series_name is required")
        return self


class SeriesIdentificationOutput(BaseModel):
    series: list[SeriesIdentification] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unsupported_flags: list[str] = Field(default_factory=list)


class SeriesDigitizationOutput(BaseModel):
    points: list[SeriesPointProposal] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)
    unsupported_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_series(self) -> "SeriesDigitizationOutput":
        min_points, max_points = series_data_point_limits()
        if not min_points <= len(self.points) <= max_points:
            raise ValueError(f"series extraction requires {min_points} to {max_points} points")
        return self


class SeriesDigitizationConversationResponse(BaseModel):
    response_kind: StageDecision
    revision_reason: str | None
    proposal: SeriesDigitizationOutput | None

    @model_validator(mode="after")
    def validate_decision(self) -> "SeriesDigitizationConversationResponse":
        if self.response_kind in {"proposal", "revise_previous"} and self.proposal is None:
            raise ValueError("proposal is required for proposal or revise_previous responses")
        if self.response_kind == "accept_previous" and self.proposal is not None:
            raise ValueError("proposal must be null when accepting the previous attempt")
        return self


class SeriesState(BaseModel):
    id: str
    name: str
    llm_series_name: str | None = None
    visual_description: str | None = None
    line_color: str | None = None
    line_style: str | None = None
    x_axis_id: str | None = None
    y_axis_id: str | None = None
    axis_selection_reason: str | None = None
    confidence: float | None = None
    source: Literal["llm", "manual"] = "llm"
    points: list[SeriesPoint] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PromptMetadata(BaseModel):
    prompt_pack: str = "prompts.yaml"
    prompt_version: str
    prompt_hash: str


class RunState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    stage: StageName = "uploaded"
    active_job: str | None = None
    settings: RunSettings
    prompt_metadata: PromptMetadata
    upload_filename: str
    original_path: str
    pages: list[Asset] = Field(default_factory=list)
    selected_page_index: int | None = None
    canonical_image: Asset | None = None
    crop: CropState | None = None
    calibration: CalibrationState = Field(default_factory=CalibrationState)
    pending_series: list[SeriesIdentification] = Field(default_factory=list)
    series: list[SeriesState] = Field(default_factory=list)
    attempts: list[AttemptRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    active_step_status: str | None = None

    def touch(self) -> None:
        self.updated_at = utc_now_iso()


class StatePatch(BaseModel):
    data: dict[str, Any]
