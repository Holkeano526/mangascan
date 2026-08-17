import asyncio
import sys
import time
from pathlib import Path
from typing import Dict, Any

# Globals for state
job_queue = asyncio.Queue()
jobs: Dict[str, Dict[str, Any]] = {}
active_processes: Dict[str, asyncio.subprocess.Process] = {}

BASE_DIR = Path(__file__).parent.parent.parent
OUTPUT_DIR = BASE_DIR / "data" / "output"

async def queue_worker():
    while True:
        task = await job_queue.get()
        task_id, file_path, log_path, out_pdf_path, fast_mode = task

        if task_id in jobs:
            jobs[task_id]["status"] = "running"

        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n[SYSTEM] Iniciando procesamiento de la tarea...\n")

            await run_orchestrator(task_id, file_path, log_path, out_pdf_path, fast_mode)
        except Exception as e:
            if task_id in jobs:
                jobs[task_id]["status"] = "error"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n[SYSTEM] Error en el worker: {e}\n")
        finally:
            job_queue.task_done()

async def run_orchestrator(task_id: str, file_path: Path, log_path: Path, out_pdf_path: Path, fast_mode: bool = False):
    work_dir = OUTPUT_DIR / task_id
    
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
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(BASE_DIR)
        )
        
        active_processes[task_id] = process
        
        if process.stdout:
            async for line in process.stdout:
                line_text = line.decode('utf-8', errors='replace')
                log_file.write(line_text)
                log_file.flush()
                
        await process.wait()
        active_processes.pop(task_id, None)
        log_file.write(f"\n[SYSTEM] Procesamiento finalizado con codigo: {process.returncode}\n")

    try:
        if out_pdf_path.exists() and file_path.exists():
            file_path.unlink()
    except OSError:
        pass

    if task_id in jobs and jobs[task_id]["status"] not in ("cancelled",):
        jobs[task_id]["status"] = "done" if out_pdf_path.exists() else "error"
