"""Dataset routes — upload, list, delete, preview."""

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from app.models.dataset import DatasetPublic, DatasetListResponse
from app.middleware.auth import get_current_user
from app.services.dataset_service import (
    upload_dataset,
    list_user_datasets,
    get_dataset,
    delete_dataset,
)
from app.services.duckdb_engine import execute_query

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


@router.post("/upload", response_model=DatasetPublic)
async def upload_file(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """Upload a CSV, Excel, PDF, or DOCX file for analysis."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided.")

    file_bytes = await file.read()
    try:
        dataset = await upload_dataset(
            user_id=user["id"],
            filename=file.filename,
            file_bytes=file_bytes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if dataset["status"] == "error":
        raise HTTPException(status_code=422, detail=dataset["error_message"])

    return DatasetPublic(**dataset)


@router.get("/", response_model=DatasetListResponse)
async def list_datasets(user: dict = Depends(get_current_user)):
    """List all datasets belonging to the authenticated user."""
    datasets = await list_user_datasets(user["id"])
    return DatasetListResponse(
        datasets=[DatasetPublic(**d) for d in datasets],
        total=len(datasets),
    )


@router.get("/{dataset_id}", response_model=DatasetPublic)
async def get_dataset_detail(
    dataset_id: str,
    user: dict = Depends(get_current_user),
):
    """Get details of a specific dataset."""
    dataset = await get_dataset(user["id"], dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    return DatasetPublic(**dataset)


@router.get("/{dataset_id}/preview")
async def preview_dataset(
    dataset_id: str,
    limit: int = 20,
    user: dict = Depends(get_current_user),
):
    """Preview the first N rows of a dataset."""
    dataset = await get_dataset(user["id"], dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    if dataset["status"] != "ready":
        raise HTTPException(status_code=400, detail="Dataset is not ready.")

    try:
        rows, total = execute_query(
            user["id"],
            f'SELECT * FROM "{dataset["table_name"]}" LIMIT {min(limit, 100)}',
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "dataset_id": dataset_id,
        "table_name": dataset["table_name"],
        "columns": dataset["columns"],
        "rows": rows,
        "total_rows": dataset["row_count"],
        "preview_rows": len(rows),
    }


@router.delete("/{dataset_id}")
async def delete_dataset_endpoint(
    dataset_id: str,
    user: dict = Depends(get_current_user),
):
    """Delete a dataset and its associated data."""
    ok = await delete_dataset(user["id"], dataset_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    return {"message": "Dataset deleted successfully."}