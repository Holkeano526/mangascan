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

# El código también se monta en vivo desde /datos/src/mangascan/src (ver receta),
# pero se copia para que la imagen funcione por sí sola.
COPY src/ ./src/
