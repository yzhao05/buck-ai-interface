from typing import Literal
import json
import math
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .vision import ChatImage


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def legacy_pwm_options(options):
    return {'pwm_denoise': 'none', 'pwm_filter_cycles': 1., **options}


class Mapping(StrictModel):
    time: str
    current: str
    voltage: str
    pwm: str | None = None
    time_unit: Literal["s", "ms", "us", "ns"] = "s"
    current_unit: Literal["A", "mA"] = "A"
    voltage_unit: Literal["V", "mV"] = "V"


class AnalysisRequest(StrictModel):
    dataset_id: str
    mapping: Mapping


class ControllerParameters(StrictModel):
    R1_ohm: float = Field(default=11000, ge=1, le=1e9)
    R2_ohm: float = Field(default=85000, ge=1, le=1e9)
    R3_ohm: float = Field(default=120000, ge=1, le=1e9)
    R4_ohm: float = Field(default=47000, ge=1, le=1e9)
    C2_F: float = Field(default=1.1e-9, ge=1e-12, le=1e-3)
    C3_F: float = Field(default=2.7e-9, ge=1e-12, le=1e-3)
    opamp_gain: float = Field(default=1e6, ge=1, le=1e8)
    reference_V: float = Field(default=5, ge=0, le=2000)
    pwm_gain: float = Field(default=.25, gt=0, le=100)
    duty_min: float = Field(default=.1, ge=0, lt=1)
    duty_max: float = Field(default=.9, gt=0, le=1)
    carrier_phase_at_t0: float = Field(default=0, ge=0, lt=1)
    initial_c2_V: float = Field(default=0, ge=-2000, le=2000)
    initial_c3_V: float = Field(default=0, ge=-2000, le=2000)

    @model_validator(mode="after")
    def ordered_duty(self):
        if self.duty_min >= self.duty_max:
            raise ValueError("占空比下限必须小于上限。")
        return self


class FitOptions(StrictModel):
    vin: float = Field(default=28, gt=0, le=2000)
    frequency_hz: float = Field(default=100000, ge=100, le=2000000)
    start_s: float | None = None
    end_s: float | None = None
    train_fraction: float = Field(default=0.7, ge=0.5, le=0.85)
    mode: Literal["LC", "LCR"] = "LCR"
    resistance_ohm: float = Field(default=6, gt=0.01, le=100000)
    # "integral" remains available for reproducing older reports/API clients.
    method: Literal["weighted_integral", "pann", "integral"] = "pann"
    epochs: int = Field(default=200, ge=10, le=300)
    pann_initial_state: Literal["joint", "measured"] = "joint"
    pann_window_cycles: int = Field(default=0, ge=0, le=3, description="0: training-noise heuristic; 1/2/3: explicit cycles")
    pwm_source: Literal["measured", "controller"] = "measured"
    pwm_denoise: Literal["sg", "modified_sinc", "none"] = "sg"
    pwm_filter_cycles: float = Field(default=6., ge=.1, le=8)
    controller_confirmed: bool = False
    controller: ControllerParameters | None = None


class FitRequest(AnalysisRequest):
    options: FitOptions = Field(default_factory=FitOptions)


class IdentificationChanges(StrictModel):
    method: Literal["pann", "weighted_integral"] | None = None
    mode: Literal["LC", "LCR"] | None = None
    resistance_ohm: float | None = Field(default=None, strict=True, gt=0.01, le=100000)


class ConfigureIdentification(StrictModel):
    open_panel: bool = Field(strict=True)
    changes: IdentificationChanges
    start_after_apply: bool = Field(strict=True)


class IdentificationUIContext(StrictModel):
    request_id: str = Field(min_length=1, max_length=128)
    # Separate from the displayed historical result's options.
    options: FitOptions


class ChatMessage(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)
    images: list[ChatImage] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def user_images_only(self):
        if self.images and self.role != "user":
            raise ValueError("Only user messages can include images")
        return self


class ChatGroundTruth(StrictModel):
    result_id: str = Field(min_length=1, max_length=128)
    L_H: float = Field(gt=0, le=1e30)
    C_F: float = Field(gt=0, le=1e30)


class ExperimentDataset(StrictModel):
    filename: str = Field(default="", max_length=500)
    sha256: str = Field(default="", max_length=64)


