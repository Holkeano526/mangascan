from pydantic import BaseModel, Field
from typing import List, Optional

class JobResponse(BaseModel):
    task_id: str
    filename: str
    fast_mode: bool
    status: str
    ts: float

class UploadResponse(BaseModel):
    task_id: str
    filename: str

class LibraryItem(BaseModel):
    task_id: str
    filename: str
    status: str
    has_pdf: bool
    leftovers: bool
    size_mb: float
    ts: float

class DeleteResponse(BaseModel):
    status: str
    task_id: Optional[str] = None
    error: Optional[str] = None

class CancelResponse(BaseModel):
    status: str
