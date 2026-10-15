from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


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


class FitOptions(StrictModel):
    vin: float = Field(default=28, gt=0, le=2000)
    frequency_hz: float = Field(default=100000, ge=100, le=2000000)
    start_s: float | None = None
    end_s: float | None = None
    train_fraction: float = Field(default=0.7, ge=0.5, le=0.85)
    mode: Literal["LC", "LCR"] = "LCR"
    resistance_ohm: float = Field(default=6, gt=0.01, le=100000)
    method: Literal["integral", "pann"] = "integral"
    epochs: int = Field(default=60, ge=10, le=150)


class FitRequest(AnalysisRequest):
    options: FitOptions = Field(default_factory=FitOptions)


class ChatMessage(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatRequest(StrictModel):
    identification_enabled: bool = False
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=12)
    dataset_id: str | None = None
    mapping: Mapping | None = None
    options: FitOptions = Field(default_factory=FitOptions)
    result_id: str | None = None
