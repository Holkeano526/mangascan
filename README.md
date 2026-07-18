# Traductor de Manga Local (Japonés a Español Latino)

Una herramienta automatizada de código abierto para escanear, reconocer, traducir y renderizar mangas desde su idioma original (japonés) al español latino de forma desatendida. 

Este proyecto combina un procesamiento de imágenes y reconocimiento óptico de caracteres (OCR) ejecutado de forma 100% **local** (ideal para NAS o servidores con CPU o GPU integrada) con la poderosa y económica API de **DeepSeek V3** encargada exclusivamente de la traducción del texto.

## Características Principales
- **Extracción Inteligente:** Toma un archivo PDF (o una carpeta con imágenes) y lo prepara para traducción sin comprimirlo excesivamente.
- **OCR Local:** Utiliza [manga-image-translator](https://github.com/zyddnys/manga-image-translator) para detectar los globos de diálogo y extraer el texto en japonés, sin costo de API.
- **Inpainting Automático:** Limpia los globos de texto originales en la imagen, regenerando el fondo cuando el texto está encima de dibujos.
- **Traducción Contextual con DeepSeek:** Envía todos los textos de una página a la vez al modelo `deepseek-chat` para que traduzca con el contexto completo, aplicando un tono de manga natural y coloquial. 
- **Validación Anti-Asiática Estricta:** Incorpora una capa de seguridad para rechazar y reintentar si el modelo intenta devolver caracteres asiáticos en la traducción.
- **Renderizado Dinámico:** Redibuja el texto en español sobre la imagen ajustando la fuente.
- **Ensamblaje a PDF:** Reconstruye la obra completa y genera un archivo final `_traducido.pdf` conservando la calidad de lectura original.

---

## Requisitos Previos

- Python 3.11 o superior (si se instala de forma manual).
- Cuenta y API Key de **DeepSeek**.
- Docker (opcional, pero fuertemente recomendado para despliegues en servidores NAS).

---

## 🐳 Instalación y Uso con Docker (Recomendado para NAS)

El entorno contenedorizado garantiza que las dependencias conflictivas (como librerías de OpenCV y compiladores locales) no afecten tu sistema.

1. **Clonar el repositorio:**
   ```bash
   git clone https://github.com/tu-usuario/traductor-manga.git
   cd traductor-manga
   ```

2. **Construir la imagen de Docker:**
   ```bash
   docker build -t traductor-manga .
   ```

3. **Ejecutar el proceso:**
   Suponiendo que tienes una carpeta de entrada en tu servidor con tus PDFs, puedes montar los volúmenes para que el sistema lea y escriba en tu disco:
   ```bash
   docker run --rm \
     -v /ruta/absoluta/a/tus/mangas/in:/data/in \
     -v /ruta/absoluta/a/tus/mangas/out:/data/out \
     -v /ruta/absoluta/cache/manga-translator:/root/.cache/manga-image-translator \
     -e DEEPSEEK_API_KEY=tu_api_key_aqui \
     traductor-manga /data/in/tomo_original.pdf --work-dir /data/out/tomo_traducido
   ```
   > **Nota de caché:** Mapear el volumen `/root/.cache/manga-image-translator` es vital para que los modelos de Machine Learning para el OCR y el Inpainting (de varios gigabytes) solo se descarguen la primera vez y persistan entre ejecuciones.

### 🚀 Optimización Extrema para NAS (Procesadores Intel sin GPU dedicada)
Si tu servidor o NAS tiene un procesador Intel moderno (ej. i5-1135G7) que no cuenta con tarjeta gráfica dedicada NVIDIA, puedes compilar una versión súper acelerada del proyecto usando el archivo alternativo `Dockerfile.nas`. 

Esta imagen reemplaza el motor base por **Intel OpenMP (MKL)**, ancla el procesamiento a los núcleos físicos reales de tu CPU (evitando los hilos virtuales) y exprime las instrucciones AVX-512 y la Caché L3 para que la Inferencia de la IA trabaje a máxima velocidad por software. 

Para usar esta versión específica en tu NAS, compila con este comando:
```bash
docker build -t traductor-manga -f Dockerfile.nas .
```
*(El comando `docker run` posterior será exactamente el mismo).*

---

## 💻 Instalación y Uso Manual (Windows / WSL / Linux)

Si prefieres ejecutarlo en tu propio entorno:

1. **Crear y activar el entorno virtual:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # En Linux/Mac o WSL
   # o en Windows: venv\Scripts\activate
   ```

2. **Instalar dependencias:**
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. **Configurar API Key:**
   Crea un archivo llamado `.env` en la **raíz del proyecto** (la misma carpeta donde está este README) con el siguiente contenido:
   ```env
   DEEPSEEK_API_KEY="sk-tu_api_key_aqui"
   ```
   Alternativamente, puedes exportar la variable de entorno directamente en tu terminal:
   ```bash
   export DEEPSEEK_API_KEY="sk-tu_api_key_aqui"
   ```

4. **Ejecución del orquestador:**
   Puedes pasarle un PDF completo o una carpeta con imágenes.
   ```bash
   python -m src.orquestador "data/input/manga.pdf" --work-dir "data/output/manga_es" -v
   ```
   
   **Parámetros adicionales:**
   - `--force`: Ignora los archivos ya procesados y fuerza una nueva traducción total de cada página.
   - `--debug`: Evita que el orquestador borre las carpetas temporales (`raw/`, `jsons/`, `render/`) al finalizar el PDF, útil para diagnósticos o para arreglar traducciones de páginas puntuales.
   - `-v` o `--verbose`: Muestra logs paso a paso.

---

## 📂 Arquitectura Interna del Pipeline

El `src/orquestador.py` organiza el proceso en 5 fases secuenciales ininterrumpidas:

1. **Extracción (PyMuPDF):** Descompone el archivo PDF de entrada en imágenes individuales PNG en alta resolución (carpeta `raw/`).
2. **Detección y OCR Local:** Utiliza el motor local para localizar las cajas delimitadoras de los globos y extraer los caracteres japoneses.
3. **Traducción en Lote:** Unifica los textos de la página actual, los envía estructurados en JSON a DeepSeek y recupera sus correspondientes traducciones al español. Cuenta con mitigación de errores `HTTP 429` (Exponential Backoff).
4. **Inpainting & Render:** Borra el texto de la imagen cruda utilizando la máscara detectada en la fase 2 y dibuja la fuente ajustando los límites. (Carpeta `render/`).
5. **Reconstrucción Final:** Lee las imágenes traducidas de la carpeta render (junto con las páginas sin texto detectado) y vuelve a unificar el libro en formato `.pdf` con la coletilla `_traducido`.

*Tras el punto final, si el flag `--debug` no está activo, el sistema purga automáticamente todos los ficheros intermedios pesados.*

---

## ⚠️ Consideraciones de la IA de Traducción
El sistema de escaneo omite deliberadamente los **SFX (Efectos de sonido)** que estén incrustados en el arte de manera orgánica, ya que la red neuronal de `manga-image-translator` se entrena sobre globos de texto convencionales. En estos casos, las onomatopeyas se mantienen intactas tal cual como la versión japonesa original.

---
**Proyecto listo para producción (Escala de tomos pesados).**
