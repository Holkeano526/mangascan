import uuid
import time
import shutil
import asyncio
from pathlib import Path
from typing import List

from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse

from src.schemas.job import JobResponse, UploadResponse, LibraryItem, DeleteResponse, CancelResponse
from src.services.queue_manager import job_queue, jobs, active_processes, OUTPUT_DIR, BASE_DIR

router = APIRouter()
INPUT_DIR = BASE_DIR / "data" / "input"
STATIC_DIR = BASE_DIR / "src" / "static"

@router.get("/", response_class=HTMLResponse)
async def read_root():
    with open(STATIC_DIR / "index.html", "r", encoding="utf-8") as f:
        return f.read()

@router.post("/api/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    fast_mode: bool = Form(False)
):
    task_id = str(uuid.uuid4())[:8]
    safe_filename = Path(file.filename).name.replace("'", "").replace('"', "")
    file_path = INPUT_DIR / f"{task_id}_{safe_filename}"
    
    with open(file_path, "wb") as buffer:
        while chunk := await file.read(1024 * 1024):
            buffer.write(chunk)

    log_path = OUTPUT_DIR / f"{task_id}.log"
    out_pdf_path = OUTPUT_DIR / task_id / f"translated_{safe_filename}"
    posicion = job_queue.qsize() + 1
    
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"[SYSTEM] Archivo recibido. Posición en cola: {posicion}\n")
        f.write("[SYSTEM] Esperando recursos del sistema...\n")
        
    jobs[task_id] = {
        "task_id": task_id,
        "filename": safe_filename,
        "fast_mode": fast_mode,
        "status": "queued",
        "ts": time.time(),
    }
    
    await job_queue.put((task_id, file_path, log_path, out_pdf_path, fast_mode))
    return UploadResponse(task_id=task_id, filename=safe_filename)

@router.get("/api/stream/{task_id}")
async def stream_log(task_id: str):
    safe_task_id = Path(task_id).name
    log_path = OUTPUT_DIR / f"{safe_task_id}.log"

    async def log_generator():
        for _ in range(10):
            if log_path.exists():
                break
            await asyncio.sleep(0.5)

        if not log_path.exists():
            yield "data: [ERROR] El log no se pudo iniciar.\n\n"
            return

        started = False
        idle_tras_fin = 0
        with open(log_path, "r", encoding="utf-8") as f:
            while True:
                line = f.readline()
                if line:
                    if "Iniciando procesamiento" in line:
                        started = True
                    clean_line = line.replace('\n', '')
                    yield f"data: {clean_line}\n\n"
                    if "[SYSTEM] Procesamiento finalizado" in clean_line:
                        break
                    continue

                await asyncio.sleep(0.5)

                if started and safe_task_id not in active_processes:
                    idle_tras_fin += 1
                    if idle_tras_fin >= 3:
                        for resto in f.read().splitlines():
                            yield f"data: {resto}\n\n"
                        yield "data: [SYSTEM] Procesamiento finalizado (stream cerrado inesperadamente).\n\n"
                        break
                else:
                    idle_tras_fin = 0

    return StreamingResponse(log_generator(), media_type="text/event-stream")

@router.get("/api/download/{task_id}")
async def download_file(task_id: str, filename: str):
    safe_task_id = Path(task_id).name
    safe_filename = Path(filename).name
    out_pdf_path = OUTPUT_DIR / safe_task_id / f"translated_{safe_filename}"

    try:
        out_pdf_path.resolve().relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        return {"error": "Ruta inválida."}

    if out_pdf_path.exists():
        return FileResponse(path=out_pdf_path, filename=f"traducido_{safe_filename}", media_type='application/pdf')
    return {"error": "Archivo no encontrado o no terminado."}

@router.get("/api/jobs", response_model=List[JobResponse])
async def list_jobs():
    return sorted(jobs.values(), key=lambda j: j["ts"], reverse=True)

def _dir_size(path: Path) -> int:
    total = 0
    if path.is_dir():
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
    return total

@router.get("/api/library", response_model=List[LibraryItem])
async def library():
    ids = set()
    for log in OUTPUT_DIR.glob("*.log"):
        ids.add(log.stem)
    for d in OUTPUT_DIR.iterdir():
        if d.is_dir():
            ids.add(d.name)
    ids.update(jobs.keys())

    result = []
    for tid in ids:
        folder = OUTPUT_DIR / tid
        pdf = next(folder.glob("translated_*.pdf"), None) if folder.is_dir() else None

        filename = None
        if pdf:
            filename = pdf.name[len("translated_"):]
        else:
            inp = next(INPUT_DIR.glob(f"{tid}_*"), None)
            if inp:
                filename = inp.name[len(tid) + 1:]

        reg = jobs.get(tid)
        if reg:
            status = reg["status"]
        elif pdf:
            status = "done"
        else:
            status = "incomplete"

        leftovers = folder.is_dir() and ((folder / "raw").exists() or (folder / "render").exists())

        if reg:
            ts = reg["ts"]
        elif folder.is_dir():
            ts = folder.stat().st_mtime
        else:
            log = OUTPUT_DIR / f"{tid}.log"
            ts = log.stat().st_mtime if log.exists() else 0

        result.append({
            "task_id": tid,
            "filename": filename or tid,
            "status": status,
            "has_pdf": bool(pdf),
            "leftovers": bool(leftovers),
            "size_mb": round(_dir_size(folder) / 1024 / 1024, 1),
            "ts": ts,
        })

    result.sort(key=lambda x: x["ts"], reverse=True)
    return result

@router.delete("/api/jobs/{task_id}", response_model=DeleteResponse)
async def delete_job(task_id: str):
    tid = Path(task_id).name
    if tid in active_processes:
        return DeleteResponse(status="running", error="Cancela el trabajo antes de eliminarlo.")

    folder = OUTPUT_DIR / tid
    try:
        folder.resolve().relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        return DeleteResponse(status="error", error="Ruta inválida.")

    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)
    log = OUTPUT_DIR / f"{tid}.log"
    if log.exists():
        log.unlink(missing_ok=True)
    for inp in INPUT_DIR.glob(f"{tid}_*"):
        try:
            inp.unlink(missing_ok=True)
        except OSError:
            pass
    jobs.pop(tid, None)
    return DeleteResponse(status="deleted", task_id=tid)

@router.post("/api/cancel/{task_id}", response_model=CancelResponse)
async def cancel_task(task_id: str):
    if task_id in jobs:
        jobs[task_id]["status"] = "cancelled"
    if task_id in active_processes:
        process = active_processes.pop(task_id)
        try:
            process.kill()
        except OSError:
            pass

        log_path = OUTPUT_DIR / f"{task_id}.log"
        if log_path.exists():
            with open(log_path, "a", encoding="utf-8") as log_file:
                log_file.write(f"\n[SYSTEM] Procesamiento cancelado por el usuario.\n")

        return CancelResponse(status="cancelled")
    return CancelResponse(status="not_found_or_finished")
