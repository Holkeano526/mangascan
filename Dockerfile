# Dockerfile para el Traductor de Manga desde PDF
# Basado en el plan del proyecto (Arquitectura Moderna)

FROM python:3.11-slim

# COPY uv binary from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Instalar dependencias del sistema necesarias para OpenCV/PyMuPDF/manga-image-translator
RUN apt-get update && apt-get install -y \
    git \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    build-essential \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Clonar manga-image-translator completo porque su paquete de pip está roto/incompleto.
# Fijado al commit con el que se construyó la imagen en producción (17/08/2026).
# OJO: con MIT_REF=main el build falla desde nov-2026 (pide networkx 3.7 -> Python 3.12).
# En el NAS no se reconstruye desde aquí: se usa Dockerfile.nas sobre la imagen congelada.
ARG MIT_REF=95227a2bb0fd306cd4f0c104d57284026f991b3a
RUN git clone https://github.com/zyddnys/manga-image-translator.git /app/manga-image-translator && \
    cd /app/manga-image-translator && git checkout "${MIT_REF}" && \
    pip install --no-cache-dir -r /app/manga-image-translator/requirements.txt

# Configurar PYTHONPATH para que los scripts en src/ encuentren a manga_translator
ENV PYTHONPATH="/app/manga-image-translator:${PYTHONPATH}"

# Enrutar la carpeta models de manga_translator hacia /config para persistencia y permisos
RUN rm -rf /app/manga-image-translator/models && \
    ln -s /config/models /app/manga-image-translator/models

# Copiar configuración del proyecto e instalar dependencias con uv
COPY pyproject.toml README.md ./
RUN uv pip install --system --no-cache .

# Copiar el código fuente
COPY src/ ./src/

# NAS Optimization: Mapear todo el caché de modelos pesados a /config
VOLUME ["/app/data", "/config"]

# Exponer el puerto para la interfaz web
EXPOSE 8000

# Variables de entorno crícticas para enrutar el caché fuera de /root (para OMV non-root)
ENV DEEPSEEK_API_KEY=""
ENV PYTHONUNBUFFERED=1
ENV XDG_CACHE_HOME="/config/cache"
ENV TORCH_HOME="/config/cache/torch"
ENV HF_HOME="/config/cache/huggingface"
ENV YOLO_CONFIG_DIR="/config/cache/yolo"

# Punto de entrada (Servidor Web reestructurado)
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]