import os
import time
import uuid
import shutil
import subprocess
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Arranca el worker de la cola al iniciar el servidor.
    app.state.worker = asyncio.create_task(queue_worker())
    yield
    app.state.worker.cancel()


app = FastAPI(title="Manga Translator NAS", lifespan=lifespan)

# Config paths
BASE_DIR = Path(__file__).parent.parent
INPUT_DIR = BASE_DIR / "data" / "input"
OUTPUT_DIR = BASE_DIR / "data" / "output"
STATIC_DIR = BASE_DIR / "src" / "static"

INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

active_processes = {}
job_queue = asyncio.Queue()
# Registro de trabajos conocidos por el servidor (sobrevive al cierre del navegador,
# no al reinicio del servidor). Permite reconectar la UI a un proceso en curso.
jobs: dict = {}

async def queue_worker():
    while True:
        task = await job_queue.get()
        task_id, file_path, log_path, out_pdf_path, fast_mode = task

        if task_id in jobs:
            jobs[task_id]["status"] = "running"

        try:
            # Escribir en log posiciÃ³n inicial (procesando)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n[SYSTEM] Iniciando procesamiento de la tarea...\n")

            await asyncio.to_thread(
                run_orchestrator, task_id, file_path, log_path, out_pdf_path, fast_mode
            )
        except Exception as e:
            if task_id in jobs:
                jobs[task_id]["status"] = "error"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n[SYSTEM] Error en el worker: {e}\n")
        finally:
            job_queue.task_done()

@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open(STATIC_DIR / "index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    fast_mode: bool = Form(False)
):
    task_id = str(uuid.uuid4())[:8]
    # Sanear el nombre: descartar cualquier componente de ruta (evita path traversal)
    # y quitar comillas que puedan romper la shell/logs.
    safe_filename = Path(file.filename).name.replace("'", "").replace('"', "")
    file_path = INPUT_DIR / f"{task_id}_{safe_filename}"
    
    # Escritura en streaming por chunks: RAM acotada (~1 MB) sin importar el
    # tamaño del PDF. La velocidad es prácticamente idéntica (el cuello de botella
    # es la red/disco, no la memoria), pero evita el pico de RAM que tumbaría un
    # NAS al subir PDFs de cientos de MB.
    with open(file_path, "wb") as buffer:
        while chunk := await file.read(1024 * 1024):
            buffer.write(chunk)

    log_path = OUTPUT_DIR / f"{task_id}.log"
    out_pdf_path = OUTPUT_DIR / task_id / f"translated_{safe_filename}"
    # Enviar trabajo a la cola
    posicion = job_queue.qsize() + 1
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"[SYSTEM] Archivo recibido. PosiciÃ³n en cola: {posicion}\n")
        f.write("[SYSTEM] Esperando recursos del sistema...\n")
        
    jobs[task_id] = {
        "task_id": task_id,
        "filename": safe_filename,
        "fast_mode": fast_mode,
        "status": "queued",
        "ts": time.time(),
    }
    await job_queue.put((task_id, file_path, log_path, out_pdf_path, fast_mode))

    return {"task_id": task_id, "filename": safe_filename}

def run_orchestrator(task_id: str, file_path: Path, log_path: Path, out_pdf_path: Path, fast_mode: bool = False):
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
    if fast_mode:
        cmd.append("--fast")
    
    with open(log_path, "a", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(BASE_DIR)
        )
        
        active_processes[task_id] = process
        
        for line in iter(process.stdout.readline, ''):
            log_file.write(line)
            log_file.flush()
            
        process.wait()
        active_processes.pop(task_id, None)
        log_file.write(f"\n[SYSTEM] Procesamiento finalizado con codigo: {process.returncode}\n")

    # Retención: una vez generada la salida, el PDF de entrada ya no hace falta.
    # Borrarlo evita que data/input/ se llene en un NAS desatendido.
    try:
        if out_pdf_path.exists() and file_path.exists():
            file_path.unlink()
    except OSError:
        pass

    # Estado final del trabajo (hay PDF = terminado, aunque falten páginas sueltas).
    if task_id in jobs and jobs[task_id]["status"] not in ("cancelled",):
        jobs[task_id]["status"] = "done" if out_pdf_path.exists() else "error"

