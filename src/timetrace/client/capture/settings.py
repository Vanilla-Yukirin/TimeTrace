"""Validated settings for the standalone screenshot recorder."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from timetrace.common.config import CaptureConfig

Theme = Literal["system", "light", "dark"]


class ScreenshotSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    strategy: Literal["smart", "interval"] = "smart"
    interval_s: float = Field(default=30, ge=1, le=86400)
    switch_capture_delay_s: float = Field(default=CaptureConfig.switch_capture_delay_s, ge=0, le=60)
    min_capture_interval_s: float = Field(
        default=CaptureConfig.min_capture_interval_s, ge=0, le=300
    )
    max_capture_interval_s: float = Field(
        default=CaptureConfig.max_capture_interval_s, gt=0, le=300
    )
    idle_threshold_s: float = Field(default=CaptureConfig.idle_threshold_s, ge=0)
    capture_mode: str = "active_window"  # retained for older client.toml files
    deduplicate: bool = False
    image_format: Literal["jpg", "png", "webp"] = "jpg"
    quality: int = Field(default=88, ge=1, le=100)
    smart_compression: bool = True

    @model_validator(mode="after")
    def check_intervals(self):
        if self.max_capture_interval_s < max(
            self.min_capture_interval_s, self.switch_capture_delay_s
        ):
            raise ValueError("最长间隔须大于或等于最小间隔和切窗延迟")
        return self
