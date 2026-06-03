from types import SimpleNamespace

import pytest

from app.core.config import get_agent_file_download_base_url, settings
from app.services import storage_service
from app.core.exceptions import BadRequestError
from app.services import upload_policy_service
from app.services.upload_policy_service import build_upload_policy


def test_agent_file_download_base_url_normalizes_api_prefix() -> None:
    cfg = SimpleNamespace(
        AGENT_FILE_DOWNLOAD_BASE_URL="https://agent-download.example.com/api/v1/",
        AGENT_API_BASE_URL="https://backend.example.com/api/v1",
    )

    assert get_agent_file_download_base_url(cfg) == "https://agent-download.example.com/api/v1"


def test_agent_file_download_base_url_falls_back_to_agent_api_base_url() -> None:
    cfg = SimpleNamespace(
        AGENT_FILE_DOWNLOAD_BASE_URL="",
        AGENT_API_BASE_URL="https://backend.example.com",
    )

    assert get_agent_file_download_base_url(cfg) == "https://backend.example.com/api/v1"


def test_storage_status_does_not_fallback_to_local_for_partial_s3(monkeypatch) -> None:
    monkeypatch.setattr(storage_service.settings, "UPLOAD_STORAGE_BACKEND", "auto")
    monkeypatch.setattr(storage_service.settings, "S3_ENDPOINT", "https://s3.example.com")
    monkeypatch.setattr(storage_service.settings, "S3_BUCKET", "")
    monkeypatch.setattr(storage_service.settings, "S3_ACCESS_KEY_ID", "")
    monkeypatch.setattr(storage_service.settings, "S3_SECRET_ACCESS_KEY", "")

    status = storage_service.get_storage_status()

    assert status["backend"] == "s3"
    assert status["storage_status"] == "unavailable"
    assert status["storage_reason_code"] == "s3_config_incomplete"


def test_storage_status_reports_s3_direct_upload_when_health_and_cors_pass(monkeypatch) -> None:
    class FakeS3Client:
        def head_bucket(self, **_kwargs):
            return {}

        def put_object(self, **_kwargs):
            return {}

        def delete_object(self, **_kwargs):
            return {}

        def create_multipart_upload(self, **_kwargs):
            return {"UploadId": "upload-id"}

        def abort_multipart_upload(self, **_kwargs):
            return {}

        def get_bucket_cors(self, **_kwargs):
            return {
                "CORSRules": [{
                    "AllowedOrigins": ["http://localhost:4517", "http://localhost:4518"],
                    "AllowedMethods": ["PUT", "POST"],
                    "AllowedHeaders": ["*"],
                    "ExposeHeaders": ["ETag"],
                }]
            }

    monkeypatch.setattr(storage_service.settings, "UPLOAD_STORAGE_BACKEND", "s3")
    monkeypatch.setattr(storage_service.settings, "S3_ENDPOINT", "https://s3.example.com")
    monkeypatch.setattr(storage_service.settings, "S3_BUCKET", "bucket")
    monkeypatch.setattr(storage_service.settings, "S3_ACCESS_KEY_ID", "access-key")
    monkeypatch.setattr(storage_service.settings, "S3_SECRET_ACCESS_KEY", "secret-key")
    monkeypatch.setattr(storage_service.settings, "CORS_ORIGINS", ["http://localhost:4517", "http://localhost:4518"])
    monkeypatch.setattr(storage_service, "_get_s3_client", lambda: FakeS3Client())
    storage_service.reset_storage_status_cache()

    status = storage_service.get_storage_status(force_refresh=True)

    assert status["backend"] == "s3"
    assert status["storage_status"] == "available"
    assert status["storage_reason_code"] == ""
    assert status["direct_upload_supported"] is True


def test_storage_status_keeps_s3_available_when_cors_blocks_direct_upload(monkeypatch) -> None:
    class FakeS3Client:
        def head_bucket(self, **_kwargs):
            return {}

        def put_object(self, **_kwargs):
            return {}

        def delete_object(self, **_kwargs):
            return {}

        def create_multipart_upload(self, **_kwargs):
            return {"UploadId": "upload-id"}

        def abort_multipart_upload(self, **_kwargs):
            return {}

        def get_bucket_cors(self, **_kwargs):
            return {"CORSRules": []}

    monkeypatch.setattr(storage_service.settings, "UPLOAD_STORAGE_BACKEND", "s3")
    monkeypatch.setattr(storage_service.settings, "S3_ENDPOINT", "https://s3.example.com")
    monkeypatch.setattr(storage_service.settings, "S3_BUCKET", "bucket")
    monkeypatch.setattr(storage_service.settings, "S3_ACCESS_KEY_ID", "access-key")
    monkeypatch.setattr(storage_service.settings, "S3_SECRET_ACCESS_KEY", "secret-key")
    monkeypatch.setattr(storage_service.settings, "CORS_ORIGINS", ["http://localhost:4517"])
    monkeypatch.setattr(storage_service, "_get_s3_client", lambda: FakeS3Client())
    storage_service.reset_storage_status_cache()

    status = storage_service.get_storage_status(force_refresh=True)

    assert status["backend"] == "s3"
    assert status["storage_status"] == "available"
    assert status["storage_reason_code"] == "s3_cors_direct_upload_unavailable"
    assert status["direct_upload_supported"] is False


@pytest.mark.asyncio
async def test_upload_policy_exposes_surface_limits(monkeypatch) -> None:
    monkeypatch.setattr(storage_service, "get_storage_status", lambda: {
        "backend": "local",
        "storage_status": "available",
        "storage_reason_code": "",
        "direct_upload_supported": False,
    })

    policy = await build_upload_policy(None)

    assert policy["surfaces"]["chat_attachment"]["max_file_size_bytes"] == (
        settings.UPLOAD_CHAT_ATTACHMENT_MAX_MB * 1024 * 1024
    )
    assert policy["surfaces"]["chat_attachment"]["recommended_alternative"] == "shared_file"
    assert policy["gateway"]["proxy_body_size_bytes"] == (
        settings.UPLOAD_GATEWAY_PROXY_BODY_SIZE_MB * 1024 * 1024
    )


@pytest.mark.asyncio
async def test_async_scan_config_cannot_disable_scanner(monkeypatch) -> None:
    async def get_config(key, _db):
        values = {
            "upload_security_scan_mode": "async_required",
            "upload_scanner_provider": "http",
            "upload_scanner_endpoint": "http://scanner.local/scan",
        }
        return values.get(key)

    monkeypatch.setattr(upload_policy_service.config_service, "get_config", get_config)

    with pytest.raises(BadRequestError) as exc:
        await upload_policy_service.validate_upload_config_value(
            "upload_scanner_provider",
            "none",
            object(),
        )

    assert exc.value.message_key == "errors.upload.scanner_unavailable"