class SavedExperiment(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    created: int = Field(ge=0, le=9007199254740991)
    updated: int = Field(ge=0, le=9007199254740991)
    dataset: ExperimentDataset
    result: dict
    ground_truth: ChatGroundTruth | None = None

    @model_validator(mode="after")
    def validate_snapshot(self):
        # Reports created before voltage denoising used unfiltered voltage.
        if isinstance(self.result.get('options'), dict):
            self.result = {**self.result, 'options': legacy_pwm_options(self.result['options'])}
        if len(json.dumps(self.result, allow_nan=False).encode()) > 128*1024:
            raise ValueError("Experiment snapshot exceeds 128 KB")
        parameters = self.result.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError("Invalid experiment parameters")
        for key in ("L_H", "C_F", "R_ohm"):
            value = parameters.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 < value <= 1e30 or not math.isfinite(value):
                raise ValueError("Invalid experiment parameters")
        if self.ground_truth and self.ground_truth.result_id != self.id:
            raise ValueError("Ground truth must belong to this experiment")
        return self


class ExperimentParameters(StrictModel):
    L_H: float = Field(gt=0, le=1e30)
    C_F: float = Field(gt=0, le=1e30)
    R_ohm: float = Field(gt=0, le=1e30)


class ExperimentComparisonContext(StrictModel):
    options: FitOptions
    mapping: Mapping
    start_s: float
    end_s: float

    @model_validator(mode="before")
    @classmethod
    def legacy_options(cls, value):
        if isinstance(value, dict) and isinstance(value.get('options'), dict):
            return {**value, 'options': legacy_pwm_options(value['options'])}
        return value

    @model_validator(mode="after")
    def ordered_interval(self):
        if self.end_s <= self.start_s:
            raise ValueError("Comparison interval must have positive duration")
        return self


class ChatExperiment(StrictModel):
    comparison_context: ExperimentComparisonContext | None = None
    id: str = Field(min_length=1, max_length=128)
    dataset: ExperimentDataset
    method: Literal["pann", "weighted_integral", "integral"] | None = None
    pwm_source: Literal["measured", "controller"] | None = None
    parameters: ExperimentParameters
    ground_truth: ChatGroundTruth | None = None

    @model_validator(mode="after")
    def truth_matches_experiment(self):
        if self.ground_truth and self.ground_truth.result_id != self.id:
            raise ValueError("Ground truth must belong to this experiment")
        return self


class HistoricalAnalysis(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=100000)
    method: Literal["pann", "weighted_integral", "integral"] | None = None
    pwm_source: Literal["measured", "controller"] | None = None
    result_id: str | None = Field(default=None, max_length=128)


class AnalysisMetadata(StrictModel):
    kind: Literal["identification_analysis"] = "identification_analysis"
    method: Literal["pann", "weighted_integral", "integral"] | None = None
    pwm_source: Literal["measured", "controller"] | None = None
    result_id: str | None = Field(default=None, max_length=128)
    filename: str | None = Field(default=None, max_length=1024)


class ConversationMessage(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=100000)
    created: int | None = None
    request_message_id: str | None = Field(default=None, max_length=128)
    analysis: AnalysisMetadata | None = None


class DiagnosticReference(StrictModel):
    dataset_id: str = Field(min_length=1, max_length=128)
    result_id: str = Field(min_length=1, max_length=128)
    confirmed_same_experiment: Literal[True]


class ChatRequest(StrictModel):
    enable_diagnostic_controls: bool = False
    diagnostic_reference: DiagnosticReference | None = None
    ui_context: IdentificationUIContext | None = None
    language: Literal["zh", "en"] = "zh"
    identification_enabled: bool = False
    current_result_analysis: bool = False
    message: str = Field(min_length=1, max_length=4000)
    images: list[ChatImage] = Field(default_factory=list, max_length=3)
    history: list[ChatMessage] = Field(default_factory=list, max_length=12)
    dataset_id: str | None = None
    mapping: Mapping | None = None
    options: FitOptions = Field(default_factory=FitOptions)
    result_id: str | None = None
    ground_truth: ChatGroundTruth | None = None
    saved_result: SavedExperiment | None = None
    experiments: list[ChatExperiment] = Field(default_factory=list, max_length=200)
    # Legacy clients only. New clients send the searchable full conversation.
    analysis_history: list[HistoricalAnalysis] = Field(default_factory=list, max_length=4)
    analysis_history_total: int = Field(default=0, ge=0, le=10000)
    analysis_history_requested: bool = False
    conversation_messages: list[ConversationMessage] = Field(default_factory=list, max_length=10000)
    conversation_retrieval_requested: bool = False

    @model_validator(mode="before")
    @classmethod
    def legacy_saved_options(cls, value):
        if isinstance(value, dict) and value.get('saved_result') and isinstance(value.get('options'), dict):
            return {**value, 'options': legacy_pwm_options(value['options'])}
        return value

    @model_validator(mode="after")
    def truth_matches_result(self):
        if self.diagnostic_reference and (not self.enable_diagnostic_controls or self.diagnostic_reference.result_id != self.result_id):
            raise ValueError('Reference must belong to the selected diagnostic result.')
        if self.saved_result:
            if self.identification_enabled or self.saved_result.id != self.result_id:
                raise ValueError("Saved result analysis must be read-only and match the result ID.")
            snapshot = self.saved_result.result
            if snapshot.get("options") != self.options.model_dump() or snapshot.get("mapping") != (self.mapping.model_dump() if self.mapping else None):
                raise ValueError("Saved result configuration and mapping must match the selected result.")
        if len({m.id for m in self.conversation_messages}) != len(self.conversation_messages):
            raise ValueError("Conversation message IDs must be unique.")
        if sum(len(m.content) for m in self.conversation_messages) > 8000000:
            raise ValueError("Conversation retrieval exceeds the 8 million character request limit.")
        if self.ground_truth and self.ground_truth.result_id != self.result_id:
            raise ValueError("Ground truth must belong to the requested result.")
        return self
