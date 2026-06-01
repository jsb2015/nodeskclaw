"""File storage service with S3-compatible object storage and local filesystem backends."""

import asyncio
import hashlib
import hmac
import logging
import os
import tempfile
import time
import uuid
from collections.abc import AsyncIterable
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig

from app.core.config import settings

logger = logging.getLogger(__name__)

_client = None


class StorageUnavailableError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class UploadTooLargeError(ValueError):
    def __init__(self, limit_bytes: int, actual_bytes: int) -> None:
        super().__init__("upload too large")
        self.limit_bytes = limit_bytes
        self.actual_bytes = actual_bytes


def _storage_intent() -> str:
    intent = (settings.UPLOAD_STORAGE_BACKEND or "auto").lower()
    return intent if intent in {"auto", "local", "s3"} else "auto"


def _s3_required_values() -> list[str]:
    return [
        settings.S3_ENDPOINT,
        settings.S3_BUCKET,
        settings.S3_ACCESS_KEY_ID,
        settings.S3_SECRET_ACCESS_KEY,
    ]


def _s3_any_configured() -> bool:
    return any(bool(value) for value in _s3_required_values())


def _s3_config_complete() -> bool:
    return all(bool(value) for value in _s3_required_values())


def _target_backend() -> str:
    intent = _storage_intent()
    if intent == "local":
        return "local"
    if intent == "s3":
        return "s3"
    return "s3" if _s3_any_configured() else "local"


def _use_s3() -> bool:
    return _target_backend() == "s3" and _s3_config_complete()


def is_configured() -> bool:
    return get_storage_status()["storage_status"] == "available"


def get_storage_status() -> dict[str, str | bool]:
    backend = _target_backend()
    if backend == "s3":
        if not _s3_config_complete():
            return {
                "backend": "s3",
                "storage_status": "unavailable",
                "storage_reason_code": "s3_config_incomplete",
                "direct_upload_supported": False,
            }
        return {
            "backend": "s3",
            "storage_status": "available",
            "storage_reason_code": "",
            "direct_upload_supported": False,
        }

    local_dir = _get_local_dir()
    parent = local_dir if local_dir.exists() else local_dir.parent
    if parent.exists() and os.access(parent, os.W_OK):
        return {
            "backend": "local",
            "storage_status": "available",
            "storage_reason_code": "",
            "direct_upload_supported": False,
        }
    return {
        "backend": "local",
        "storage_status": "unavailable",
        "storage_reason_code": "local_storage_unwritable",
        "direct_upload_supported": False,
    }


def _ensure_storage_available() -> None:
    status = get_storage_status()
    if status["storage_status"] != "available":
        raise StorageUnavailableError(str(status["storage_reason_code"]))


def _get_local_dir() -> Path:
    if settings.LOCAL_STORAGE_DIR:
        return Path(settings.LOCAL_STORAGE_DIR)
    docker_data = os.environ.get("DOCKER_DATA_DIR")
    if docker_data:
        return Path(docker_data) / "shared-files"
    return Path.home() / ".nodeskclaw" / "shared-files"


def _safe_filename(filename: str) -> str:
    clean = filename.replace("\\", "/").split("/")[-1].strip()
    clean = "".join(ch for ch in clean if ch >= " " and ch != "\x7f")
    return clean or "unnamed"


def _build_object_key(workspace_id: str, filename: str, *, include_prefix: bool) -> str:
    safe_name = _safe_filename(filename)
    base = f"workspace-files/{workspace_id}/{uuid.uuid4().hex}/{safe_name}"
    if not include_prefix:
        return base
    prefix = settings.S3_KEY_PREFIX.strip("/")
    return f"{prefix}/{base}" if prefix else base


def _sign_url(key: str, expires_at: int) -> str:
    payload = f"{key}{expires_at}"
    sig = hmac.new(
        settings.JWT_SECRET.encode(), payload.encode(), hashlib.sha256,
    ).hexdigest()
    return sig


