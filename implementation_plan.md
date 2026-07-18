# Plan de Implementación: Depuración y Preparación para Docker (NAS)

El objetivo de este plan es guiar al desarrollador en la refactorización, limpieza y fortalecimiento del código actual del traductor de manga. El sistema actual ya es funcional de principio a fin, pero requiere una capa final de pulido (manejo de errores robusto, limpieza de archivos temporales y optimización de la imagen) antes de compilarse en Docker y desplegarse permanentemente en un NAS.

## User Review Required

> [!IMPORTANT]
> **Gestión de Modelos de ML:** Actualmente `manga-image-translator` descarga modelos pesados en su primera ejecución. Debemos decidir si:
> 1. (Recomendado) Mapear una carpeta de caché externa en el NAS como volumen de Docker para que los modelos se descarguen allí y persistan entre reinicios.
> 2. Incluir el paso de descarga forzada dentro de la construcción del `Dockerfile` (hace la imagen mucho más pesada, pero 100% plug-and-play).

> [!WARNING]
> **Limpieza de archivos temporales:** El orquestador actualmente deja las imágenes intermedias (raw, jsons, render) en disco para facilitar la depuración. Para producción en Docker, necesitaremos una rutina que borre todo rastro temporal tras la generación exitosa del PDF, para no agotar el almacenamiento del NAS.

## Open Questions

- ¿Deseas que el sistema procese subcarpetas recursivamente (varios tomos a la vez), o el contenedor de Docker siempre se ejecutará pasándole de a un archivo PDF específico?
- ¿El programador requiere configurar notificaciones automáticas (ej. un webhook a Discord/Telegram) al terminar el PDF para que no tengas que estar revisando los logs del NAS?

## Proposed Changes

### Orquestador (Flujo principal)
Refinar la lógica central para que sea tolerante a fallos fatales e independiente del entorno de pruebas.

#### [MODIFY] [src/orquestador.py](file:///d:/Documentos/mangascan/src/orquestador.py)
- Añadir un bloque `finally` para la limpieza automática de la carpeta de trabajo temporal (`work_dir/raw`, `work_dir/render`, `work_dir/jsons`) una vez que el PDF final (`_traducido.pdf`) haya sido generado exitosamente.
- Fortalecer el manejo de permisos: envolver `shutil.copyfile` en bloques `try...except` que notifiquen si el NAS presenta restricciones de lectura/escritura en los volúmenes montados.
- Añadir parámetros por línea de comandos para activar o desactivar el modo "debug" (guardar temporales vs borrarlos).

### Lógica de Traducción (DeepSeek)
Consolidación de código y manejo de tasa de peticiones.

#### [MODIFY] [src/fase3_traducir.py](file:///d:/Documentos/mangascan/src/fase3_traducir.py)
- Externalizar completamente la validación de caracteres asiáticos. 
- Implementar un sistema de espera exponencial progresiva estricto (Exponential Backoff) para el error `HTTP 429 Too Many Requests`. Al procesar decenas de páginas rápido, la API puede limitar el uso; el script debe suspenderse por 60 segundos automáticamente en lugar de estrellarse.

### Configuración de Contenedorización
Optimización final para que el NAS consuma los menores recursos de CPU y RAM posibles.

#### [MODIFY] [Dockerfile](file:///d:/Documentos/mangascan/Dockerfile)
- Ajustar la declaración de `VOLUME` para incluir explícitamente `/root/.cache/manga-image-translator` (o similar), para persistir los modelos de OCR e inpainting localmente y que el contenedor inicie al instante.
- Definir variables de entorno predeterminadas `ENV PYTHONUNBUFFERED=1` para asegurar que los logs se emitan al sistema del NAS en tiempo real sin demoras de caché.

## Verification Plan

### Automated Tests
- Ejecutar un "dry-run" del contenedor Docker que procese un PDF de 2 páginas con inyección de fallo de red en la fase 3, verificando que el Exponential Backoff actúa correctamente.
- Validar mediante el log del sistema que la validación de la Regla de Oro (anti-chino) bloquea efectivamente respuestas falsas.

### Manual Verification
- Compilar la imagen con `docker build -t traductor-manga .`.
- Montar las carpetas de prueba locales como volúmenes simulando el NAS.
- Comprobar visualmente que, tras la ejecución del contenedor, las carpetas `/data/in/` no han sido alteradas, y en `/data/out/` se encuentra *exclusivamente* el archivo `manga_traducido.pdf` (y el archivo de resumen), sin rastros de imágenes PNG sueltas.
