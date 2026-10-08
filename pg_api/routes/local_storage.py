"""The development storage backend's signed URLs (`LocalStorage`). Mounted only when
`PG_STORAGE_BACKEND=local`; same contract as Supabase's: multipart `PUT` with field `file`."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse

from pg_api.errors import ApiError
from pg_api.state import AppState, app_state
from pg_api.storage import LocalStorage, StorageError

router = APIRouter(prefix="/v1/local-storage", tags=["local-storage"])
State = Annotated[AppState, Depends(app_state)]


def _local(state: AppState, op: str, key: str, expires: int, sig: str) -> LocalStorage:
    storage = state.storage
    if not isinstance(storage, LocalStorage):
        raise ApiError(404, "not_found", "local storage is not enabled")
    try:
        ok = storage.verify(op, key, expires, sig)
    except StorageError:
        ok = False
    if not ok:
        raise ApiError(403, "forbidden", "invalid or expired storage URL")
    return storage


@router.put("/{key:path}", status_code=200)
async def put_object(
    key: str, op: str, expires: int, sig: str, file: Annotated[UploadFile, File()], state: State
) -> dict[str, str]:
    storage = _local(state, "put" if op == "put" else "", key, expires, sig)
    storage.write(key, await file.read())
    return {"Key": key}


@router.get("/{key:path}")
def get_object(key: str, op: str, expires: int, sig: str, state: State) -> FileResponse:
    storage = _local(state, "get" if op == "get" else "", key, expires, sig)
    if not storage.exists(key):
        raise ApiError(404, "not_found", "no such object")
    return FileResponse(storage.path(key))
