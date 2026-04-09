import re
import threading
from datetime import datetime
from typing import Dict, List, Literal, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_serializer, model_validator


# ---------------------------------------------------------------------------
# Alert (input)
# ---------------------------------------------------------------------------

class Alert(BaseModel):
    id: str
    severity: Literal["critical", "warning", "info"]
    service: str
    group: str
    description: Optional[str] = None
    timestamp: AwareDatetime  # rejects naive datetimes and invalid ISO 8601
    labels: Dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# RouteConfig (input)
# ---------------------------------------------------------------------------

class ActiveHours(BaseModel):
    timezone: str
    start: str
    end: str

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, KeyError, ValueError):
            raise ValueError(f"Invalid IANA timezone: {v!r}")
        return v

    @field_validator("start", "end")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        # Regex enforces exactly HH:MM (leading zero required); strptime validates the values
        if not re.match(r"^\d{2}:\d{2}$", v):
            raise ValueError(f"Time must be in HH:MM format, got {v!r}")
        try:
            datetime.strptime(v, "%H:%M")
        except ValueError:
            raise ValueError(f"Time must be in HH:MM format, got {v!r}")
        return v


class Target(BaseModel):
    type: Literal["slack", "email", "pagerduty", "webhook"]
    channel: Optional[str] = None       # slack
    address: Optional[str] = None       # email
    service_key: Optional[str] = None   # pagerduty
    url: Optional[str] = None           # webhook
    headers: Optional[Dict[str, str]] = None  # webhook, optional

    @model_validator(mode="after")
    def validate_type_specific_fields(self) -> "Target":
        if self.type == "slack" and not self.channel:
            raise ValueError("slack target requires 'channel'")
        if self.type == "email" and not self.address:
            raise ValueError("email target requires 'address'")
        if self.type == "pagerduty" and not self.service_key:
            raise ValueError("pagerduty target requires 'service_key'")
        if self.type == "webhook" and not self.url:
            raise ValueError("webhook target requires 'url'")
        return self

    @model_serializer(mode="wrap")
    def serialize_without_nones(self, handler) -> Dict:
        return {k: v for k, v in handler(self).items() if v is not None}


_VALID_SEVERITIES = {"critical", "warning", "info"}


class Conditions(BaseModel):
    severity: Optional[List[str]] = None
    service: Optional[List[str]] = None   # supports glob patterns
    group: Optional[List[str]] = None
    labels: Optional[Dict[str, str]] = None

    @field_validator("severity")
    @classmethod
    def validate_severity_values(cls, v):
        if v is not None:
            invalid = [s for s in v if s not in _VALID_SEVERITIES]
            if invalid:
                raise ValueError(
                    f"Invalid severity value(s): {invalid}. Must be one of: critical, warning, info"
                )
        return v


class RouteConfig(BaseModel):
    id: str
    conditions: Conditions
    target: Target
    priority: int
    suppression_window_seconds: int = 0
    active_hours: Optional[ActiveHours] = None

    @field_validator("priority", mode="before")
    @classmethod
    def validate_priority_is_strict_int(cls, v) -> int:
        # Reject floats (e.g. 10.0) and booleans — both pass isinstance(v, int) in Python
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("priority must be an integer")
        return v

    @field_validator("suppression_window_seconds")
    @classmethod
    def validate_suppression_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("suppression_window_seconds must be non-negative")
        return v


# ---------------------------------------------------------------------------
# AlertResult (stored + response)
# ---------------------------------------------------------------------------

class RoutedTo(BaseModel):
    route_id: str
    target: Target


class EvaluationDetails(BaseModel):
    total_routes_evaluated: int
    routes_matched: int
    routes_not_matched: int
    suppression_applied: bool


class AlertResult(BaseModel):
    alert_id: str
    routed_to: Optional[RoutedTo]
    suppressed: bool
    suppression_reason: Optional[str] = None
    matched_routes: List[str]
    evaluation_details: EvaluationDetails


# ---------------------------------------------------------------------------
# Stats (in-memory counters)
# ---------------------------------------------------------------------------

class RouteStats(BaseModel):
    total_matched: int = 0
    total_routed: int = 0
    total_suppressed: int = 0


class Stats(BaseModel):
    total_alerts_processed: int = 0
    total_routed: int = 0
    total_suppressed: int = 0
    total_unrouted: int = 0
    by_severity: Dict[str, int] = Field(
        default_factory=lambda: {"critical": 0, "warning": 0, "info": 0}
    )
    by_route: Dict[str, RouteStats] = Field(default_factory=dict)
    by_service: Dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# AppState (in-memory store)
# ---------------------------------------------------------------------------

class AppState:
    def __init__(self) -> None:
        self.routes: Dict[str, RouteConfig] = {}
        self.alerts: Dict[str, AlertResult] = {}
        self.alert_inputs: Dict[str, "Alert"] = {}
        # (route_id, service) -> expiry datetime (UTC, timezone-aware)
        self.suppression_windows: Dict[Tuple[str, str], datetime] = {}
        self.stats: Stats = Stats()
        self._lock: threading.Lock = threading.Lock()

    def reset(self) -> None:
        self.routes.clear()
        self.alerts.clear()
        self.alert_inputs.clear()
        self.suppression_windows.clear()
        self.stats = Stats()
