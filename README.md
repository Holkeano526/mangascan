# Mangascan

Bot/Web server de traducción de mangas, optimizado para ejecutarse en contenedores (como en NAS) y con colas asíncronas para el uso eficiente de memoria RAM.

## Características
- **Arquitectura Modular (FastAPI)**: Código limpio y organizado por capas (`api`, `schemas`, `services`), lo que permite que el servidor web nunca se bloquee, incluso durante trabajos pesados de traducción.
- **Procesamiento Asíncrono (`asyncio`)**: Las tareas de traducción intensivas (uso de CPU y memoria) se ejecutan en un worker en segundo plano. 
- **Manejo Eficiente de Memoria para PDFs (Optimizado para NAS)**: Evita cierres forzados por el OOM Killer (Out Of Memory) al ensamblar PDFs con muchísimas páginas utilizando `img2pdf` y compresión iterativa en lugar de cargar todo el documento a la memoria RAM simultáneamente.
- **Builds Rápidos**: Empaquetado y gestión de dependencias ágil usando `uv` y `pyproject.toml`.

## Instalación y Despliegue (Docker)

1. **Clonar y compilar:**
```bash
git pull
docker compose up -d --build
```
2. **Acceso a la interfaz web y API:**
Una vez en ejecución, puedes usar la interfaz gráfica accediendo a la IP de tu servidor (ej. `http://<TU_NAS_IP>:8001/`). 
La documentación automática de la API está disponible en `http://<TU_NAS_IP>:8001/docs`.

## Script Salvavidas (`rescue.py`)

Si por alguna razón (como un reinicio inesperado o interrupción manual) la traducción de un manga finalizó pero falló en el último paso de **ensamblaje del PDF**, no tienes que empezar de cero ni volver a traducir todas las páginas.

Puedes utilizar el script `rescue.py` para buscar las imágenes que ya están traducidas en la carpeta de trabajo, comprimirlas eficientemente, y volver a generar el PDF final.

### ¿Cómo usarlo?
Simplemente ejecuta el script dentro del contenedor en ejecución:
```bash
docker exec mangascan-ai python src/rescue.py
```

El script automáticamente:
1. Buscará tareas que tengan imágenes en su directorio de `render`.
2. Redimensionará y comprimirá las imágenes para que el PDF sea ligero (aprox. 100-250MB para mangas grandes, en lugar de +1GB).
3. Ensamblará las páginas una por una y generará el archivo PDF final optimizado en la carpeta correspondiente.
