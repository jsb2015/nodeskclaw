"""Upload policy service shared by API, portal, and file services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services import config_service
from app.services import storage_service


UPLOAD_CONFIG_KEYS = {
    "upload_chat_attachment_max_mb",
    "upload_shared_file_max_mb",
    "upload_chunked_upload_threshold_mb",
    "upload_workspace_quota_mb",
    "upload_gateway_proxy_body_size_mb",
    "upload_proxy_read_timeout_seconds",
    "upload_proxy_send_timeout_seconds",
    "upload_security_scan_mode",
}


@dataclass(frozen=True)
class SurfacePolicy:
    enabled: bool
    max_file_size_bytes: int
    allowed_content_types: list[str]
    blocked_extensions: list[str]
    retention_days: int | None = None
    max_files_per_message: int | None = None
    recommended_alternative: str | None = None
    chunked_upload_threshold_bytes: int | None = None
    max_workspace_total_bytes: int | None = None
    remaining_workspace_bytes: int | None = None
    chunk_size_bytes: int | None = None
    session_ttl_minutes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "enabled": self.enabled,
                "max_file_size_bytes": self.max_file_size_bytes,
                "max_files_per_message": self.max_files_per_message,
                "retention_days": self.retention_days,
                "allowed_content_types": self.allowed_content_types,
                "blocked_extensions": self.blocked_extensions,
                "recommended_alternative": self.recommended_alternative,
                "chunked_upload_threshold_bytes": self.chunked_upload_threshold_bytes,
                "max_workspace_total_bytes": self.max_workspace_total_bytes,
                "remaining_workspace_bytes": self.remaining_workspace_bytes,
                "chunk_size_bytes": self.chunk_size_bytes,
                "session_ttl_minutes": self.session_ttl_minutes,
            }.items()
            if value is not None
        }


def _mb(value: int) -> int:
    return value * 1024 * 1024


def _parse_int(raw: str | None, default: int) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


async def _effective_int(db: AsyncSession | None, key: str, default: int) -> int:
    if db is None:
        return default
    return _parse_int(await config_service.get_config(key, db), default)


async def _effective_str(db: AsyncSession | None, key: str, default: str) -> str:
    if db is None:
        return default
    value = await config_service.get_config(key, db)
    return value if value is not None else default


async def build_upload_policy(db: AsyncSession | None = None) -> dict[str, Any]:
    chat_max_mb = await _effective_int(
        db, "upload_chat_attachment_max_mb", settings.UPLOAD_CHAT_ATTACHMENT_MAX_MB,
    )
    shared_max_mb = await _effective_int(
        db, "upload_shared_file_max_mb", settings.UPLOAD_SHARED_FILE_MAX_MB,
    )
    chunk_threshold_mb = await _effective_int(
        db, "upload_chunked_upload_threshold_mb", settings.UPLOAD_CHUNKED_UPLOAD_THRESHOLD_MB,
    )
    quota_mb = await _effective_int(
        db, "upload_workspace_quota_mb", settings.UPLOAD_WORKSPACE_QUOTA_MB,
    )
    gateway_mb = await _effective_int(
        db, "upload_gateway_proxy_body_size_mb", settings.UPLOAD_GATEWAY_PROXY_BODY_SIZE_MB,
    )
    scan_mode = await _effective_str(
        db, "upload_security_scan_mode", settings.UPLOAD_SECURITY_SCAN_MODE,
    )

    storage_status = storage_service.get_storage_status()
    blocked_extensions = [".exe", ".bat", ".cmd", ".sh"]
    chat_max_bytes = _mb(chat_max_mb)
    shared_max_bytes = _mb(shared_max_mb)
    chunk_threshold_bytes = _mb(chunk_threshold_mb)
    quota_bytes = _mb(quota_mb)
    gateway_bytes = _mb(gateway_mb)

    return {
        "backend": storage_status["backend"],
        "storage_status": storage_status["storage_status"],
        "storage_reason_code": storage_status["storage_reason_code"],
        "direct_upload_supported": storage_status["direct_upload_supported"],
        "surfaces": {
            "chat_attachment": SurfacePolicy(
                enabled=storage_status["storage_status"] == "available",
                max_file_size_bytes=chat_max_bytes,
                max_files_per_message=5,
                retention_days=90,
                allowed_content_types=[],
                blocked_extensions=blocked_extensions,
                recommended_alternative="shared_file",
            ).to_dict(),
            "shared_file": SurfacePolicy(
                enabled=storage_status["storage_status"] == "available",
                max_file_size_bytes=shared_max_bytes,
                chunked_upload_threshold_bytes=chunk_threshold_bytes,
                max_workspace_total_bytes=quota_bytes,
                remaining_workspace_bytes=quota_bytes,
                allowed_content_types=[],
                blocked_extensions=blocked_extensions,
            ).to_dict(),
            "large_input": SurfacePolicy(
                enabled=storage_status["storage_status"] == "available",
                max_file_size_bytes=max(shared_max_bytes, _mb(2048)),
                chunk_size_bytes=8 * 1024 * 1024,
                session_ttl_minutes=120,
                allowed_content_types=[],
                blocked_extensions=blocked_extensions,
            ).to_dict(),
        },
        "gateway": {
            "proxy_body_size_bytes": gateway_bytes,
            "is_gateway_lower_than_policy": gateway_bytes < max(chat_max_bytes, chunk_threshold_bytes),
        },
        "security": {
            "scan_mode": scan_mode,
            "download_requires_clean_scan": scan_mode == "async_required",
            "scanner_configured": (
                settings.UPLOAD_SCANNER_PROVIDER != "none"
                and bool(settings.UPLOAD_SCANNER_ENDPOINT)
            ),
        },
    }


async def get_surface_max_bytes(surface: str, db: AsyncSession | None = None) -> int:
    policy = await build_upload_policy(db)
    return int(policy["surfaces"][surface]["max_file_size_bytes"])
