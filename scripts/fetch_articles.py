
import os
import json
import time
import re
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

# =====================================================
# CONFIGURACION GENERAL
# =====================================================

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-3.5-flash-lite"

DATA_FILE = Path("data/articles.json")
MAX_ARTICLES_PER_RUN = 1
YEARS_BACK = 5

PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
EUROPE_PMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/"

USER_AGENT = "SPORT-LAB-Educational-Project/1.0"


# =====================================================
# PETICIONES HTTP
# =====================================================

def http_request(url, data=None, timeout=60, attempts=3,
                 content_type=None):

    headers = {"User-Agent": USER_AGENT}

    if data is not None:
        headers["Content-Type"] = (
            content_type or "application/x-www-form-urlencoded"
        )

    last_error = None

    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                url,
                data=data,
                headers=headers
            )

            with urllib.request.urlopen(
                request,
                timeout=timeout
            ) as response:
                return response.read().decode("utf-8")

        except Exception as error:
            last_error = error
            print(
                f"Intento {attempt + 1}/{attempts} fallido: {error}"
            )

            if attempt < attempts - 1:
                time.sleep(3 * (attempt + 1))

    raise RuntimeError(
        f"No se pudo completar la petición: {last_error}"
    )


# =====================================================
# FECHAS Y ARTICULOS EXISTENTES
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
# BUSQUEDA EN PUBMED
# =====================================================

def search_pubmed(existing_ids):
    print("Buscando investigaciones en PubMed...")

    start_date, end_date = get_date_range()

    search_terms = (
        '(exercise OR sport OR "physical activity" '
        'OR training OR sports science)'
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

    response = http_request(url)
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

    xml_data = http_request(fetch_url)
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
# BUSQUEDA EN EUROPE PMC
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

    response = http_request(url)
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
# BUSQUEDA COMBINADA
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
# GENERACION DEL ARTICULO CON GEMINI
# =====================================================

def generate_article(study):
    if not GEMINI_API_KEY:
        raise RuntimeError("Falta el secreto GEMINI_API_KEY.")

    print("Generando artículo educativo con Gemini...")

    prompt = f"""
Eres el redactor científico de SPORT LAB, una plataforma
educativa de ciencias del deporte para estudiantes de ESO
de entre 12 y 16 años.

Transforma el siguiente estudio científico en un artículo
educativo riguroso, atractivo y fácil de entender.

ESTUDIO ORIGINAL:

Título: {study['title']}
Revista: {study['journal']}
Año: {study['year']}
Resumen: {study['abstract']}

REGLAS:

- No inventes resultados ni conclusiones.
- No inventes cifras.
- Explica los conceptos para estudiantes de 12 a 16 años.
- Diferencia resultados de interpretaciones.
- Explica las limitaciones del estudio.
- No presentes correlaciones como causalidad.
- No des consejos médicos individuales.
- Escribe en español de España.
- El artículo debe ser claro, atractivo y didáctico.
- Incluye exactamente 5 preguntas tipo test.
- Cada pregunta debe tener 4 opciones.
- La respuesta correcta se indica mediante un número
  de opción empezando por 0.

Devuelve EXCLUSIVAMENTE un objeto JSON válido con esta estructura:

{{
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
      "options": [
        "Opción A",
        "Opción B",
        "Opción C",
        "Opción D"
      ],
      "correct": 0,
      "explanation": "Explicación de la respuesta"
    }}
  ]
}}

La categoría debe ser una de estas:
Entrenamiento, Salud, Nutrición, Cerebro, Fisiología,
Biomecánica o Psicología deportiva.
"""

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?"
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
            "temperature": 0.4,
            "responseMimeType": "application/json"
        }
    }

    response = http_request(
        url,
        data=json.dumps(body).encode("utf-8"),
        timeout=120,
        attempts=3,
        content_type="application/json"
    )

    result = json.loads(response)

    text = (
        result["candidates"][0]["content"]["parts"][0]["text"]
    )

    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)

    article = json.loads(text)

    required_fields = [
        "category", "icon", "type", "title", "teaser",
        "question", "study", "method", "results",
        "explained", "limits", "takeaway", "quiz"
    ]

    for field in required_fields:
        if field not in article:
            raise ValueError(f"Falta el campo: {field}")

    if len(article["quiz"]) != 5:
        raise ValueError("El artículo debe tener 5 preguntas.")

    for question in article["quiz"]:
        if (
            len(question.get("options", [])) != 4
            or question.get("correct") not in [0, 1, 2, 3]
        ):
            raise ValueError("Una pregunta tipo test no es válida.")

    return article


# =====================================================
# GUARDAR ARTICULO
# =====================================================

def save_article(article, study, existing_data):
    today = date.today().isoformat()

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
    article["published_at"] = today
    article["evidence_window_years"] = YEARS_BACK

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
# PROGRAMA PRINCIPAL
# =====================================================

def main():
    print("===================================")
    print("       SPORT LAB AUTOMATICO")
    print("===================================")

    print(f"Fecha: {date.today().isoformat()}")
    print(f"Modelo Gemini: {GEMINI_MODEL}")

    existing_data = load_existing_articles()

    existing_ids = get_existing_ids(
        existing_data["articles"]
    )

    candidates = find_studies(existing_ids)

    if not candidates:
        print("No hay estudios nuevos disponibles.")
        return

    published = 0

    for study in candidates:
        if published >= MAX_ARTICLES_PER_RUN:
            break

        try:
            article = generate_article(study)

            save_article(
                article,
                study,
                existing_data
            )

            published += 1
            break

        except Exception as error:
            print(
                f"No se pudo generar el artículo "
                f"'{study.get('title', '')}': {error}"
            )

    if published == 0:
        print("No se ha podido publicar ningún artículo.")

    print("Proceso SPORT LAB finalizado.")


if __name__ == "__main__":
    main()
