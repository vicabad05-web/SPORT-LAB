
#!/usr/bin/env python3
# ============================================================
# SPORT LAB
# Buscador y redactor automatico de articulos cientificos
# Investigaciones de los ultimos cinco anos
# ============================================================

import json
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


# ------------------------------------------------------------
# CONFIGURACION
# ------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]

ARTICLES_FILE = ROOT / "data" / "articles.json"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
)

EUROPE_PMC = (
    "https://www.ebi.ac.uk/europepmc/"
    "webservices/rest"
)

CATEGORIES = [
    "Cerebro",
    "Rendimiento",
    "Cuerpo humano",
    "Nutrición",
    "Descanso",
    "Salud"
]

USER_AGENT = "SPORT-LAB/2.0 educational science project"


# ------------------------------------------------------------
# PETICIONES HTTP CON REINTENTOS
# ------------------------------------------------------------

def request_url(url, timeout=120, attempts=3):

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*"
        }
    )

    last_error = None

    for attempt in range(attempts):

        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout
            ) as response:

                return response.read()

        except Exception as error:

            last_error = error

            print(
                f"Intento {attempt + 1}/{attempts} "
                f"fallido: {error}"
            )

            if attempt < attempts - 1:
                time.sleep(5 * (attempt + 1))

    raise RuntimeError(
        f"No se pudo completar la petición: {last_error}"
    )


def get_json(url):

    content = request_url(
        url,
        timeout=120,
        attempts=3
    )

    return json.loads(
        content.decode("utf-8")
    )


# ------------------------------------------------------------
# CARGAR ARTICULOS EXISTENTES
# ------------------------------------------------------------

def load_articles():

    ARTICLES_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    if not ARTICLES_FILE.exists():
        return {"articles": []}

    try:
        data = json.loads(
            ARTICLES_FILE.read_text(
                encoding="utf-8"
            )
        )

        if isinstance(data, list):
            return {"articles": data}

        if not isinstance(data, dict):
            return {"articles": []}

        if not isinstance(data.get("articles"), list):
            data["articles"] = []

        return data

    except Exception as error:

        raise RuntimeError(
            f"No se pudo leer articles.json: {error}"
        )


# ------------------------------------------------------------
# DESCARGAR TEXTO CIENTIFICO
# ------------------------------------------------------------

def get_full_text(pmcid):

    url = (
        f"{EUROPE_PMC}/{pmcid}/fullTextXML"
    )

    try:

        content = request_url(
            url,
            timeout=60,
            attempts=2
        )

        root = ET.fromstring(content)

        paragraphs = []

        for node in root.iter():

            tag = node.tag.split("}")[-1]

            if tag not in ("title", "p"):
                continue

            text = " ".join(
                "".join(node.itertext()).split()
            )

            if len(text) > 45:
                paragraphs.append(text)

        return "\n".join(paragraphs)[:16000]

    except Exception as error:

        print(
            f"No se pudo descargar el texto completo "
            f"de {pmcid}: {error}"
        )

        return ""


# ------------------------------------------------------------
# BUSCAR INVESTIGACIONES DE LOS ULTIMOS CINCO ANOS
# ------------------------------------------------------------

