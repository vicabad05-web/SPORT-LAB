# SPORT LAB — automatización gratuita

Plataforma educativa de ciencia del deporte y cuerpo humano para alumnado de ESO (12–16 años), preparada para GitHub Pages y GitHub Actions.

## Qué hace

- Publica automáticamente un artículo nuevo los lunes, miércoles y viernes, con objetivo de las 08:00 (hora peninsular de Madrid).
- Busca investigaciones en Europe PMC publicadas dentro de los cinco años anteriores a la fecha de ejecución.
- Solo considera estudios de acceso abierto con resumen y texto completo disponible en Europe PMC.
- Usa Gemini API (`gemini-3.1-flash-lite`) en su nivel gratuito para adaptar la investigación a un artículo en español y crear un cuestionario de cinco preguntas.
- Copia autores, título, revista, año, DOI, PMID, PMCID y enlace desde Europe PMC; no permite que la IA invente esos metadatos.
- Comprueba estructura, categoría, cinco preguntas y ventana temporal antes de guardar el artículo.
- No requiere aprobación manual. Si no hay estudio apto, la IA rechaza el candidato o la cuota gratuita se agota, no se publica un artículo nuevo y se conserva la biblioteca existente.

## Coste y límites

El proyecto no configura facturación ni utiliza servicios de pago. Europe PMC es gratuito. Gemini API dispone de un nivel gratuito limitado, pero Google puede modificar modelos, disponibilidad y cuotas. Por tanto, no se puede garantizar disponibilidad ilimitada ni exactamente tres artículos cada semana. En el nivel gratuito, Google indica que el contenido puede utilizarse para mejorar sus productos; no envíes datos personales ni información sensible.

GitHub Actions y GitHub Pages se utilizan para ejecutar el proceso y alojar el sitio. Para repositorios públicos, los runners estándar de GitHub Actions son gratuitos según las condiciones vigentes de GitHub.

## Configuración obligatoria (una sola vez)

1. Crea una clave API gratuita en [Google AI Studio](https://aistudio.google.com/app/apikey). No actives facturación si quieres mantener el proyecto sin coste.
2. En GitHub abre tu repositorio `Sport-News` → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.
3. Nombre del secreto: `GEMINI_API_KEY`.
4. Valor: pega la clave creada en Google AI Studio. Guarda el secreto. No lo escribas en el código, en `index.html` ni en archivos públicos.
5. En **Settings → Pages**, selecciona **GitHub Actions** como método de publicación.
6. Sustituye el workflow antiguo de noticias por `.github/workflows/publish.yml`. Sube también `scripts/fetch_articles.py` actualizado y conserva `data/articles.json`.
7. En **Actions**, ejecuta una vez **SPORT LAB - artículos automáticos y publicación** con **Run workflow** para probarlo.

## Horario

GitHub Actions programa en UTC. El workflow tiene dos disparadores (06:00 y 07:00 UTC) y el script solo sigue cuando la hora local de `Europe/Madrid` está en las 08:00. Así se adapta al horario de verano e invierno. GitHub puede retrasar los eventos programados, por lo que no es una garantía de minuto exacto.

## Política editorial y límites

La IA redacta a partir del texto completo recuperado en Europe PMC. Esto reduce el riesgo de inventar información, pero no equivale a una revisión por un especialista y no garantiza ausencia de errores. La validación automática comprueba formato y procedencia bibliográfica, no puede certificar científicamente cada frase. Se recomienda revisar ocasionalmente la web como responsable docente, aunque el flujo no exige aprobación por artículo.

No se garantiza un artículo en cada ejecución: si no encuentra un estudio elegible, no puede leer el texto completo, la API gratuita alcanza su cuota o el contenido no supera las validaciones, no publica. No se inventan artículos para completar el calendario.

## Estructura

- `index.html`: interfaz, biblioteca, cuestionarios y gamificación.
- `data/articles.json`: artículos visibles en la web.
- `scripts/fetch_articles.py`: búsqueda, generación y validación automática.
- `.github/workflows/publish.yml`: calendario y despliegue.

## Privacidad y gamificación

El progreso de XP se guarda en el navegador de cada alumno (`localStorage`). No hay cuentas, clasificación pública ni recopilación de nombres.
