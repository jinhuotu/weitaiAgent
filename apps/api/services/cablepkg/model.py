"""Cable-only data contracts; no persistence or external service dependencies."""

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Literal


class EvidenceLevel(str, Enum):
    """L0/L1 are evidence; L2–L4 alone cannot establish bid eligibility."""

    L0 = "L0"  # Real report with report number and page.
    L1 = "L1"  # Enterprise type test / product master data.
    L2 = "L2"  # Standard limit / copied requirement.
    L3 = "L3"  # Structural calculation.
    L4 = "L4"  # Example placeholder.

    def to_dict(self) -> dict[str, str]:
        return {"value": self.value}


Operator = Literal["ge", "le", "range", "eq", "enum", "text", "empty", "na", "bidder_supplied"]


@dataclass
class Requirement:
    raw: str = ""
    operator: Operator = "empty"
    value: Any = None
    unit_condition: str | None = None
    is_key: bool = False
    needs_report: bool = False
    bidder_supplied: bool = False
    seq: str = ""
    name: str = ""
    unit: str = ""
    qualifier: str | None = None
    solidified_id: str = ""

    def __post_init__(self) -> None:
        self.is_key = self.is_key or "※" in self.name
        self.needs_report = self.needs_report or "△" in self.name
        self.name = self.name.replace("※", "").replace("△", "").strip()
        self.bidder_supplied = self.bidder_supplied or self.operator == "bidder_supplied"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Fact:
    """A value with explicit provenance, defaulting to placeholder evidence."""

    value: Any = None
    unit: str = ""
    evidence_level: EvidenceLevel = EvidenceLevel.L4
    basis: str = ""
    report_number: str | None = None
    page: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["evidence_level"] = self.evidence_level.value
        return result


@dataclass
class Response:
    """A proposed guarantee; eligibility is reserved for the later gate module."""

    seq: str = ""
    name: str = ""
    guarantee_value: Any = None
    unit: str = ""
    evidence_level: EvidenceLevel = EvidenceLevel.L4
    basis: str = ""
    solidified_id: str = ""
    is_key: bool = False
    bidder_supplied: bool = False
    operator: Operator = "text"

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["evidence_level"] = self.evidence_level.value
        return result
