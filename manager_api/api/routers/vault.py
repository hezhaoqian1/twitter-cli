"""Vault lifecycle endpoints for the local administrator console."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ...api.dependencies import get_db, get_vault_runtime
from ...schemas.vault import (
    VaultInitializeRequest,
    VaultInitializeResponse,
    VaultBackupResponse,
    VaultRestoreResponse,
    VaultPasswordRequest,
    VaultRecoveryKeyRequest,
    VaultStatusResponse,
)
from ...services.vault import (
    VaultAlreadyInitializedError,
    VaultRuntime,
    VaultService,
    VaultUnlockError,
)
from ...services.backup import (
    BackupError,
    BackupFormatError,
    create_backup,
    restore_backup,
    verify_backup,
)

router = APIRouter(prefix="/api/vault", tags=["vault"])


def _status(vault: VaultService) -> VaultStatusResponse:
    """Build a redacted vault status response."""
    return VaultStatusResponse(
        initialized=vault.initialized,
        unlocked=vault.is_unlocked,
        initialized_at=vault.initialized_at,
    )


@router.get("/status", response_model=VaultStatusResponse)
def vault_status(
    session: Session = Depends(get_db),
    runtime: VaultRuntime = Depends(get_vault_runtime),
) -> VaultStatusResponse:
    """Return initialization and in-memory unlock state."""
    return _status(VaultService(session, runtime=runtime))


@router.post("/initialize", response_model=VaultInitializeResponse, status_code=status.HTTP_201_CREATED)
def initialize_vault(
    request: VaultInitializeRequest,
    session: Session = Depends(get_db),
    runtime: VaultRuntime = Depends(get_vault_runtime),
) -> VaultInitializeResponse:
    """Create the vault and return its recovery key once."""
    try:
        result = VaultService(session, runtime=runtime).initialize(
            request.password.get_secret_value()
        )
    except VaultAlreadyInitializedError as exc:
        raise HTTPException(status_code=409, detail="vault already initialized") from exc
    return VaultInitializeResponse(initialized=True, recovery_key=result.recovery_key)


@router.post("/unlock/password", response_model=VaultStatusResponse)
def unlock_vault_with_password(
    request: VaultPasswordRequest,
    session: Session = Depends(get_db),
    runtime: VaultRuntime = Depends(get_vault_runtime),
) -> VaultStatusResponse:
    """Unlock the vault using the management password."""
    vault = VaultService(session, runtime=runtime)
    try:
        vault.unlock_with_password(request.password.get_secret_value())
    except VaultUnlockError as exc:
        raise HTTPException(status_code=401, detail="vault unlock failed") from exc
    return _status(vault)


@router.post("/unlock/recovery", response_model=VaultStatusResponse)
def unlock_vault_with_recovery_key(
    request: VaultRecoveryKeyRequest,
    session: Session = Depends(get_db),
    runtime: VaultRuntime = Depends(get_vault_runtime),
) -> VaultStatusResponse:
    """Unlock the vault using the generated recovery key."""
    vault = VaultService(session, runtime=runtime)
    try:
        vault.unlock_with_recovery_key(request.recovery_key.get_secret_value())
    except VaultUnlockError as exc:
        raise HTTPException(status_code=401, detail="vault unlock failed") from exc
    return _status(vault)


@router.post("/lock", response_model=VaultStatusResponse)
def lock_vault(
    session: Session = Depends(get_db),
    runtime: VaultRuntime = Depends(get_vault_runtime),
) -> VaultStatusResponse:
    """Lock the process-local Vault runtime immediately."""
    vault = VaultService(session, runtime=runtime)
    vault.lock()
    return _status(vault)


@router.post("/backups", response_model=VaultBackupResponse)
async def create_vault_backup(
    recovery_key: str = Form(..., min_length=1),
    session: Session = Depends(get_db),
    runtime: VaultRuntime = Depends(get_vault_runtime),
) -> Response:
    """Create an encrypted backup and return it as a downloadable response."""
    vault = VaultService(session, runtime=runtime)
    try:
        package = create_backup(session, vault, recovery_key)
        verification = verify_backup(session, vault, package, recovery_key)
    except (BackupError, VaultUnlockError) as exc:
        raise HTTPException(status_code=422, detail="backup creation failed") from exc
    response = Response(
        content=package,
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="manager-backup.json"'},
    )
    response.headers["X-Backup-Format-Version"] = str(verification.format_version)
    response.headers["X-Backup-Table-Count"] = str(verification.table_count)
    response.headers["X-Backup-Row-Count"] = str(verification.row_count)
    return response


@router.post("/backups/verify", response_model=VaultBackupResponse)
async def verify_vault_backup(
    recovery_key: str = Form(..., min_length=1),
    package: UploadFile = File(...),
    session: Session = Depends(get_db),
    runtime: VaultRuntime = Depends(get_vault_runtime),
) -> VaultBackupResponse:
    """Validate a backup without changing the current database."""
    vault = VaultService(session, runtime=runtime)
    try:
        result = verify_backup(session, vault, await package.read(), recovery_key)
    except (BackupError, VaultUnlockError) as exc:
        raise HTTPException(status_code=422, detail="backup verification failed") from exc
    return VaultBackupResponse(**result.__dict__)


@router.post("/restore", response_model=VaultRestoreResponse)
async def restore_vault_backup(
    recovery_key: str = Form(..., min_length=1),
    package: UploadFile = File(...),
    session: Session = Depends(get_db),
    runtime: VaultRuntime = Depends(get_vault_runtime),
) -> VaultRestoreResponse:
    """Restore an encrypted package only when the current database is empty."""
    vault = VaultService(session, runtime=runtime)
    try:
        result = restore_backup(session, vault, await package.read(), recovery_key)
    except BackupFormatError as exc:
        raise HTTPException(status_code=422, detail="backup restore validation failed") from exc
    except BackupError as exc:
        raise HTTPException(status_code=409, detail="backup restore rejected") from exc
    except VaultUnlockError as exc:
        raise HTTPException(status_code=422, detail="backup restore decryption failed") from exc
    return VaultRestoreResponse(**result.__dict__, restored=True)
