/* ═══════════════════════════════════════════
   MangaScan AI — Premium Frontend Engine
   ═══════════════════════════════════════════ */

'use strict';

// ─── DOM References ──────────────────────────

const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('fileInput');
const progressContainer = document.getElementById('progress-container');
const statusText = document.getElementById('status-text');
const terminal = document.getElementById('terminal');
const downloadBtn = document.getElementById('download-btn');
const spinner = document.querySelector('.spinner');
const progressBarFill = document.getElementById('progress-bar-fill');
const progressBarBg = document.querySelector('.progress-bar-bg');
const cancelBtn = document.getElementById('cancel-btn');
const resetBtn = document.getElementById('reset-btn');

let currentTaskId = null;
let currentEvtSource = null;
let uploadAbortController = null;

// ─── Cursor Parallax on Background Orbs ──────

(function initCursorParallax() {
    const orbs = document.querySelectorAll('.orb-glow');
    if (!orbs.length) return;

    let mouseX = 0.5;
    let mouseY = 0.5;

    document.addEventListener('mousemove', (e) => {
        mouseX = e.clientX / window.innerWidth;
        mouseY = e.clientY / window.innerHeight;

        orbs.forEach((orb, i) => {
            const offset = (i + 1) * 15;
            const x = (mouseX - 0.5) * offset;
            const y = (mouseY - 0.5) * offset;
            orb.style.transform = `translate(${x}px, ${y}px)`;
        });
    });
})();

// ─── Dropzone Click ──────────────────────────

dropzone.addEventListener('click', () => fileInput.click());

// Keyboard activation (Enter/Space)
dropzone.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        fileInput.click();
    }
});

// ─── Drag & Drop Handlers ────────────────────

['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
    dropzone.addEventListener(eventName, preventDefaults, false);
});

function preventDefaults(e) {
    e.preventDefault();
    e.stopPropagation();
}

['dragenter', 'dragover'].forEach(eventName => {
    dropzone.addEventListener(eventName, () => {
        dropzone.classList.add('dragover');
    }, false);
});

['dragleave', 'drop'].forEach(eventName => {
    dropzone.addEventListener(eventName, () => {
        dropzone.classList.remove('dragover');
    }, false);
});

dropzone.addEventListener('drop', handleDrop, false);
fileInput.addEventListener('change', handleFiles, false);

function handleDrop(e) {
    const dt = e.dataTransfer;
    handleFiles({ target: { files: dt.files } });
}

// ─── File Processing ─────────────────────────

let pendingFile = null;

async function handleFiles(e) {
    const files = e.target.files;
    if (!files.length) return;

    const file = files[0];
    if (file.type !== 'application/pdf') {
        appendLog('⚠️ Solo se aceptan archivos PDF.', 'warning');
        return;
    }

    pendingFile = file;

    // Transition UI to confirmation
    dropzone.classList.add('hidden');
    document.getElementById('confirmation-container').classList.remove('hidden');
    document.getElementById('selected-filename').textContent = file.name;
}

document.getElementById('cancel-upload-btn')?.addEventListener('click', () => {
    pendingFile = null;
    document.getElementById('confirmation-container').classList.add('hidden');
    dropzone.classList.remove('hidden');
    fileInput.value = '';
});

