import os
import uuid
import subprocess
import asyncio
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Manga Translator NAS")

# Config paths
BASE_DIR = Path(__file__).parent.parent
INPUT_DIR = BASE_DIR / "data" / "input"
OUTPUT_DIR = BASE_DIR / "data" / "output"
STATIC_DIR = BASE_DIR / "src" / "static"

INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open(STATIC_DIR / "index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.post("/api/upload")
async def upload_file(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    task_id = str(uuid.uuid4())[:8]
    # Limpiamos el nombre del archivo para la shell
    safe_filename = file.filename.replace("'", "").replace('"', "")
    file_path = INPUT_DIR / f"{task_id}_{safe_filename}"
    
    with open(file_path, "wb") as buffer:
        buffer.write(await file.read())
        
    log_path = OUTPUT_DIR / f"{task_id}.log"
    out_pdf_path = OUTPUT_DIR / task_id / f"translated_{safe_filename}"
    
    # Launch background task
    background_tasks.add_task(run_orchestrator, task_id, file_path, log_path, out_pdf_path)
    
    return {"task_id": task_id, "filename": safe_filename}

def run_orchestrator(task_id: str, file_path: Path, log_path: Path, out_pdf_path: Path):
    work_dir = OUTPUT_DIR / task_id
    
    import sys
    # Comando multiplataforma (Docker/WSL) usando el mismo python del servidor
    cmd = [
        sys.executable, "-m", "src.orquestador",
        file_path.as_posix(),
        "--work-dir", work_dir.as_posix(),
        "--output", out_pdf_path.as_posix(),
        "-v"
    ]
    
    with open(log_path, "w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(BASE_DIR)
        )
        
        for line in iter(process.stdout.readline, ''):
            log_file.write(line)
            log_file.flush()
            
        process.wait()
        log_file.write(f"\n[SYSTEM] Procesamiento finalizado con codigo: {process.returncode}\n")

@app.get("/api/stream/{task_id}")
async def stream_log(task_id: str):
    log_path = OUTPUT_DIR / f"{task_id}.log"
    
    async def log_generator():
        # Esperar a que el archivo se cree
        for _ in range(10):
            if log_path.exists():
                break
            await asyncio.sleep(0.5)
            
        if not log_path.exists():
            yield "data: [ERROR] El log no se pudo iniciar.\n\n"
            return
            
        with open(log_path, "r", encoding="utf-8") as f:
            while True:
                line = f.readline()
                if not line:
                    await asyncio.sleep(0.5)
                    continue
                
                # Sanitize newlines for SSE
                clean_line = line.replace('\n', '')
                yield f"data: {clean_line}\n\n"
                
                if "[SYSTEM] Procesamiento finalizado" in clean_line:
                    break

    return StreamingResponse(log_generator(), media_type="text/event-stream")

@app.get("/api/download/{task_id}")
async def download_file(task_id: str, filename: str):
    out_pdf_path = OUTPUT_DIR / task_id / f"translated_{filename}"
    if out_pdf_path.exists():
        return FileResponse(path=out_pdf_path, filename=f"traducido_{filename}", media_type='application/pdf')
    return {"error": "Archivo no encontrado o no terminado."}