@app.get("/api/stream/{task_id}")
async def stream_log(task_id: str):
    safe_task_id = Path(task_id).name
    log_path = OUTPUT_DIR / f"{safe_task_id}.log"

    async def log_generator():
        # Esperar a que el archivo se cree
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
                    # Sanitize newlines for SSE
                    clean_line = line.replace('\n', '')
                    yield f"data: {clean_line}\n\n"
                    if "[SYSTEM] Procesamiento finalizado" in clean_line:
                        break
                    continue

                # Sin líneas nuevas: esperar un poco.
                await asyncio.sleep(0.5)

                # Salvavidas: si el proceso ya arrancó y desapareció (p. ej. lo mató
                # el OOM del NAS) sin escribir el marcador de fin, cerrar el stream
                # en lugar de quedarse colgado leyendo para siempre.
                if started and safe_task_id not in active_processes:
                    idle_tras_fin += 1
                    if idle_tras_fin >= 3:  # ~1.5s de gracia para vaciar el buffer
                        for resto in f.read().splitlines():
                            yield f"data: {resto}\n\n"
                        yield "data: [SYSTEM] Procesamiento finalizado (stream cerrado inesperadamente).\n\n"
                        break
                else:
                    idle_tras_fin = 0

    return StreamingResponse(log_generator(), media_type="text/event-stream")

@app.get("/api/download/{task_id}")
async def download_file(task_id: str, filename: str):
    # Descartar componentes de ruta en ambos parámetros para evitar path traversal.
    safe_task_id = Path(task_id).name
    safe_filename = Path(filename).name
    out_pdf_path = OUTPUT_DIR / safe_task_id / f"translated_{safe_filename}"

    # Defensa en profundidad: la ruta resuelta debe quedar dentro de OUTPUT_DIR.
    try:
        out_pdf_path.resolve().relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        return {"error": "Ruta inválida."}

    if out_pdf_path.exists():
        return FileResponse(path=out_pdf_path, filename=f"traducido_{safe_filename}", media_type='application/pdf')
    return {"error": "Archivo no encontrado o no terminado."}

@app.get("/api/jobs")
async def list_jobs():
    """Trabajos conocidos por el servidor (más recientes primero).

    La UI lo consulta al cargar para reconectarse a un proceso en curso o mostrar
    el resultado del último trabajo terminado.
    """
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


@app.get("/api/library")
async def library():
    """Escanea el disco (data/output) y devuelve todos los trabajos con su estado,
    tamaño y si tienen PDF descargable o restos (raw/render) purgables."""
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


@app.delete("/api/jobs/{task_id}")
async def delete_job(task_id: str):
    """Purga un trabajo: borra su carpeta de salida, su log y el PDF de entrada.
    Rechaza el borrado si el trabajo aún está corriendo (hay que cancelarlo antes)."""
    tid = Path(task_id).name
    if tid in active_processes:
        return {"status": "running", "error": "Cancela el trabajo antes de eliminarlo."}

    folder = OUTPUT_DIR / tid
    try:
        folder.resolve().relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        return {"status": "error", "error": "Ruta inválida."}

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
    return {"status": "deleted", "task_id": tid}

@app.post("/api/cancel/{task_id}")
async def cancel_task(task_id: str):
    if task_id in jobs:
        jobs[task_id]["status"] = "cancelled"
    if task_id in active_processes:
        process = active_processes.pop(task_id)
        process.kill()

        log_path = OUTPUT_DIR / f"{task_id}.log"
        if log_path.exists():
            with open(log_path, "a", encoding="utf-8") as log_file:
                log_file.write(f"\n[SYSTEM] Procesamiento cancelado por el usuario.\n")

        return {"status": "cancelled"}
    return {"status": "not_found_or_finished"}

