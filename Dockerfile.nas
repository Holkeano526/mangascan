# Dockerfile.nas
# Optimizado para procesadores Intel con AVX-512 y sin GPU dedicada (Ej: i5-1135G7)

FROM python:3.11-slim

# Instalar dependencias del sistema necesarias
# - libgomp1 y libiomp-dev: Librerías de OpenMP para exprimir los hilos del procesador
RUN apt-get update && apt-get install -y \
    git \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    libiomp-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar e instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instalar Intel OpenMP para máxima aceleración en CPUs Intel (Tiger Lake)
RUN pip install --no-cache-dir intel-openmp

# Variables de entorno críticas para optimización en Intel CPU (NAS i5-1135G7)
# Tu CPU tiene 4 núcleos físicos / 8 hilos. MKL funciona mejor limitándolo a los núcleos físicos.
ENV OMP_NUM_THREADS=4
ENV MKL_NUM_THREADS=4
# Evitar saltos de hilos entre núcleos para reducir latencia caché L3
ENV KMP_AFFINITY=granularity=fine,compact,1,0
ENV KMP_BLOCKTIME=1
ENV LD_PRELOAD=/usr/local/lib/python3.11/site-packages/libiomp5.so

# Copiar el código fuente
COPY src/ ./src/

# Volúmenes
VOLUME ["/data/in", "/data/out", "/root/.cache/manga-image-translator"]

# Variables de ejecución
ENV DEEPSEEK_API_KEY=""
ENV PYTHONUNBUFFERED=1

# Punto de entrada
ENTRYPOINT ["python", "-m", "src.orquestador"]
CMD ["--help"]
