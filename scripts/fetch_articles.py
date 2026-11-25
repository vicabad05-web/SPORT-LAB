
import os
import json
import time
import random
import re
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

# =====================================================
# 1. CONFIGURACION
# =====================================================

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

GEMINI_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.8-flash"
]

DATA_FILE = Path("data/articles.json")
MAX_ARTICLES_PER_RUN = 1
YEARS_BACK = 5

PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
EUROPE_PMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/"

USER_AGENT = "SPORT-LAB-Educational-Project/1.0"


# =====================================================
# 2. PETICIONES HTTP
# =====================================================

def http_request(url, data=None, timeout=90, content_type=None):

    headers = {"User-Agent": USER_AGENT}

    if data is not None:
        headers["Content-Type"] = (
            content_type or "application/x-www-form-urlencoded"
        )

    request = urllib.request.Request(
        url,
        data=data,
        headers=headers
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def get_with_retries(url, data=None, timeout=90,
                     content_type=None, attempts=4):

    transient_codes = [408, 429, 500, 502, 503, 504]

    for attempt in range(attempts):
        try:
            return http_request(
                url,
                data=data,
                timeout=timeout,
                content_type=content_type
            )

        except urllib.error.HTTPError as error:

            print(f"Error HTTP {error.code}: {error.reason}")

            if error.code not in transient_codes:
                raise

            if attempt == attempts - 1:
                raise

            wait = min(5 * (2 ** attempt), 60)
            wait += random.uniform(0, 3)

            print(f"Servicio ocupado. Esperando {wait:.1f} segundos...")
            time.sleep(wait)

        except Exception as error:

            if attempt == attempts - 1:
                raise

            wait = min(5 * (2 ** attempt), 60)

            print(f"Error de conexión: {error}")
            print(f"Nuevo intento en {wait} segundos...")
            time.sleep(wait)

    raise RuntimeError("Se agotaron los intentos de conexión.")


# =====================================================
# 3. FECHAS Y ARTICULOS EXISTENTES
# =====================================================

def get_date_range():
    end_date = date.today()
    start_date = end_date - timedelta(days=365 * YEARS_BACK)
    return start_date, end_date


def load_existing_articles():

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    if not DATA_FILE.exists():
        return {"articles": []}

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data.get("articles"), list):
            return {"articles": []}

        return data

    except Exception as error:
        print(f"Error leyendo artículos: {error}")
        return {"articles": []}


def get_existing_ids(articles):

    identifiers = set()

    for article in articles:
        for field in ("doi", "pmid", "id"):
            value = article.get(field)

            if value:
                identifiers.add(str(value).lower())

    return identifiers


# =====================================================
# 4. BUSQUEDA EN PUBMED
# =====================================================

