from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import upload_session_service
from app.schemas.upload import UploadCompleteRequest, UploadSessionCreateRequest


class _ScalarResult:
    def __init__(self, value) -> None:
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value


class _ScalarsResult:
    def __init__(self, values: list) -> None:
        self._values = values

    def scalars(self):
        return self

    def all(self) -> list:
        return self._values


class _SequenceDb:
    def __init__(self, results: list) -> None:
        self._results = list(results)
        self.added: list = []
        self.flushed = 0
        self.committed = False
        self.refreshed: list = []

    async def execute(self, _stmt):
        return self._results.pop(0)

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed += 1
        for index, obj in enumerate(self.added, 1):
            if not getattr(obj, "id", None):
                obj.id = f"generated-{index}"

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, obj) -> None:
        self.refreshed.append(obj)


def _policy() -> dict:
    return {
        "backend": "local",
        "storage_status": "available",
        "storage_reason_code": "",
        "direct_upload_supported": False,
        "surfaces": {
            "chat_attachment": {"max_file_size_bytes": 20 * 1024 * 1024},
            "shared_file": {
                "max_file_size_bytes": 200 * 1024 * 1024,
                "max_workspace_total_bytes": 1024 * 1024 * 1024,
            },
            "large_input": {
                "max_file_size_bytes": 2 * 1024 * 1024 * 1024,
                "chunk_size_bytes": 8 * 1024 * 1024,
            },
        },
        "security": {
            "scan_mode": "metadata_only",
            "scanner_configured": False,
        },
    }


@pytest.mark.asyncio
async def test_create_upload_session_reserves_quota(monkeypatch) -> None:
    async def validate_upload_request(*_args, **_kwargs) -> None:
        return None

    async def build_upload_policy(_db):
        return _policy()

    monkeypatch.setattr(upload_session_service, "validate_upload_request", validate_upload_request)
    monkeypatch.setattr(upload_session_service, "build_upload_policy", build_upload_policy)
    db = _SequenceDb([
        _ScalarResult(None),
        _ScalarResult(None),
        _ScalarResult("ws-1"),
        _ScalarResult(0),
        _ScalarResult(0),
        _ScalarResult(0),
        _ScalarResult(0),
    ])

    session = await upload_session_service.create_upload_session(
        db,
        workspace_id="ws-1",
        uploader_type="user",
        uploader_id="user-1",
        uploader_name="User",
        data=UploadSessionCreateRequest(
            surface="shared_file",
            filename="report.pdf",
            content_type="application/pdf",
            expected_size=1024,
            client_request_id="request-1",
        ),
    )

    reservation = db.added[1]
    assert session.id == "generated-1"
    assert session.quota_reservation_id == reservation.id
    assert reservation.reserved_bytes == 1024
    assert reservation.status == "active"
    assert db.committed is True


