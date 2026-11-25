# Imagen de producción del NAS (receta OMV "mangascan").
#
# Parte de la imagen construida el 17/08/2026 con ./Dockerfile, congelada con la
# etiqueta mangascan-ai:base-20260817. Reconstruir desde cero ya no es fiable:
# manga-image-translator (rama main) y python:3.11-slim cambiaron y el build se rompe.
# Los cambios incrementales van aquí como capas nuevas.
#
# Si se pierde la etiqueta base: docker tag <id de la imagen buena> mangascan-ai:base-20260817
FROM mangascan-ai:base-20260817

# Detector INT8 en cascada (src/detector_cascada.py). Modelo en /config/models/openvino.
RUN pip install --no-cache-dir "openvino>=2026.4,<2027"

# Runtime de GPU Intel para la iGPU Iris Xe (src/aceleracion.py). Debian 13 no lo trae
# y la rama "legacy1" de Intel es solo para Gen9-11: Tiger Lake (Gen12) va en la
# rama actual. Paquetes oficiales de GitHub, verificados por SHA-256 (tools/igpu.sha256).
ARG CR=26.40.40196.8
ARG IGC=2.42.6
COPY tools/igpu.sha256 /tmp/igpu.sha256
RUN apt-get update \
 && apt-get install -y --no-install-recommends ocl-icd-libopencl1 curl ca-certificates \
 && mkdir /tmp/igpu && cd /tmp/igpu \
 && curl -fsSLO "https://github.com/intel/compute-runtime/releases/download/${CR}/intel-opencl-icd_${CR}-0_amd64.deb" \
 && curl -fsSLO "https://github.com/intel/compute-runtime/releases/download/${CR}/libze-intel-gpu1_${CR}-0_amd64.deb" \
 && curl -fsSLO "https://github.com/intel/compute-runtime/releases/download/${CR}/libigdgmm12_22.10.2_amd64.deb" \
 && curl -fsSL -o "intel-igc-core-2_${IGC}+23070_amd64.deb" "https://github.com/intel/intel-graphics-compiler/releases/download/v${IGC}/intel-igc-core-2_${IGC}%2B23070_amd64.deb" \
 && curl -fsSL -o "intel-igc-opencl-2_${IGC}+23070_amd64.deb" "https://github.com/intel/intel-graphics-compiler/releases/download/v${IGC}/intel-igc-opencl-2_${IGC}%2B23070_amd64.deb" \
 && sha256sum -c /tmp/igpu.sha256 \
 && dpkg -i ./*.deb \
 && cd / && rm -rf /tmp/igpu /tmp/igpu.sha256 /var/lib/apt/lists/*

# El código también se monta en vivo desde /datos/src/mangascan/src (ver receta),
# pero se copia para que la imagen funcione por sí sola.
COPY src/ ./src/