def search_pubmed(existing_ids):

    print("Buscando investigaciones en PubMed...")

    start_date, end_date = get_date_range()

    search_terms = (
        '("Exercise"[MeSH Terms] OR "Sports"[MeSH Terms] '
        'OR "Motor Activity"[MeSH Terms] OR exercise[Title/Abstract] '
        'OR sport[Title/Abstract] OR "physical activity"[Title/Abstract] '
        'OR training[Title/Abstract])'
        ' AND humans[MeSH Terms]'
    )

    params = {
        "db": "pubmed",
        "term": search_terms,
        "retmax": "30",
        "retmode": "json",
        "datetype": "pdat",
        "mindate": start_date.strftime("%Y/%m/%d"),
        "maxdate": end_date.strftime("%Y/%m/%d"),
        "sort": "date"
    }

    url = PUBMED_BASE + "esearch.fcgi?" + urllib.parse.urlencode(params)

    response = get_with_retries(url)
    data = json.loads(response)

    pmids = data.get("esearchresult", {}).get("idlist", [])

    if not pmids:
        print("PubMed no ha devuelto resultados.")
        return []

    fetch_params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml"
    }

    fetch_url = PUBMED_BASE + "efetch.fcgi?" + urllib.parse.urlencode(
        fetch_params
    )

    xml_data = get_with_retries(fetch_url)
    root = ET.fromstring(xml_data)

    candidates = []

    for item in root.findall(".//PubmedArticle"):

        pmid = item.findtext(".//PMID")

        if not pmid or pmid.lower() in existing_ids:
            continue

        title = item.findtext(".//ArticleTitle") or ""

        abstract_parts = []

        for element in item.findall(".//Abstract/AbstractText"):
            text = "".join(element.itertext()).strip()

            if text:
                abstract_parts.append(text)

        abstract = " ".join(abstract_parts)

        journal = item.findtext(".//Journal/Title") or ""

        year = (
            item.findtext(".//PubDate/Year")
            or item.findtext(".//PubDate/MedlineDate")
            or ""
        )

        doi = ""

        for identifier in item.findall(".//ArticleId"):
            if identifier.attrib.get("IdType") == "doi":
                doi = identifier.text or ""
                break

        if not title or not abstract:
            continue

        if doi and doi.lower() in existing_ids:
            continue

        candidates.append({
            "source": "PubMed",
            "pmid": pmid,
            "doi": doi,
            "title": title,
            "abstract": abstract,
            "journal": journal,
            "year": year,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        })

    print(f"PubMed: {len(candidates)} estudios encontrados.")

    return candidates


# =====================================================
# 5. BUSQUEDA EN EUROPE PMC
# =====================================================

def search_europe_pmc(existing_ids):

    print("Buscando investigaciones en Europe PMC...")

    start_date, end_date = get_date_range()

    query = (
        '(TITLE_ABS:exercise OR TITLE_ABS:sport '
        'OR TITLE_ABS:"physical activity" '
        'OR TITLE_ABS:training)'
        f' AND FIRST_PDATE:[{start_date.isoformat()} '
        f'TO {end_date.isoformat()}]'
        ' AND HAS_ABSTRACT:Y'
    )

    params = {
        "query": query,
        "format": "json",
        "pageSize": "30",
        "sort": "FIRST_PDATE_D desc"
    }

    url = EUROPE_PMC_BASE + "search?" + urllib.parse.urlencode(params)

    response = get_with_retries(url)
    data = json.loads(response)

    candidates = []

    for item in data.get("resultList", {}).get("result", []):

        pmid = item.get("pmid", "")
        doi = item.get("doi", "")
        title = item.get("title", "")
        abstract = item.get("abstractText", "")
        journal = item.get("journalTitle", "")
        year = item.get("firstPublicationDate", "")[:4]

        identifiers = []

        if pmid:
            identifiers.append(pmid.lower())

        if doi:
            identifiers.append(doi.lower())

        if any(identifier in existing_ids for identifier in identifiers):
            continue

        if not title or not abstract:
            continue

        if pmid:
            article_url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        elif doi:
            article_url = f"https://doi.org/{doi}"
        else:
            article_url = "https://europepmc.org/"

        candidates.append({
            "source": "Europe PMC",
            "pmid": pmid,
            "doi": doi,
            "title": title,
            "abstract": abstract,
            "journal": journal,
            "year": year,
            "url": article_url
        })

    print(f"Europe PMC: {len(candidates)} estudios encontrados.")

    return candidates


# =====================================================
# 6. BUSQUEDA COMBINADA
# =====================================================

def find_studies(existing_ids):

    candidates = []

    try:
        candidates.extend(search_pubmed(existing_ids))

    except Exception as error:
        print(f"AVISO: PubMed no disponible: {error}")

    try:
        candidates.extend(search_europe_pmc(existing_ids))

    except Exception as error:
        print(f"AVISO: Europe PMC no disponible: {error}")

    unique = []
    seen = set()

    for item in candidates:

        identifier = (
            item.get("doi")
            or item.get("pmid")
            or item.get("title")
        ).lower()

        if identifier not in seen:
            seen.add(identifier)
            unique.append(item)

    if not unique:
        print("No se han podido obtener estudios.")
        return []

    return unique