def find_studies(existing_ids):

    today = date.today()

    start_year = today.year - 5

    start_date = date(
        start_year,
        today.month,
        min(today.day, 28)
    )

    query = (
        'OPEN_ACCESS:Y AND HAS_ABSTRACT:Y AND '
        '(TITLE_ABS:exercise OR '
        'TITLE_ABS:"physical activity" OR '
        'TITLE_ABS:sport OR '
        'TITLE_ABS:sleep OR '
        'TITLE_ABS:nutrition OR '
        'TITLE_ABS:hydration OR '
        'TITLE_ABS:muscle OR '
        'TITLE_ABS:brain) AND '
        '(TITLE_ABS:adolescent OR '
        'TITLE_ABS:children OR '
        'TITLE_ABS:youth OR '
        'TITLE_ABS:young) AND '
        f'FIRST_PDATE:[{start_date.isoformat()} '
        f'TO {today.isoformat()}]'
    )

    params = urllib.parse.urlencode({
        "query": query,
        "format": "json",
        "pageSize": 25,
        "sort": "FIRST_PDATE_D"
    })

    url = (
        f"{EUROPE_PMC}/search?{params}"
    )

    print("Buscando investigaciones en Europe PMC...")

    data = get_json(url)

    results = data.get(
        "resultList", {}
    ).get("result", [])

    candidates = []

    for item in results:

        pmcid = item.get("pmcid")
        pmid = item.get("pmid")
        doi = item.get("doi")

        if not pmcid:
            continue

        identifier = (
            f"pmid-{pmid}"
            if pmid
            else f"pmcid-{pmcid}"
        )

        if identifier in existing_ids:
            continue

        try:

            publication_date = item.get(
                "firstPublicationDate", ""
            )

            publication_year = int(
                item.get("pubYear", 0)
            )

            if not publication_date:
                continue

            published = date.fromisoformat(
                publication_date[:10]
            )

            if published < start_date or published > today:
                continue

            if not item.get("abstractText"):
                continue

            full_text = get_full_text(pmcid)

            candidates.append({

                "id": identifier,

                "title": item.get(
                    "title", ""
                ),

                "authors": item.get(
                    "authorString", ""
                ),

                "journal": item.get(
                    "journalTitle", ""
                ),

                "year": publication_year,

                "publication_date": publication_date,

                "doi": doi or "",

                "pmid": str(pmid or ""),

                "pmcid": pmcid,

                "abstract": item.get(
                    "abstractText", ""
                ),

                "full_text": full_text,

                "url": (
                    f"https://europepmc.org/article/MED/{pmid}"
                    if pmid
                    else f"https://europepmc.org/article/PMC/{pmcid}"
                )

            })

        except Exception as error:

            print(
                "Estudio descartado:",
                error
            )

            continue

    print(
        f"Investigaciones candidatas encontradas: "
        f"{len(candidates)}"
    )

    return candidates


# ------------------------------------------------------------
# REDACTAR ARTICULO CON GEMINI
# ------------------------------------------------------------

def generate_article(study):

    prompt = f"""
Eres el redactor cientifico de SPORT LAB.

SPORT LAB es una plataforma educativa para estudiantes
espanoles de Educacion Secundaria Obligatoria (12-16 anos).

Debes redactar un articulo educativo atractivo y riguroso.

REGLAS OBLIGATORIAS:

1. Utiliza exclusivamente los datos del estudio proporcionado.
2. No inventes autores, resultados, cifras ni conclusiones.
3. No confundas correlacion con causalidad.
4. Explica las limitaciones de la investigacion.
5. Utiliza lenguaje comprensible para adolescentes.
6. No des consejos medicos individuales.
7. Si la evidencia no permite una explicacion fiable,
   devuelve publish=false.
8. No presentes hipotesis como hechos demostrados.

Devuelve exclusivamente JSON valido.

ESTRUCTURA:

{{
  "publish": true,
  "category": "Rendimiento",
  "icon": "🏃",
  "type": "Investigacion cientifica",
  "title": "Pregunta atractiva para adolescentes",
  "teaser": "Resumen breve y atractivo",
  "question": "Pregunta principal del articulo",
  "study": "Que investigaron los cientificos",
  "method": "Como realizaron la investigacion",
  "results": "Resultados que encontraron",
  "explained": "Explicacion sencilla para ESO",
  "limits": "Limitaciones del estudio",
  "takeaway": "Idea cientifica principal",
  "read_minutes": 5,
  "quiz": [
    {{
      "q": "Pregunta de comprension",
      "options": ["Opcion A", "Opcion B", "Opcion C"],
      "correct": 0,
      "explanation": "Explicacion de la respuesta"
    }}
  ]
}}

REQUISITOS DEL CUESTIONARIO:

- Exactamente cinco preguntas.
- Tres opciones por pregunta.
- Una sola respuesta correcta.
- Preguntas basadas en el estudio.
- Explicaciones claras.

CATEGORIAS PERMITIDAS:

{", ".join(CATEGORIES)}

REFERENCIA ORIGINAL:

Titulo: {study["title"]}
Autores: {study["authors"]}
Revista: {study["journal"]}
Ano: {study["year"]}
DOI: {study["doi"]}
PMID: {study["pmid"]}

RESUMEN CIENTIFICO:

{study["abstract"]}

TEXTO COMPLETO DISPONIBLE:

{study["full_text"][:16000]}
"""

    payload = {

        "contents": [{
            "parts": [{
                "text": prompt
            }]
        }],

        "generationConfig": {

            "temperature": 0.2,

            "responseMimeType": "application/json"

        }

    }

    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{GEMINI_MODEL}:generateContent?"
        + urllib.parse.urlencode({
            "key": GEMINI_API_KEY
        })
    )

    request = urllib.request.Request(

        url,

        data=json.dumps(payload).encode("utf-8"),

        headers={
            "Content-Type": "application/json"
        },

        method="POST"

    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=120
        ) as response:

            result = json.loads(
                response.read().decode("utf-8")
            )

    except urllib.error.HTTPError as error:

        detail = error.read().decode(
            "utf-8",
            errors="replace"
        )

        raise RuntimeError(
            f"Error de Gemini HTTP {error.code}: "
            f"{detail[:1000]}"
        )

    text = (
        result["candidates"][0]
        ["content"]["parts"][0]["text"]
    )

    return json.loads(text)


