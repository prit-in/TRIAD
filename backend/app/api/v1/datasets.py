"""Datasets, versions and profiles (PRD section 25.2).

Registration goes through `DatasetRegistry`, the same code path the pipeline uses, so the
immutability rule and the duplicate-checksum rule apply identically whether a file arrives
through the API or through the watched folder. Registering an identical file returns 200 and
the version that already holds it, rather than creating a second copy of the same bytes.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response, UploadFile
from sqlalchemy import select

from backend.app import problems
from backend.app.deps import CurrentUser, DbSession, require_roles
from backend.app.schemas import (
    DatasetSummary,
    DatasetVersionSummary,
    RegisterDatasetRequest,
)
from backend.app.security import DATA_ENGINEER, DATA_ROLES
from triad_core.storage import storage

router = APIRouter(tags=["datasets"])

MAX_UPLOAD_BYTES = 200 * 1024 * 1024


def _latest(session: DbSession, dataset_id: uuid.UUID):
    from triad_core.models import DatasetVersion

    return session.execute(
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == dataset_id)
        .order_by(DatasetVersion.version_no.desc())
    ).scalars().first()


def _summary(session: DbSession, dataset) -> DatasetSummary:
    latest = _latest(session, dataset.id)
    return DatasetSummary(
        id=dataset.id, name=dataset.name, entity_type=dataset.entity_type,
        source_type=dataset.source_type,
        latest_version=latest.version_no if latest else None,
        latest_quality_score=latest.quality_score if latest else None,
        updated_at=latest.created_at if latest else dataset.created_at,
    )


def _find(session: DbSession, ref: str):
    from triad_core.models import Dataset

    dataset = session.execute(select(Dataset).where(Dataset.name == ref)).scalar_one_or_none()
    if dataset is None:
        try:
            dataset = session.get(Dataset, uuid.UUID(ref))
        except ValueError:
            dataset = None
    if dataset is None:
        raise problems.not_found("Dataset", ref)
    return dataset


@router.get("/datasets", response_model=list[DatasetSummary], summary="List datasets")
def list_datasets(session: DbSession, user: CurrentUser) -> list[DatasetSummary]:
    from triad_core.models import Dataset

    rows = session.execute(select(Dataset).order_by(Dataset.name)).scalars().all()
    return [_summary(session, d) for d in rows]


@router.get("/datasets/{ref}", response_model=DatasetSummary, summary="One dataset")
def get_dataset(ref: str, session: DbSession, user: CurrentUser) -> DatasetSummary:
    return _summary(session, _find(session, ref))


@router.get(
    "/datasets/{ref}/versions",
    response_model=list[DatasetVersionSummary],
    summary="Version history",
)
def list_versions(ref: str, session: DbSession, user: CurrentUser) -> list[DatasetVersionSummary]:
    from triad_core.models import DatasetVersion

    dataset = _find(session, ref)
    rows = session.execute(
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == dataset.id)
        .order_by(DatasetVersion.version_no.desc())
    ).scalars().all()
    return [
        DatasetVersionSummary(
            version_no=r.version_no, row_count=r.row_count, checksum=r.checksum_sha256,
            quality_score=r.quality_score, created_at=r.created_at, storage_uri=r.storage_uri,
        )
        for r in rows
    ]


@router.get(
    "/datasets/{ref}/versions/{version_no}/profile",
    summary="Profile and issues for one version",
    dependencies=[Depends(require_roles(*DATA_ROLES))],
)
def get_profile(ref: str, version_no: int, session: DbSession) -> dict:
    """The stored profile document, read from where the profiling stage wrote it."""
    import json

    from triad_core.models import DatasetVersion

    dataset = _find(session, ref)
    version = session.execute(
        select(DatasetVersion).where(
            DatasetVersion.dataset_id == dataset.id,
            DatasetVersion.version_no == version_no,
        )
    ).scalar_one_or_none()
    if version is None:
        raise problems.not_found("Dataset version", f"{dataset.name} v{version_no}")
    if not version.profile_uri:
        raise problems.not_found("Profile", f"{dataset.name} v{version_no}")

    store = storage()
    path = store.path(version.profile_uri)
    if not path.exists():
        raise problems.not_found("Profile document", version.profile_uri)
    return json.loads(store.read_text(path))


@router.post(
    "/datasets",
    response_model=DatasetSummary,
    status_code=201,
    summary="Register a source or upload a file",
    dependencies=[Depends(require_roles(DATA_ENGINEER))],
)
def register_dataset(
    payload: RegisterDatasetRequest,
    response: Response,
    session: DbSession,
    user: CurrentUser,
) -> DatasetSummary:
    from dataeng.ingest import DatasetRegistry

    store = storage()
    registry = DatasetRegistry(session, store)
    registry.register_source(
        payload.name, entity_type=payload.entity_type, source_type=payload.source_type,
    )
    source = store.path(payload.source_uri)
    if not source.exists():
        raise problems.unprocessable(
            f"{payload.source_uri} does not exist in the platform's storage."
        )

    result = registry.register_version(payload.name, source, actor=str(user.id))
    if result.version_id is None:
        # An identical checksum: the existing version already holds these bytes.
        response.status_code = 200
    return _summary(session, _find(session, payload.name))


@router.post(
    "/datasets/{ref}/upload",
    response_model=DatasetSummary,
    status_code=201,
    summary="Upload a file as a new version",
    dependencies=[Depends(require_roles(DATA_ENGINEER))],
)
async def upload_version(
    ref: str,
    response: Response,
    session: DbSession,
    user: CurrentUser,
    file: UploadFile,
) -> DatasetSummary:
    from dataeng.ingest import DatasetRegistry

    dataset = _find(session, ref)
    store = storage()
    landing = store.root / "uploads" / dataset.name
    landing.mkdir(parents=True, exist_ok=True)
    target = landing / (file.filename or "upload.bin")

    written = 0
    with target.open("wb") as handle:
        while chunk := await file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                handle.close()
                target.unlink(missing_ok=True)
                raise problems.Problem(
                    status=413, title="Upload too large",
                    detail=f"The limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                    problem_type="upload-too-large",
                )
            handle.write(chunk)

    result = DatasetRegistry(session, store).register_version(
        dataset.name, target, actor=str(user.id)
    )
    if result.version_id is None:
        response.status_code = 200
    return _summary(session, dataset)