document.getElementById('start-upload-btn')?.addEventListener('click', async () => {
    if (!pendingFile) return;
    const file = pendingFile;
    pendingFile = null;

    // Transition UI to progress
    document.getElementById('confirmation-container').classList.add('hidden');
    progressContainer.classList.remove('hidden');
    terminal.innerHTML = '';
    statusText.textContent = `Procesando: ${file.name}`;
    spinner.style.display = 'block';
    progressBarFill.style.width = '0%';
    downloadBtn.classList.add('hidden');
    resetBtn.classList.add('hidden');
    updateProgressBarAria(0);

    // Upload
    const formData = new FormData();
    formData.append('file', file);
    
    const fastModeToggle = document.getElementById('fastModeToggle');
    if (fastModeToggle) {
        formData.append('fast_mode', fastModeToggle.checked ? 'true' : 'false');
    }

    cancelBtn.style.display = 'inline-flex';
    cancelBtn.disabled = false;
    cancelBtn.innerHTML = `
        <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="10"></circle>
            <line x1="15" y1="9" x2="9" y2="15"></line>
            <line x1="9" y1="9" x2="15" y2="15"></line>
        </svg>
        Cancelar
    `;

    appendLog('📡 Iniciando conexión...', 'system');
    appendLog('📡 Subiendo archivo al NAS...', 'info');

    uploadAbortController = new AbortController();

    try {
        const response = await fetch('/api/upload', {
            method: 'POST',
            body: formData,
            signal: uploadAbortController.signal
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();

        if (data.task_id) {
            currentTaskId = data.task_id;
            localStorage.setItem('mangascan_task', data.task_id);
            appendLog(`✅ Archivo subido. ID Tarea: ${data.task_id}`, 'success');
            startLogStream(data.task_id, data.filename);
        } else {
            appendLog('❌ Error al subir el archivo.', 'error');
            cancelBtn.style.display = 'none';
        }
    } catch (err) {
        if (err.name === 'AbortError') {
            appendLog('🛑 Subida cancelada por el usuario.', 'warning');
        } else {
            appendLog(`🚨 Error de red: ${err.message}`, 'error');
        }
        cancelBtn.style.display = 'none';
    } finally {
        uploadAbortController = null;
    }
});


// ─── Helpers ─────────────────────────────────────

function appendLog(msg, type = '') {
    const div = document.createElement('div');
    div.textContent = msg;
    if (type) div.className = type;
    terminal.appendChild(div);
    terminal.parentElement.scrollTop = terminal.parentElement.scrollHeight;
}

// ─── Event Stream (SSE) ──────────────────────

function startLogStream(taskId, filename) {
    const evtSource = new EventSource(`/api/stream/${taskId}`);
    currentEvtSource = evtSource;
    let totalPages = 0;
    let pagesOk = null;
    let pagesTotal = null;

    evtSource.onmessage = function (event) {
        const rawLine = event.data;
        const parsed = parseLogLine(rawLine);
        appendLog(parsed.text, parsed.type);

        // Extract page progress: "Página 5/63"
        const pageMatch = rawLine.match(/Página\s+(\d+)\s*\/\s*(\d+)/i);
        if (pageMatch) {
            const current = parseInt(pageMatch[1], 10);
            totalPages = parseInt(pageMatch[2], 10);
            const percent = Math.min((current / totalPages) * 100, 100);
            progressBarFill.style.width = `${percent}%`;
            statusText.textContent = `Traduciendo... ${current}/${totalPages}`;
            updateProgressBarAria(percent);
        }

        // Resumen final del orquestador: "COMPLETADO: 45/53"
        const okMatch = rawLine.match(/COMPLETADO:\s*(\d+)\s*\/\s*(\d+)/i);
        if (okMatch) {
            pagesOk = parseInt(okMatch[1], 10);
            pagesTotal = parseInt(okMatch[2], 10);
        }

        // System progress markers
        if (rawLine.match(/(Procesando|Extrayendo|OCR|Analizando)/i)) {
            progressBarFill.style.width = `${Math.min((progressBarFill.style.width ? parseFloat(progressBarFill.style.width) + 5 : 0), 85)}%`;
        }

        // Completion
        if (rawLine.match(/Procesamiento\s+finalizado|Completado|PDF\s+listo/i)) {
            evtSource.close();
            currentEvtSource = null;
            cancelBtn.style.display = 'none';
            resetBtn.classList.remove('hidden');
            spinner.style.display = 'none';

            const codeMatch = rawLine.match(/codigo:\s*(-?\d+)/i);
            const code = codeMatch ? parseInt(codeMatch[1], 10) : 0;
            const failed = (pagesTotal !== null && pagesOk !== null) ? (pagesTotal - pagesOk) : null;

            // Fallo fatal: excepción (código 2) o cero páginas correctas → no hay PDF.
            const fatal = code === 2 || (pagesOk === 0 && pagesTotal > 0);

            const terminalWrapper = document.getElementById('terminal-wrapper');
            const successBanner = document.getElementById('success-banner');

            if (fatal) {
                appendLog('❌ El proceso falló y no se generó ningún PDF (código ' + code + ').', 'error');
                statusText.textContent = 'Traducción Fallida';
            } else {
                progressBarFill.style.width = '100%';
                updateProgressBarAria(100);

                if (terminalWrapper) terminalWrapper.style.display = 'none';
                if (successBanner) successBanner.classList.remove('hidden');

                if (failed && failed > 0) {
                    // Éxito parcial: el PDF existe pero algunas páginas no se tradujeron.
                    statusText.textContent = `⚠️ Completado (${failed} sin traducir)`;
                    const h2 = successBanner ? successBanner.querySelector('h2') : null;
                    const p = successBanner ? successBanner.querySelector('p') : null;
                    if (h2) h2.textContent = 'Traducción completada (parcial)';
                    if (p) p.textContent = `${pagesOk} de ${pagesTotal} páginas traducidas. Las ${failed} restantes se incluyen sin traducir (sin texto detectado o con error).`;
                    appendLog(`⚠️ ${failed} página(s) sin traducir, pero el PDF está completo y descargable.`, 'warning');
                } else {
                    // Éxito total: restaurar el texto por defecto del banner.
                    const h2 = successBanner ? successBanner.querySelector('h2') : null;
                    const p = successBanner ? successBanner.querySelector('p') : null;
                    if (h2) h2.textContent = '¡Tu Manga está Listo!';
                    if (p) p.textContent = 'El archivo ha sido traducido exitosamente y ya puedes descargarlo.';
                    statusText.textContent = '🎉 ¡Traducción Completada!';
                    appendLog('📥 PDF listo para descargar.', 'success');
                }

                downloadBtn.href = `/api/download/${taskId}?filename=${encodeURIComponent(filename)}`;
                downloadBtn.classList.remove('hidden', 'primary-btn');
                downloadBtn.classList.add('success-btn');
            }
        }
    };

    evtSource.onerror = function () {
        evtSource.close();
        currentEvtSource = null;
        cancelBtn.style.display = 'none';
        resetBtn.classList.remove('hidden');
        appendLog('🔌 Conexión al stream perdida. Recarga la página para reintentar.', 'error');
    };

    // Return the source so it can be closed externally if needed
    return evtSource;
}

// ─── Cancel logic ────────────────────────────

if (cancelBtn) {
    cancelBtn.addEventListener('click', async () => {
        cancelBtn.disabled = true;
        cancelBtn.innerHTML = 'Cancelando...';
        
        // Cancelar subida en progreso si la hay
        if (uploadAbortController) {
            uploadAbortController.abort();
            uploadAbortController = null;
        }

        // Cancelar tarea en el backend si ya se creó
        if (currentTaskId) {
            try {
                await fetch(`/api/cancel/${currentTaskId}`, { method: 'POST' });
                if (currentEvtSource) {
                    currentEvtSource.close();
                    currentEvtSource = null;
                }
                appendLog('🛑 Proceso cancelado por el usuario en el backend.', 'warning');
            } catch(e) {
                console.error(e);
            }
        }
        
        statusText.textContent = 'Proceso cancelado';
        spinner.style.display = 'none';
        progressBarFill.style.width = '0%';
        resetBtn.classList.remove('hidden');
        cancelBtn.style.display = 'none';
        cancelBtn.disabled = false;
        cancelBtn.innerHTML = `
            <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <circle cx="12" cy="12" r="10"></circle>
                <line x1="15" y1="9" x2="9" y2="15"></line>
                <line x1="9" y1="9" x2="15" y2="15"></line>
            </svg>
            Cancelar
        `;
        currentTaskId = null;
    });
}

if (resetBtn) {
    resetBtn.addEventListener('click', () => {
        // Olvidar el trabajo actual para no reabrirlo al recargar.
        localStorage.removeItem('mangascan_task');
        currentTaskId = null;

        progressContainer.classList.add('hidden');
        dropzone.classList.remove('hidden');
        fileInput.value = '';

        // Reset completion UI
        const terminalWrapper = document.getElementById('terminal-wrapper');
        const successBanner = document.getElementById('success-banner');
        if (terminalWrapper) terminalWrapper.style.display = 'block';
        if (successBanner) successBanner.classList.add('hidden');
        statusText.textContent = 'Procesando...';
        progressBarFill.style.width = '0%';
    });
}

// ─── Reconexión a un trabajo en curso (al recargar/volver) ──────────
async function reconnectExistingJob() {
    let jobList = [];
    try {
        const res = await fetch('/api/jobs');
        if (!res.ok) return;
        jobList = await res.json();
    } catch (e) {
        return; // servidor sin registro (recién reiniciado) o sin red
    }
    if (!Array.isArray(jobList) || !jobList.length) return;

    // 1) Prioridad: un trabajo activo en el servidor (corriendo o en cola).
    let job = jobList.find(j => j.status === 'running') || jobList.find(j => j.status === 'queued');

    // 2) Si no hay activo, recuperar el último que ESTE navegador estaba viendo,
    //    solo si ya terminó (para poder descargarlo).
    if (!job) {
        const lastId = localStorage.getItem('mangascan_task');
        if (lastId) {
            job = jobList.find(j => j.task_id === lastId && (j.status === 'done' || j.status === 'error'));
        }
    }
    if (!job) return;

    currentTaskId = job.task_id;
    localStorage.setItem('mangascan_task', job.task_id);

    // Cambiar a la vista de progreso.
    dropzone.classList.add('hidden');
    document.getElementById('confirmation-container')?.classList.add('hidden');
    progressContainer.classList.remove('hidden');
    terminal.innerHTML = '';
    progressBarFill.style.width = '0%';
    downloadBtn.classList.add('hidden');
    resetBtn.classList.remove('hidden');
    updateProgressBarAria(0);

    const activo = job.status === 'running' || job.status === 'queued';
    if (activo) {
        spinner.style.display = 'block';
        statusText.textContent = `Reconectando: ${job.filename}`;
        cancelBtn.style.display = 'inline-flex';
        cancelBtn.disabled = false;
        appendLog('🔄 Reconectado a un proceso que seguía corriendo en el servidor.', 'system');
    } else {
        spinner.style.display = 'none';
        statusText.textContent = `Último trabajo: ${job.filename}`;
        cancelBtn.style.display = 'none';
        appendLog('📂 Mostrando el resultado de tu último trabajo.', 'system');
    }

    // El stream reproduce el log completo y luego sigue en vivo; para un trabajo
    // ya terminado, disparará por sí mismo el estado de descarga.
    startLogStream(job.task_id, job.filename);
}

reconnectExistingJob();

// ─── Biblioteca ──────────────────────────────
function libStatusInfo(s) {
    const map = {
        done: ['Completado', '#10b981'],
        running: ['En curso', '#3b82f6'],
        queued: ['En cola', '#9ca3af'],
        error: ['Error', '#ef4444'],
        cancelled: ['Cancelado', '#f59e0b'],
        incomplete: ['Incompleto', '#9ca3af'],
    };
    return map[s] || [s, '#9ca3af'];
}

async function openLibrary() {
    const panel = document.getElementById('library-container');
    const list = document.getElementById('library-list');
    if (!panel || !list) return;

    // Ocultar el resto de vistas.
    dropzone.classList.add('hidden');
    document.getElementById('confirmation-container')?.classList.add('hidden');
    progressContainer.classList.add('hidden');
    panel.classList.remove('hidden');
    list.textContent = 'Cargando…';

    let entries = [];
    try {
        const res = await fetch('/api/library');
        entries = await res.json();
    } catch (e) {
        list.textContent = 'No se pudo cargar la biblioteca.';
        return;
    }
    renderLibrary(entries);
}

function renderLibrary(entries) {
    const list = document.getElementById('library-list');
    list.innerHTML = '';
    if (!Array.isArray(entries) || !entries.length) {
        list.textContent = 'No hay trabajos todavía.';
        return;
    }
    for (const e of entries) {
        const row = document.createElement('div');
        row.style.cssText = 'display:flex; align-items:center; gap:12px; padding:0.75rem 0; border-bottom:1px solid var(--border-light);';

        const info = document.createElement('div');
        info.style.cssText = 'flex:1; min-width:0;';
        const name = document.createElement('div');
        name.textContent = e.filename;
        name.style.cssText = 'font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;';
        const meta = document.createElement('div');
        meta.style.cssText = 'font-size:0.8rem; color:var(--text-secondary); margin-top:2px;';
        const [label, color] = libStatusInfo(e.status);
        const chip = document.createElement('span');
        chip.textContent = label;
        chip.style.cssText = `color:${color}; font-weight:600;`;
        meta.appendChild(chip);
        meta.appendChild(document.createTextNode(` · ${e.size_mb} MB${e.leftovers ? ' · restos temporales' : ''}`));
        info.appendChild(name);
        info.appendChild(meta);
        row.appendChild(info);

        if (e.has_pdf) {
            const dl = document.createElement('a');
            dl.href = `/api/download/${e.task_id}?filename=${encodeURIComponent(e.filename)}`;
            dl.className = 'btn primary-btn';
            dl.style.cssText = 'padding:0.4rem 0.9rem; font-size:0.85rem; flex-shrink:0;';
            dl.textContent = '⬇ Descargar';
            dl.setAttribute('download', '');
            row.appendChild(dl);
        }

        const del = document.createElement('button');
        del.className = 'btn';
        del.style.cssText = 'padding:0.4rem 0.9rem; font-size:0.85rem; flex-shrink:0; background:rgba(239,68,68,0.15); color:var(--accent-error); border:1px solid rgba(239,68,68,0.3);';
        del.textContent = '🗑 Eliminar';
        del.addEventListener('click', () => deleteJob(e.task_id, e.filename));
        row.appendChild(del);

        list.appendChild(row);
    }
}

async function deleteJob(taskId, filename) {
    if (!confirm(`¿Eliminar "${filename}"?\nSe borrarán su PDF, log y archivos temporales. Esta acción no se puede deshacer.`)) return;
    try {
        const res = await fetch(`/api/jobs/${taskId}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.status === 'running') {
            alert(data.error || 'El trabajo está en curso; cancélalo antes de eliminarlo.');
            return;
        }
    } catch (e) {
        alert('Error al eliminar el trabajo.');
        return;
    }
    openLibrary(); // refrescar la lista
}

document.getElementById('open-library-btn')?.addEventListener('click', openLibrary);
document.getElementById('close-library-btn')?.addEventListener('click', () => {
    document.getElementById('library-container')?.classList.add('hidden');
    dropzone.classList.remove('hidden');
});

// ─── Log Parser ──────────────────────────────

function parseLogLine(raw) {
    let text = raw;
    let type = 'system';

    // Strip common prefixes for cleaner display
    text = text.replace(/^\[SYSTEM\]\s*/i, '');

    if (/ERROR|FATAL|CRASH|EXCEPCIÓN|Error|🚨/i.test(raw)) {
        type = 'error';
    } else if (/WARN|ADVERTENCIA|⚠️/i.test(raw)) {
        type = 'warning';
    } else if (/SUCCESS|✅|finalizado|Completado|listo|📥/i.test(raw)) {
        type = 'success';
    } else if (/INFO|DEBUG|📡|ℹ️|🔍|📄/i.test(raw)) {
        type = 'info';
    }

    // Enrich with contextual emojis if missing
    if (!/^[📡✅❌🚨🔌🎉📥🔍📄ℹ️⚠️]/.test(text)) {
        if (type === 'error') text = `🚨 ${text}`;
        else if (type === 'warning') text = `⚠️ ${text}`;
        else if (type === 'success') text = `✅ ${text}`;
        else if (type === 'info') text = `ℹ️ ${text}`;
    }

    return { text, type };
}



function updateProgressBarAria(percent) {
    if (progressBarBg) {
        progressBarBg.setAttribute('aria-valuenow', Math.round(percent));
    }
}

