"""Normalized hospital announcement sources.

This module intentionally does not connect to any hospital system. It defines
the contract that an authorized integration would implement, plus a synthetic
adapter that is safe for demos. Real-data mode stays off until written approval
and an explicit source configuration are present.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

STATUSES = frozenset({
    "announced", "now_serving", "called", "missing", "delayed",
    "cancelled", "info",
})
MAX_TEXT_LENGTH = 2000
MAX_METADATA_BYTES = 8192
_LANGUAGE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")
_KIND = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class HospitalError(RuntimeError):
    """A safe client-facing hospital-source error with an HTTP status."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        raise HospitalError("Announcement text is required.")
    text = _CONTROL.sub("", value).strip()
    if not text:
        raise HospitalError("Announcement text is required.")
    if len(text) > MAX_TEXT_LENGTH:
        raise HospitalError(
            f"Announcement text exceeds {MAX_TEXT_LENGTH} characters."
        )
    return text


def _clean_language(value: Any) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise HospitalError("Language must be a string such as en-IN.")
    language = value.strip()
    if not _LANGUAGE.fullmatch(language):
        raise HospitalError("Language must look like en-IN or te.")
    return language


def _clean_status(value: Any) -> str:
    if value in (None, ""):
        return "announced"
    if not isinstance(value, str):
        raise HospitalError("Status must be a string.")
    status = value.strip().lower().replace("-", "_").replace(" ", "_")
    if status not in STATUSES:
        raise HospitalError(
            "Status must be one of: " + ", ".join(sorted(STATUSES)) + "."
        )
    return status


def _clean_kind(value: Any) -> str:
    if value in (None, ""):
        return "hospital"
    if not isinstance(value, str):
        raise HospitalError("Kind must be a string.")
    kind = value.strip().lower()
    if not _KIND.fullmatch(kind):
        raise HospitalError("Kind must be a short lowercase token.")
    return kind


def _clean_metadata(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise HospitalError("Metadata must be a JSON object.")
    try:
        serialized = json.dumps(value, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError):
        raise HospitalError("Metadata must be JSON-serializable.") from None
    if len(serialized) > MAX_METADATA_BYTES:
        raise HospitalError("Metadata is too large.")
    return dict(value)


@dataclass(frozen=True)
class Announcement:
    source_id: str
    source_type: str
    source_name: str
    received_at: str
    text: str
    kind: str = "hospital"
    language: str = ""
    status: str = "announced"
    real_data: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        source_id: str,
        source_type: str,
        source_name: str,
        real_data: bool,
        default_text: str = "",
    ) -> "Announcement":
        if not isinstance(payload, dict):
            raise HospitalError("JSON object expected.")
        raw_text = payload.get("text", default_text)
        return cls(
            source_id=source_id,
            source_type=source_type,
            source_name=source_name,
            received_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            text=_clean_text(raw_text),
            kind=_clean_kind(payload.get("kind")),
            language=_clean_language(payload.get("language")),
            status=_clean_status(payload.get("status")),
            real_data=bool(real_data),
            metadata=_clean_metadata(payload.get("metadata")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "received_at": self.received_at,
            "text": self.text,
            "kind": self.kind,
            "language": self.language,
            "status": self.status,
            "real_data": self.real_data,
            "metadata": self.metadata,
        }


class HospitalAdapter:
    """Contract for turning a source payload into a normalized announcement."""

    source_id = ""
    source_type = "unknown"
    source_name = ""
    real_data = False
    requires_approval = False

    def available(self) -> bool:
        return False

    def unavailable_reason(self) -> str:
        return "This source is not configured."

    def parse(self, payload: dict[str, Any]) -> Announcement:
        raise NotImplementedError

    def public(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "available": self.available(),
            "real_data": self.real_data,
            "requires_approval": self.requires_approval,
            "reason": None if self.available() else self.unavailable_reason(),
        }


class SyntheticAdapter(HospitalAdapter):
    """Always-available, clearly labelled demo source with no hospital data."""

    source_id = "synthetic-demo"
    source_type = "synthetic"
    source_name = "Synthetic demonstration announcement"
    real_data = False
    requires_approval = False
    DEFAULT_TEXT = "Synthetic demo announcement for token 47 at Counter 3."

    def available(self) -> bool:
        return True

    def parse(self, payload: dict[str, Any]) -> Announcement:
        return Announcement.from_payload(
            payload,
            source_id=self.source_id,
            source_type=self.source_type,
            source_name=self.source_name,
            real_data=False,
            default_text=self.DEFAULT_TEXT,
        )


class AuthorizedTextAdapter(HospitalAdapter):
    """A gated contract for text supplied by an authorized hospital source.

    No network connection is made. The route accepts a payload and normalizes
    it; persistence, polling, and hospital-system authentication are out of
    scope until the hospital provides authorized integration details.
    """

    source_type = "authorized_text"
    real_data = True
    requires_approval = True

    def __init__(
        self,
        *,
        approved: bool,
        source_id: str = "",
        source_name: str = "",
    ) -> None:
        self.approved = bool(approved)
        self.source_id = source_id.strip()
        self.source_name = source_name.strip() or "Authorized hospital source"

    def available(self) -> bool:
        return self.approved and bool(self.source_id)

    def unavailable_reason(self) -> str:
        if not self.approved:
            return (
                "Hospital data mode is not approved. "
                "Set SEVA_HOSPITAL_DATA_APPROVED=1 only after written approval."
            )
        return "No authorized hospital source is configured."

    def parse(self, payload: dict[str, Any]) -> Announcement:
        if not self.approved:
            raise HospitalError(
                "Hospital data mode is not approved; real announcements are disabled.",
                403,
            )
        if not self.source_id:
            raise HospitalError(
                "No authorized hospital source is configured.", 503
            )
        return Announcement.from_payload(
            payload,
            source_id=self.source_id,
            source_type=self.source_type,
            source_name=self.source_name,
            real_data=True,
        )


def build_adapters(
    approved: bool,
    *,
    source_id: str = "",
    source_name: str = "",
) -> list[HospitalAdapter]:
    return [
        SyntheticAdapter(),
        AuthorizedTextAdapter(
            approved=approved,
            source_id=source_id,
            source_name=source_name,
        ),
    ]


def find_adapter(
    adapters: list[HospitalAdapter], source_id: str
) -> HospitalAdapter | None:
    for adapter in adapters:
        if adapter.source_id and adapter.source_id == source_id:
            return adapter
    return None