# =====================================================
# 7. FILTRO CIENTIFICO Y PROMPT EDUCATIVO
# =====================================================

SCIENTIFIC_THRESHOLD = 70
MAX_CANDIDATES_TO_SCREEN = 5

def build_prompt(study):

    return f"""
Eres el equipo de evaluación científica y redacción de SPORT LAB,
una plataforma educativa de ciencias del deporte para alumnado de ESO
de entre 12 y 16 años.

Tu tarea tiene DOS FASES:
1) Evaluar la calidad y relevancia científica del estudio.
2) Si obtiene 70 puntos o más sobre 100, redactar el artículo educativo.

ESTUDIO ORIGINAL:
Título: {study['title']}
Revista: {study['journal']}
Año: {study['year']}
Fuente: {study['source']}
Resumen: {study['abstract']}

FASE 1. EVALUACIÓN CIENTÍFICA (0-100):
Puntúa con criterios explícitos:
- Relevancia para ciencias del deporte, actividad física, ejercicio,
  salud, nutrición, fisiología, biomecánica, cerebro o psicología deportiva: 0-30.
- Calidad y adecuación del diseño metodológico: 0-25.
- Solidez del análisis, resultados y conclusiones: 0-20.
- Calidad de la muestra, datos o fuentes utilizadas: 0-15.
- Transparencia, reconocimiento de limitaciones y riesgo de sesgo: 0-10.

La suma debe ser exactamente 100 puntos como máximo.
No penalices automáticamente un estudio por ser observacional, cualitativo,
transversal, longitudinal, ensayo clínico, revisión sistemática o metaanálisis.
Valora cada diseño según lo que puede demostrar y la calidad con que se realizó.
Incluye todos los diseños relevantes: ensayos, estudios observacionales,
cohortes, casos y controles, estudios transversales, cualitativos,
revisiones sistemáticas y metaanálisis.
No confundas el tipo de estudio con su calidad. No otorgues una puntuación alta
solo por aparecer en una revista conocida.
Si el resumen no aporta información suficiente para evaluar un criterio,
no inventes datos y asigna una puntuación prudente.

Identifica el diseño del estudio y explica brevemente la puntuación.
Si la puntuación total es inferior a {SCIENTIFIC_THRESHOLD}, devuelve
"article": null. No redactes un artículo para ese estudio.

FASE 2. ARTÍCULO:
Solo si la puntuación es igual o superior a {SCIENTIFIC_THRESHOLD},
redacta un artículo educativo riguroso, atractivo y comprensible para ESO.

REGLAS DEL ARTÍCULO:
- No inventes resultados, cifras, autores ni conclusiones.
- Usa únicamente la información del estudio proporcionado.
- Diferencia resultados de interpretaciones.
- Explica las limitaciones del estudio.
- No presentes correlaciones como causalidad.
- No des consejos médicos individuales.
- Escribe en español de España.
- Incluye exactamente 5 preguntas tipo test, con 4 opciones cada una.
- "correct" debe ser el índice de la respuesta correcta: 0, 1, 2 o 3.
- La categoría debe ser una de estas: Entrenamiento, Salud, Nutrición,
  Cerebro, Fisiología, Biomecánica o Psicología deportiva.

Devuelve EXCLUSIVAMENTE JSON válido con esta estructura:
{{
  "scientific_score": 0,
  "score_breakdown": {{
    "relevance": 0,
    "methodology": 0,
    "analysis": 0,
    "sample_or_sources": 0,
    "transparency": 0
  }},
  "study_design": "Tipo de diseño",
  "scientific_reason": "Justificación breve de la puntuación",
  "article": {{
    "category": "Entrenamiento",
    "icon": "🏃",
    "type": "Investigación",
    "title": "Título atractivo en español",
    "teaser": "Resumen breve que despierte curiosidad",
    "question": "Pregunta inicial para el estudiante",
    "study": "Qué investigaron los científicos",
    "method": "Cómo realizaron el estudio",
    "results": "Qué encontraron",
    "explained": "Explicación científica adaptada a ESO",
    "limits": "Limitaciones del estudio",
    "takeaway": "Idea principal que debe recordar el alumno",
    "read_minutes": 5,
    "quiz": [
      {{
        "question": "Pregunta",
        "options": ["Opción A", "Opción B", "Opción C", "Opción D"],
        "correct": 0,
        "explanation": "Explicación de la respuesta"
      }}
    ]
  }}
}}
Si scientific_score es inferior a {SCIENTIFIC_THRESHOLD}, article debe ser null.
"""