@pytest.mark.asyncio
async def test_complete_large_input_commits_reservation(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(upload_session_service.storage_service, "_get_local_dir", lambda: tmp_path)
    payload = b"hello large input"
    part_key = ".upload-parts/session-1/1.part"
    part_path = tmp_path / part_key
    part_path.parent.mkdir(parents=True)
    part_path.write_bytes(payload)
    checksum = hashlib.sha256(payload).hexdigest()
    now = datetime.now(timezone.utc)
    session = SimpleNamespace(
        id="session-1",
        workspace_id="ws-1",
        surface="large_input",
        status="uploading",
        expires_at=now + timedelta(minutes=30),
        part_count=1,
        expected_size=len(payload),
        checksum=f"sha256:{checksum}",
        effective_filename="dataset.zip",
        content_type="application/zip",
        purpose="agent_input",
        owner_type="none",
        owner_id="",
        retention_policy="expires_at",
        uploader_type="user",
        uploader_id="user-1",
        uploader_name="User",
        quota_reservation_id="reservation-1",
        storage_key="",
        received_size=0,
        completed_at=None,
    )
    part = SimpleNamespace(
        session_id="session-1",
        part_number=1,
        size=len(payload),
        checksum=f"sha256:{checksum}",
        storage_key=part_key,
        deleted_at=None,
        status="uploaded",
    )
    reservation = SimpleNamespace(
        id="reservation-1",
        status="active",
        committed_source="",
        committed_file_id="",
        released_at=None,
    )

    async def upload_stream(chunks, *_args, **_kwargs):
        content = b""
        async for chunk in chunks:
            content += chunk
        return "final-key", len(content), hashlib.sha256(content).hexdigest()

    async def delete_file(_key: str) -> None:
        return None

    async def build_upload_policy(_db):
        return _policy()

    async def get_initial_scan_state(_db):
        return "skipped", "metadata_only"

    monkeypatch.setattr(upload_session_service.storage_service, "upload_stream", upload_stream)
    monkeypatch.setattr(upload_session_service.storage_service, "delete_file", delete_file)
    monkeypatch.setattr(upload_session_service, "build_upload_policy", build_upload_policy)
    monkeypatch.setattr(upload_session_service.file_scan_service, "get_initial_scan_state", get_initial_scan_state)
    db = _SequenceDb([
        _ScalarResult(session),
        _ScalarsResult([part]),
        _ScalarResult(reservation),
    ])

    result = await upload_session_service.complete_upload_session(
        db,
        workspace_id="ws-1",
        session_id="session-1",
        data=UploadCompleteRequest(),
    )

    large_input = db.added[0]
    assert result["file"]["source"] == "large_input"
    assert result["file"]["file_id"] == large_input.id
    assert reservation.status == "committed"
    assert reservation.committed_source == "large_input"
    assert reservation.committed_file_id == large_input.id
    assert session.status == "completed"


@pytest.mark.asyncio
async def test_create_upload_session_uses_s3_multipart_when_direct_upload_available(monkeypatch) -> None:
    async def validate_upload_request(*_args, **_kwargs) -> None:
        return None

    async def build_upload_policy(_db):
        policy = _policy()
        policy["backend"] = "s3"
        policy["direct_upload_supported"] = True
        return policy

    async def create_multipart_upload(*_args, **_kwargs):
        return upload_session_service.storage_service.MultipartUploadContext(
            storage_key="s3/key/report.pdf",
            provider_upload_id="upload-id-1",
        )

    monkeypatch.setattr(upload_session_service, "validate_upload_request", validate_upload_request)
    monkeypatch.setattr(upload_session_service, "build_upload_policy", build_upload_policy)
    monkeypatch.setattr(upload_session_service.storage_service, "create_multipart_upload", create_multipart_upload)
    db = _SequenceDb([
        _ScalarResult(None),
        _ScalarResult(None),
        _ScalarResult("ws-1"),
        _ScalarResult(0),
        _ScalarResult(0),
        _ScalarResult(0),
        _ScalarResult(0),
    ])

    session = await upload_session_service.create_upload_session(
        db,
        workspace_id="ws-1",
        uploader_type="user",
        uploader_id="user-1",
        uploader_name="User",
        data=UploadSessionCreateRequest(
            surface="shared_file",
            filename="report.pdf",
            content_type="application/pdf",
            expected_size=10 * 1024 * 1024,
            client_request_id="request-s3",
        ),
    )

    assert session.upload_mode == "s3_multipart"
    assert session.storage_backend == "s3"
    assert session.storage_key == "s3/key/report.pdf"
    assert session.provider_upload_id == "upload-id-1"
    assert session.part_size_bytes >= upload_session_service.storage_service.S3_MIN_MULTIPART_PART_BYTES


@pytest.mark.asyncio
async def test_sign_s3_upload_part_records_signed_part(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    session = SimpleNamespace(
        id="session-1",
        workspace_id="ws-1",
        status="pending",
        upload_mode="s3_multipart",
        part_count=2,
        expires_at=now + timedelta(minutes=30),
        storage_key="s3/key/report.pdf",
        provider_upload_id="upload-id-1",
    )

    async def sign_multipart_part(*_args, **_kwargs):
        return upload_session_service.storage_service.MultipartPartUploadUrl(
            upload_url="https://s3.example.com/upload",
            expires_at=now + timedelta(minutes=15),
            required_headers={},
        )

    monkeypatch.setattr(upload_session_service.storage_service, "sign_multipart_part", sign_multipart_part)
    db = _SequenceDb([
        _ScalarResult(session),
        _ScalarResult(None),
    ])

    result = await upload_session_service.sign_part(
        db,
        workspace_id="ws-1",
        session_id="session-1",
        part_number=1,
    )

    part = db.added[0]
    assert result.upload_url == "https://s3.example.com/upload"
    assert result.upload_mode == "s3_multipart"
    assert part.status == "signed"
    assert part.storage_key == "s3/key/report.pdf"
    assert session.status == "uploading"


@pytest.mark.asyncio
async def test_complete_s3_upload_session_commits_large_input(monkeypatch) -> None:
    payload = b"hello s3 multipart"
    checksum = hashlib.sha256(payload).hexdigest()
    now = datetime.now(timezone.utc)
    session = SimpleNamespace(
        id="session-1",
        workspace_id="ws-1",
        surface="large_input",
        status="uploading",
        upload_mode="s3_multipart",
        expires_at=now + timedelta(minutes=30),
        part_count=1,
        expected_size=len(payload),
        checksum=f"sha256:{checksum}",
        effective_filename="dataset.zip",
        content_type="application/zip",
        purpose="agent_input",
        owner_type="none",
        owner_id="",
        retention_policy="expires_at",
        uploader_type="user",
        uploader_id="user-1",
        uploader_name="User",
        quota_reservation_id="reservation-1",
        storage_key="s3/key/dataset.zip",
        provider_upload_id="upload-id-1",
        received_size=0,
        completed_at=None,
    )
    part = SimpleNamespace(
        session_id="session-1",
        part_number=1,
        size=0,
        checksum="",
        etag="",
        storage_key="s3/key/dataset.zip",
        deleted_at=None,
        status="signed",
    )
    reservation = SimpleNamespace(
        id="reservation-1",
        status="active",
        committed_source="",
        committed_file_id="",
        released_at=None,
    )
    completed_calls: list[tuple[str, str, list[dict[str, str | int]]]] = []

    async def complete_multipart_upload(key, provider_upload_id, parts):
        completed_calls.append((key, provider_upload_id, parts))
        return len(payload), checksum

    async def delete_file(_key: str) -> None:
        return None

    async def get_initial_scan_state(_db):
        return "skipped", "metadata_only"

    monkeypatch.setattr(upload_session_service.storage_service, "complete_multipart_upload", complete_multipart_upload)
    monkeypatch.setattr(upload_session_service.storage_service, "delete_file", delete_file)
    monkeypatch.setattr(upload_session_service.file_scan_service, "get_initial_scan_state", get_initial_scan_state)
    db = _SequenceDb([
        _ScalarResult(session),
        _ScalarsResult([part]),
        _ScalarResult(reservation),
    ])

    result = await upload_session_service.complete_upload_session(
        db,
        workspace_id="ws-1",
        session_id="session-1",
        data=UploadCompleteRequest(
            parts=[{
                "part_number": 1,
                "etag": '"etag-1"',
                "size": len(payload),
                "checksum": f"sha256:{checksum}",
            }],
        ),
    )

    large_input = db.added[0]
    assert completed_calls == [("s3/key/dataset.zip", "upload-id-1", [{"part_number": 1, "etag": '"etag-1"'}])]
    assert part.status == "uploaded"
    assert part.size == len(payload)
    assert part.etag == '"etag-1"'
    assert result["file"]["source"] == "large_input"
    assert result["file"]["file_id"] == large_input.id
    assert reservation.status == "committed"
    assert session.status == "completed"