def verify_signature(key: str, expires_str: str, sig: str) -> bool:
    try:
        expires_at = int(expires_str)
    except (ValueError, TypeError):
        return False
    if time.time() > expires_at:
        return False
    expected = _sign_url(key, expires_at)
    return hmac.compare_digest(expected, sig)


# ── S3 backend ──────────────────────────────────────────

def _get_s3_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT,
            region_name=settings.S3_REGION or None,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            config=BotoConfig(signature_version="s3v4"),
        )
    return _client


def _s3_upload(file_content: bytes, filename: str, content_type: str, workspace_id: str) -> str:
    client = _get_s3_client()
    key = _build_object_key(workspace_id, filename, include_prefix=True)
    client.put_object(
        Bucket=settings.S3_BUCKET,
        Key=key,
        Body=file_content,
        ContentType=content_type,
    )
    return key


def _s3_presigned_url(key: str, expires: int = 3600) -> str:
    client = _get_s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.S3_BUCKET, "Key": key},
        ExpiresIn=expires,
    )


def _s3_download(key: str) -> bytes:
    client = _get_s3_client()
    resp = client.get_object(Bucket=settings.S3_BUCKET, Key=key)
    return resp["Body"].read()


def _s3_delete(key: str) -> None:
    client = _get_s3_client()
    client.delete_object(Bucket=settings.S3_BUCKET, Key=key)


# ── Local filesystem backend ─────────────────────────────

def _local_upload(file_content: bytes, filename: str, _content_type: str, workspace_id: str) -> str:
    base = _build_object_key(workspace_id, filename, include_prefix=False)
    local_dir = _get_local_dir()
    file_path = local_dir / base
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(file_content)
    return base


def _local_presigned_url(key: str, expires: int = 3600) -> str:
    expires_at = int(time.time()) + expires
    sig = _sign_url(key, expires_at)
    return f"/api/v1/files/local/{key}?expires={expires_at}&sig={sig}"


def _local_download(key: str) -> bytes:
    file_path = _get_local_dir() / key
    return file_path.read_bytes()


def _local_delete(key: str) -> None:
    file_path = _get_local_dir() / key
    try:
        file_path.unlink()
    except FileNotFoundError:
        pass


# ── Public async API ─────────────────────────────────────

async def upload_file(file_content: bytes, filename: str, content_type: str, workspace_id: str) -> str:
    _ensure_storage_available()
    if _use_s3():
        return await asyncio.to_thread(_s3_upload, file_content, filename, content_type, workspace_id)
    return await asyncio.to_thread(_local_upload, file_content, filename, content_type, workspace_id)


async def upload_stream(
    chunks: AsyncIterable[bytes],
    filename: str,
    content_type: str,
    workspace_id: str,
    *,
    max_bytes: int | None = None,
) -> tuple[str, int, str]:
    _ensure_storage_available()
    if _use_s3():
        return await _s3_upload_stream(chunks, filename, content_type, workspace_id, max_bytes=max_bytes)
    return await _local_upload_stream(chunks, filename, content_type, workspace_id, max_bytes=max_bytes)


async def upload_file_object(
    file_obj,
    filename: str,
    content_type: str,
    workspace_id: str,
    *,
    max_bytes: int | None = None,
    chunk_size: int = 1024 * 1024,
) -> tuple[str, int, str]:
    async def _chunks():
        while True:
            chunk = await file_obj.read(chunk_size)
            if not chunk:
                break
            yield chunk

    return await upload_stream(
        _chunks(),
        filename,
        content_type,
        workspace_id,
        max_bytes=max_bytes,
    )


def _s3_put_fileobj(file_obj, key: str, content_type: str) -> None:
    client = _get_s3_client()
    client.put_object(
        Bucket=settings.S3_BUCKET,
        Key=key,
        Body=file_obj,
        ContentType=content_type,
    )


