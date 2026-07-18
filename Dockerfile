# Dockerfile para el Traductor de Manga desde PDF
# Basado en el plan del proyecto

FROM python:3.11-slim

# Instalar dependencias del sistema necesarias para OpenCV/PyMuPDF/manga-image-translator
RUN apt-get update && apt-get install -y \
    git \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar requirements primero para cachear capa de dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código fuente
COPY src/ ./src/

# Volúmenes para entrada/salida y caché de modelos ML
# Entrada: montar PDFs en /data/in
# Salida: resultados en /data/out
# Caché: persistir modelos de manga-image-translator (OCR, inpainting, etc.)
VOLUME ["/data/in", "/data/out", "/root/.cache/manga-image-translator"]

# Variables de entorno
ENV DEEPSEEK_API_KEY=""
ENV PYTHONUNBUFFERED=1

# Punto de entrada
ENTRYPOINT ["python", "-m", "src.orquestador"]
CMD ["--help"]