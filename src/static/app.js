const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('fileInput');
const progressContainer = document.getElementById('progress-container');
const statusText = document.getElementById('status-text');
const terminal = document.getElementById('terminal');
const downloadBtn = document.getElementById('download-btn');
const spinner = document.querySelector('.spinner');
const progressBarFill = document.getElementById('progress-bar-fill');

// Click to select
dropzone.addEventListener('click', () => fileInput.click());

// Drag and drop handlers
['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
    dropzone.addEventListener(eventName, preventDefaults, false);
});

function preventDefaults(e) {
    e.preventDefault();
    e.stopPropagation();
}

['dragenter', 'dragover'].forEach(eventName => {
    dropzone.addEventListener(eventName, () => dropzone.classList.add('dragover'), false);
});

['dragleave', 'drop'].forEach(eventName => {
    dropzone.addEventListener(eventName, () => dropzone.classList.remove('dragover'), false);
});

dropzone.addEventListener('drop', handleDrop, false);
fileInput.addEventListener('change', handleFiles, false);

function handleDrop(e) {
    const dt = e.dataTransfer;
    const files = dt.files;
    handleFiles({ target: { files: files } });
}

async function handleFiles(e) {
    const files = e.target.files;
    if (files.length === 0) return;
    
    const file = files[0];
    if (file.type !== "application/pdf") {
        alert("Por favor sube un archivo PDF.");
        return;
    }

    // Prepare UI
    dropzone.classList.add('hidden');
    progressContainer.classList.remove('hidden');
    terminal.innerHTML = '';
    statusText.textContent = `Procesando: ${file.name}`;
    progressBarFill.style.width = '0%';
    
    // Upload
    const formData = new FormData();
    formData.append('file', file);
    
    appendLog(`[INFO] Subiendo archivo al NAS...`, 'info');

    try {
        const response = await fetch('/api/upload', {
            method: 'POST',
            body: formData
        });
        
        const data = await response.json();
        
        if (data.task_id) {
            appendLog(`[INFO] Archivo subido. ID Tarea: ${data.task_id}`, 'success');
            startLogStream(data.task_id, data.filename);
        } else {
            appendLog(`[ERROR] Error al subir el archivo.`, 'error');
        }
    } catch (err) {
        appendLog(`[ERROR] Error de red: ${err.message}`, 'error');
    }
}

function startLogStream(taskId, filename) {
    const evtSource = new EventSource(`/api/stream/${taskId}`);
    let totalPages = 1;
    
    evtSource.onmessage = function(event) {
        const line = event.data;
        appendLog(line);
        
        // Extraer progreso de paginas: "Página 5/63"
        const pageMatch = line.match(/Página (\d+)\/(\d+)/);
        if (pageMatch) {
            const current = parseInt(pageMatch[1]);
            totalPages = parseInt(pageMatch[2]);
            const percent = (current / totalPages) * 100;
            progressBarFill.style.width = `${percent}%`;
            statusText.textContent = `Traduciendo... ${current}/${totalPages}`;
        }

        // Si terminó
        if (line.includes('[SYSTEM] Procesamiento finalizado')) {
            evtSource.close();
            progressBarFill.style.width = `100%`;
            statusText.textContent = "¡Traducción Completada!";
            spinner.classList.add('hidden');
            
            // Setup download button
            downloadBtn.href = `/api/download/${taskId}?filename=${encodeURIComponent(filename)}`;
            downloadBtn.classList.remove('hidden');
            downloadBtn.classList.add('success-btn');
            
            appendLog(`[SUCCESS] PDF listo para descargar.`, 'success');
        }
    };
    
    evtSource.onerror = function() {
        evtSource.close();
        appendLog(`[ERROR] Conexión al stream perdida.`, 'error');
    };
}

function appendLog(msg, type = '') {
    const div = document.createElement('div');
    div.textContent = msg;
    if (type) div.className = type;
    
    // Simple colorizing based on log level if no specific type passed
    if (!type) {
        if (msg.includes('ERROR')) div.className = 'error';
        else if (msg.includes('INFO') || msg.includes('DEBUG')) div.className = 'info';
        else if (msg.includes('SUCCESS') || msg.includes('completada')) div.className = 'success';
    }

    terminal.appendChild(div);
    terminal.scrollTop = terminal.scrollHeight; // Auto-scroll
}