async def _s3_upload_stream(
    chunks: AsyncIterable[bytes],
    filename: str,
    content_type: str,
    workspace_id: str,
    *,
    max_bytes: int | None,
) -> tuple[str, int, str]:
    key = _build_object_key(workspace_id, filename, include_prefix=True)
    digest = hashlib.sha256()
    total = 0
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as tmp:
        async for chunk in chunks:
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise UploadTooLargeError(max_bytes, total)
            digest.update(chunk)
            tmp.write(chunk)
        tmp.seek(0)
        await asyncio.to_thread(_s3_put_fileobj, tmp, key, content_type)
    return key, total, digest.hexdigest()


async def _local_upload_stream(
    chunks: AsyncIterable[bytes],
    filename: str,
    _content_type: str,
    workspace_id: str,
    *,
    max_bytes: int | None,
) -> tuple[str, int, str]:
    key = _build_object_key(workspace_id, filename, include_prefix=False)
    file_path = _get_local_dir() / key
    file_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    try:
        with file_path.open("wb") as fh:
            async for chunk in chunks:
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    raise UploadTooLargeError(max_bytes, total)
                digest.update(chunk)
                fh.write(chunk)
    except Exception:
        try:
            file_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return key, total, digest.hexdigest()


async def get_presigned_url(key: str, expires: int = 3600) -> str:
    _ensure_storage_available()
    if _use_s3():
        return await asyncio.to_thread(_s3_presigned_url, key, expires)
    return _local_presigned_url(key, expires)


async def download_file(key: str) -> bytes:
    _ensure_storage_available()
    if _use_s3():
        return await asyncio.to_thread(_s3_download, key)
    return await asyncio.to_thread(_local_download, key)


async def delete_file(key: str) -> None:
    _ensure_storage_available()
    if _use_s3():
        await asyncio.to_thread(_s3_delete, key)
    else:
        await asyncio.to_thread(_local_delete, key)


# ── Raw key API (for backup storage) ─────────────────────

def _s3_upload_raw(key: str, data: bytes) -> None:
    client = _get_s3_client()
    prefix = settings.S3_KEY_PREFIX.strip("/")
    full_key = f"{prefix}/{key}" if prefix else key
    client.put_object(Bucket=settings.S3_BUCKET, Key=full_key, Body=data)


def _local_upload_raw(key: str, data: bytes) -> None:
    file_path = _get_local_dir() / key
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(data)


async def upload_raw(key: str, data: bytes) -> None:
    """Upload raw bytes to a specific storage key."""
    if _use_s3():
        await asyncio.to_thread(_s3_upload_raw, key, data)
    else:
        await asyncio.to_thread(_local_upload_raw, key, data)


def _s3_download_raw(key: str) -> bytes:
    client = _get_s3_client()
    prefix = settings.S3_KEY_PREFIX.strip("/")
    full_key = f"{prefix}/{key}" if prefix else key
    resp = client.get_object(Bucket=settings.S3_BUCKET, Key=full_key)
    return resp["Body"].read()


def _local_download_raw(key: str) -> bytes:
    file_path = _get_local_dir() / key
    return file_path.read_bytes()


async def download_raw(key: str) -> bytes:
    """Download raw bytes from a specific storage key."""
    if _use_s3():
        return await asyncio.to_thread(_s3_download_raw, key)
    return await asyncio.to_thread(_local_download_raw, key)


def _s3_delete_raw(key: str) -> None:
    client = _get_s3_client()
    prefix = settings.S3_KEY_PREFIX.strip("/")
    full_key = f"{prefix}/{key}" if prefix else key
    client.delete_object(Bucket=settings.S3_BUCKET, Key=full_key)


def _local_delete_raw(key: str) -> None:
    file_path = _get_local_dir() / key
    try:
        file_path.unlink()
    except FileNotFoundError:
        pass


async def delete_raw(key: str) -> None:
    """Delete an object by specific storage key."""
    if _use_s3():
        await asyncio.to_thread(_s3_delete_raw, key)
    else:
        await asyncio.to_thread(_local_delete_raw, key)