# =====================================================
# 8. EVALUACION Y GENERACION CON GEMINI
# =====================================================

def generate_with_model(study, model):

    prompt = build_prompt(study)

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?"
        f"key={urllib.parse.quote(GEMINI_API_KEY)}"
    )

    body = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json"
        }
    }

    response = get_with_retries(
        url,
        data=json.dumps(body).encode("utf-8"),
        timeout=180,
        content_type="application/json",
        attempts=4
    )

    result = json.loads(response)
    text = result["candidates"][0]["content"]["parts"][0]["text"]
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)

    return json.loads(text)


def validate_scientific_evaluation(evaluation):

    score = evaluation.get("scientific_score")

    if not isinstance(score, (int, float)) or not 0 <= score <= 100:
        raise ValueError("La puntuación científica no es válida.")

    breakdown = evaluation.get("score_breakdown", {})
    score_fields = [
        "relevance", "methodology", "analysis",
        "sample_or_sources", "transparency"
    ]

    if not all(field in breakdown for field in score_fields):
        raise ValueError("Falta el desglose de la puntuación científica.")

    if sum(breakdown[field] for field in score_fields) != score:
        raise ValueError("El desglose no coincide con la puntuación total.")

    if score >= SCIENTIFIC_THRESHOLD:
        article = evaluation.get("article")
        if not isinstance(article, dict):
            raise ValueError("El estudio supera el filtro, pero no hay artículo.")
        validate_article(article)
    elif evaluation.get("article") is not None:
        raise ValueError("Un estudio por debajo del umbral no debe generar artículo.")

    return evaluation


def validate_article(article):

    required_fields = [
        "category", "icon", "type", "title", "teaser",
        "question", "study", "method", "results",
        "explained", "limits", "takeaway", "quiz"
    ]

    for field in required_fields:
        if field not in article or article[field] is None:
            raise ValueError(f"Falta el campo obligatorio: {field}")

    if not isinstance(article["quiz"], list) or len(article["quiz"]) != 5:
        raise ValueError("El artículo debe tener exactamente 5 preguntas.")

    for question in article["quiz"]:
        if (
            len(question.get("options", [])) != 4
            or question.get("correct") not in [0, 1, 2, 3]
        ):
            raise ValueError("Una pregunta tipo test no es válida.")

    return article