# ------------------------------------------------------------
# VALIDAR ARTICULO
# ------------------------------------------------------------

def validate_article(article):

    if not isinstance(article, dict):
        return False

    if article.get("publish") is not True:
        return False

    required_fields = [

        "category",
        "icon",
        "type",
        "title",
        "teaser",
        "question",
        "study",
        "method",
        "results",
        "explained",
        "limits",
        "takeaway"

    ]

    for field in required_fields:

        value = article.get(field)

        if not isinstance(value, str):
            return False

        if len(value.strip()) < 20:
            return False

    if article["category"] not in CATEGORIES:
        return False

    quiz = article.get("quiz")

    if not isinstance(quiz, list) or len(quiz) != 5:
        return False

    for question in quiz:

        if not isinstance(question, dict):
            return False

        if not isinstance(question.get("q"), str):
            return False

        options = question.get("options")

        if not isinstance(options, list):
            return False

        if len(options) != 3:
            return False

        if type(question.get("correct")) is not int:
            return False

        if question["correct"] not in (0, 1, 2):
            return False

        if not isinstance(
            question.get("explanation"), str
        ):
            return False

    return True


# ------------------------------------------------------------
# GUARDAR ARTICULO
# ------------------------------------------------------------

def save_article(data, study, generated):

    today = date.today()

    article = {

        "id": study["id"],

        "category": generated["category"],

        "icon": generated["icon"],

        "type": generated["type"],

        "title": generated["title"],

        "teaser": generated["teaser"],

        "question": generated["question"],

        "study": generated["study"],

        "method": generated["method"],

        "results": generated["results"],

        "explained": generated["explained"],

        "limits": generated["limits"],

        "takeaway": generated["takeaway"],

        "read_minutes": int(
            generated.get("read_minutes", 5)
        ),

        "quiz": generated["quiz"],

        "authors": study["authors"],

        "paper_title": study["title"],

        "journal": study["journal"],

        "year": study["year"],

        "doi": study["doi"],

        "pmid": study["pmid"],

        "pmcid": study["pmcid"],

        "url": study["url"],

        "published_at": today.isoformat(),

        "evidence_window_years": 5

    }

    data["articles"].insert(0, article)

    data["updated_at"] = today.isoformat()

    data["automation"] = {

        "source": "Europe PMC",

        "model": GEMINI_MODEL,

        "period": "Ultimos cinco anos"

    }

    ARTICLES_FILE.write_text(

        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ) + "\n",

        encoding="utf-8"

    )

    print(
        "ARTICULO GUARDADO:",
        article["title"]
    )


# ------------------------------------------------------------
# PROCESO PRINCIPAL
# ------------------------------------------------------------

def main():

    print("===================================")
    print("        SPORT LAB AUTOMATICO")
    print("===================================")

    if not GEMINI_API_KEY:

        raise RuntimeError(
            "Falta GEMINI_API_KEY. "
            "Configura el secreto en GitHub Actions."
        )

    madrid_now = datetime.now(
        timezone.utc
    ).astimezone(
        ZoneInfo("Europe/Madrid")
    )

    print(
        "Fecha y hora de Madrid:",
        madrid_now.isoformat()
    )

    data = load_articles()

    existing_ids = {

        article.get("id")

        for article in data["articles"]

        if isinstance(article, dict)

    }

    candidates = find_studies(existing_ids)

    if not candidates:

        print(
            "No se encontraron estudios adecuados. "
            "No se publicara contenido de relleno."
        )

        return

    for study in candidates[:5]:

        try:

            print(
                "Analizando:",
                study["title"]
            )

            generated = generate_article(study)

            if not validate_article(generated):

                print(
                    "Articulo descartado por validacion."
                )

                continue

            save_article(
                data,
                study,
                generated
            )

            print(
                "PUBLICACION COMPLETADA CORRECTAMENTE."
            )

            return

        except Exception as error:

            print(
                "Error procesando estudio:",
                error
            )

            continue

    print(
        "No se pudo generar un articulo que "
        "superara las comprobaciones."
    )


if __name__ == "__main__":
    main()