def evaluate_and_generate(study):

    if not GEMINI_API_KEY:
        raise RuntimeError("Falta el secreto GEMINI_API_KEY.")

    last_error = None

    for model in GEMINI_MODELS:
        print(f"Evaluando estudio con Gemini: {model}")

        try:
            evaluation = generate_with_model(study, model)
            evaluation = validate_scientific_evaluation(evaluation)

            score = int(evaluation["scientific_score"])
            print(f"Puntuación científica: {score}/100")
            print(f"Diseño: {evaluation.get('study_design', 'No especificado')}")
            print(f"Justificación: {evaluation.get('scientific_reason', '')}")

            if score < SCIENTIFIC_THRESHOLD:
                print(
                    f"Estudio descartado: no alcanza el umbral "
                    f"de {SCIENTIFIC_THRESHOLD}/100."
                )
                return None

            article = evaluation["article"]
            article["scientific_score"] = score
            article["scientific_threshold"] = SCIENTIFIC_THRESHOLD
            article["scientific_score_breakdown"] = evaluation["score_breakdown"]
            article["study_design"] = evaluation.get(
                "study_design", "No especificado"
            )
            article["scientific_reason"] = evaluation.get(
                "scientific_reason", ""
            )
            article["ai_model"] = model

            print(f"Estudio aceptado y artículo generado con {model}.")
            return article

        except Exception as error:
            last_error = error
            print(f"Fallo en {model}: {error}")
            print("Se probará el siguiente modelo si está disponible.")

    raise RuntimeError(
        f"No se pudo evaluar y generar el artículo con Gemini: {last_error}"
    )


# =====================================================
# 9. GUARDAR ARTICULO
# =====================================================

def save_article(article, study, existing_data):

    article["id"] = (
        study.get("pmid")
        or study.get("doi")
        or str(int(time.time()))
    )

    article["authors"] = ""
    article["paper_title"] = study["title"]
    article["journal"] = study["journal"]
    article["year"] = study["year"]
    article["doi"] = study["doi"]
    article["pmid"] = study["pmid"]
    article["url"] = study["url"]
    article["source"] = study["source"]
    article["published_at"] = date.today().isoformat()
    article["evidence_window_years"] = YEARS_BACK
    article["scientific_threshold"] = SCIENTIFIC_THRESHOLD

    existing_data["articles"].insert(0, article)

    with open(DATA_FILE, "w", encoding="utf-8") as file:
        json.dump(
            existing_data,
            file,
            ensure_ascii=False,
            indent=2
        )

    print("Artículo guardado correctamente.")
    print(f"Título: {article['title']}")


# =====================================================
# 10. PROGRAMA PRINCIPAL
# =====================================================

def main():

    print("======================================")
    print("          SPORT LAB AUTOMATICO")
    print("======================================")

    print(f"Fecha: {date.today().isoformat()}")
    print(f"Filtro científico: mínimo {SCIENTIFIC_THRESHOLD}/100")
    print(f"Ventana de búsqueda: últimos {YEARS_BACK} años")
    print("Diseños: todos los diseños relevantes, incluidas revisiones")
    print("sistemáticas y metaanálisis.")
    print("Modelos disponibles:", ", ".join(GEMINI_MODELS))

    existing_data = load_existing_articles()
    existing_ids = get_existing_ids(existing_data["articles"])
    candidates = find_studies(existing_ids)

    if not candidates:
        print("No hay estudios nuevos disponibles.")
        return

    published = 0
    screened = 0

    # Se revisa un número limitado de candidatos para controlar
    # el consumo de la cuota gratuita de Gemini.
    for study in candidates[:MAX_CANDIDATES_TO_SCREEN]:

        if published >= MAX_ARTICLES_PER_RUN:
            break

        print("--------------------------------------")
        print("Estudio candidato:")
        print(study["title"])

        try:
            screened += 1
            article = evaluate_and_generate(study)

            if article is None:
                continue

            save_article(article, study, existing_data)
            existing_ids = get_existing_ids(existing_data["articles"])
            published += 1

        except Exception as error:
            print(f"Error procesando el estudio: {error}")
            print("Se continuará con el siguiente candidato.")

    if published == 0:
        print("No se ha publicado ningún artículo en esta ejecución.")

    print("--------------------------------------")
    print(f"Estudios evaluados: {screened}")
    print(f"Artículos publicados: {published}")
    print(f"Umbral científico aplicado: {SCIENTIFIC_THRESHOLD}/100")
    print("Proceso SPORT LAB finalizado.")


if __name__ == "__main__":
    main()
