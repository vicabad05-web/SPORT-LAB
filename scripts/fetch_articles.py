
#!/usr/bin/env python3
# ============================================================
# SPORT LAB - MOTOR CIENTIFICO AUTOMATICO
# PubMed + Europe PMC + Gemini
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
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EPMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"

USER_AGENT = "SPORT-LAB/2.0 educational science project"

CATEGORIES = [
    "Cerebro",
    "Rendimiento",
    "Cuerpo humano",
    "Nutrición",
    "Descanso",
    "Salud"
]

TODAY = date.today()
START_YEAR = TODAY.year - 5


# ------------------------------------------------------------
# PETICIONES HTTP CON REINTENTOS
# ------------------------------------------------------------

def http_request(url, data=None, timeout=90, attempts=4):

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*"
    }

    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    last_error = None

    for attempt in range(attempts):

        try:
            request = urllib.request.Request(
                url,
                data=data,
                headers=headers,
                method="POST" if data is not None else "GET"
            )

            with urllib.request.urlopen(
                request,
                timeout=timeout
            ) as response:
                return response.read()

        except urllib.error.HTTPError as error:

            last_error = error

            retry_after = error.headers.get("Retry-After")
            wait = int(retry_after) if retry_after and retry_after.isdigit() else 5 * (attempt + 1)

            print(
                f"HTTP {error.code}. "
                f"Intento {attempt + 1}/{attempts}"
            )

            if error.code not in (429, 500, 502, 503, 504):
                raise

            if attempt < attempts - 1:
                time.sleep(min(wait, 30))

        except Exception as error:

            last_error = error

            print(
                f"Error de conexion. "
                f"Intento {attempt + 1}/{attempts}: {error}"
            )

            if attempt < attempts - 1:
                time.sleep(5 * (attempt + 1))

    raise RuntimeError(
        f"Peticion fallida despues de varios intentos: {last_error}"
    )


def get_json(url):
    content = http_request(url)
    return json.loads(content.decode("utf-8"))


# ------------------------------------------------------------
# ALMACENAMIENTO
# ------------------------------------------------------------

def load_articles():

    ARTICLES_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    if not ARTICLES_FILE.exists():
        return {"articles": []}

    data = json.loads(
        ARTICLES_FILE.read_text(encoding="utf-8")
    )

    if isinstance(data, list):
        return {"articles": data}

    if not isinstance(data, dict):
        return {"articles": []}

    if not isinstance(data.get("articles"), list):
        data["articles"] = []

    return data


def existing_identifiers(data):

    return {
        str(article.get("id"))
        for article in data["articles"]
        if isinstance(article, dict) and article.get("id")
    }


# ------------------------------------------------------------
# BUSQUEDA PUBMED
# ------------------------------------------------------------

def search_pubmed(existing_ids):

    print("Buscando investigaciones en PubMed...")

    query = (
        '("Exercise"[Title/Abstract] OR '
        '"physical activity"[Title/Abstract] OR '
        '"sport"[Title/Abstract] OR '
        '"sleep"[Title/Abstract] OR '
        '"nutrition"[Title/Abstract] OR '
        '"hydration"[Title/Abstract] OR '
        '"muscle"[Title/Abstract] OR '
        '"brain"[Title/Abstract]) AND '
        '("Adolescent"[MeSH Terms] OR '
        '"Child"[MeSH Terms] OR '
        '"Young Adult"[MeSH Terms] OR '
        'adolescent[Title/Abstract] OR '
        'children[Title/Abstract] OR '
        'youth[Title/Abstract]) AND '
        f'("{START_YEAR}/01/01"[Date - Publication] : '
        f'"{TODAY.isoformat()}"[Date - Publication])'
    )

    params = urllib.parse.urlencode({
        "db": "pubmed",
        "term": query,
        "retmode": "json",
        "retmax": 30,
        "sort": "date"
    })

    url = f"{NCBI_BASE}/esearch.fcgi?{params}"

    result = get_json(url)

    ids = result.get("esearchresult", {}).get("idlist", [])

    print(f"PubMed: {len(ids)} registros encontrados.")

    if not ids:
        return []

    fetch_params = urllib.parse.urlencode({
        "db": "pubmed",
        "id": ",".join(ids),
        "retmode": "xml"
    })

    xml_content = http_request(
        f"{NCBI_BASE}/efetch.fcgi?{fetch_params}",
        timeout=120
    )

    root = ET.fromstring(xml_content)

    candidates = []

    for article in root.findall(".//PubmedArticle"):

        try:
            pmid = article.findtext(
                ".//PMID",
                default=""
            ).strip()

            if not pmid or f"pmid-{pmid}" in existing_ids:
                continue

            title_node = article.find(".//ArticleTitle")

            if title_node is None:
                continue

            title = " ".join(
                "".join(title_node.itertext()).split()
            )

            abstract_parts = []

            for node in article.findall(
                ".//Abstract/AbstractText"
            ):
                label = node.attrib.get("Label", "")
                text = " ".join(
                    "".join(node.itertext()).split()
                )

                if text:
                    abstract_parts.append(
                        f"{label}: {text}" if label else text
                    )

            abstract = "\n".join(abstract_parts)

            if len(abstract) < 150:
                continue

            authors = []

            for author in article.findall(
                ".//AuthorList/Author"
            ):

                last = author.findtext("LastName", "")
                fore = author.findtext("ForeName", "")

                name = " ".join(
                    part for part in [fore, last] if part
                )

                if name:
                    authors.append(name)

            journal = article.findtext(
                ".//Journal/Title",
                default=""
            )

            pub_date = article.find(".//PubDate")

            year = ""

            if pub_date is not None:
                year = (
                    pub_date.findtext("Year")
                    or pub_date.findtext("MedlineDate", "")[:4]
                )

            if not year or not year.isdigit():
                continue

            publication_year = int(year)

            if publication_year < START_YEAR:
                continue

            if publication_year > TODAY.year:
                continue

            doi = ""

            for identifier in article.findall(
                ".//ArticleId"
            ):
                if identifier.attrib.get("IdType") == "doi":
                    doi = identifier.text or ""
                    break

            candidates.append({

                "id": f"pmid-{pmid}",
                "source": "PubMed",
                "title": title,
                "authors": ", ".join(authors[:12]),
                "journal": journal,
                "year": publication_year,
                "doi": doi,
                "pmid": pmid,
                "abstract": abstract,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

            })

        except Exception as error:
            print("Registro PubMed descartado:", error)

    print(f"PubMed: {len(candidates)} candidatos utilizables.")

    return candidates


# ------------------------------------------------------------
# BUSQUEDA EUROPE PMC
# ------------------------------------------------------------

def search_europe_pmc(existing_ids):

    print("Buscando investigaciones en Europe PMC...")

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
        f'FIRST_PDATE:[{START_YEAR}-01-01 TO {TODAY.isoformat()}]'
    )

    params = urllib.parse.urlencode({
        "query": query,
        "format": "json",
        "pageSize": 30,
        "sort": "FIRST_PDATE_D"
    })

    url = f"{EPMC_BASE}/search?{params}"

    result = get_json(url)

    records = result.get(
        "resultList", {}
    ).get("result", [])

    candidates = []

    for item in records:

        pmid = item.get("pmid", "")
        pmcid = item.get("pmcid", "")

        if pmid:
            identifier = f"pmid-{pmid}"
        elif pmcid:
            identifier = f"pmcid-{pmcid}"
        else:
            continue

        if identifier in existing_ids:
            continue

        try:

            year = int(item.get("pubYear", 0))

            if year < START_YEAR or year > TODAY.year:
                continue

            abstract = item.get("abstractText", "")

            if len(abstract) < 150:
                continue

            candidates.append({

                "id": identifier,
                "source": "Europe PMC",
                "title": item.get("title", ""),
                "authors": item.get("authorString", ""),
                "journal": item.get("journalTitle", ""),
                "year": year,
                "doi": item.get("doi", ""),
                "pmid": str(pmid),
                "abstract": abstract,
                "url": (
                    f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                    if pmid
                    else f"https://europepmc.org/article/PMC/{pmcid}"
                )

            })

        except Exception as error:
            print("Registro Europe PMC descartado:", error)

    print(
        f"Europe PMC: {len(candidates)} candidatos utilizables."
    )

    return candidates


# ------------------------------------------------------------
# COMBINAR Y EVITAR DUPLICADOS
# ------------------------------------------------------------

def collect_studies(data):

    identifiers = existing_identifiers(data)

    candidates = []
    seen = set(identifiers)

    for search_function in (
        search_pubmed,
        search_europe_pmc
    ):

        try:

            results = search_function(seen)

            for study in results:

                identifier = study["id"]

                if identifier in seen:
                    continue

                seen.add(identifier)
                candidates.append(study)

        except Exception as error:

            print(
                f"Fuente {search_function.__name__} no disponible: "
                f"{error}"
            )

    candidates.sort(
        key=lambda item: item["year"],
        reverse=True
    )

    print(
        f"Total de estudios candidatos: {len(candidates)}"
    )

    return candidates


# ------------------------------------------------------------
# GENERAR ARTICULO CON GEMINI
# ------------------------------------------------------------

def generate_article(study):

    prompt = f"""
Eres el redactor cientifico de SPORT LAB.

Tu publico son estudiantes espanoles de ESO de 12 a 16 anos.

Redacta un articulo atractivo, claro y cientificamente riguroso.

REGLAS:

- Utiliza exclusivamente la informacion del estudio.
- No inventes resultados ni cifras.
- No inventes autores ni referencias.
- Distingue correlacion de causalidad.
- Explica las limitaciones.
- No des consejos medicos individuales.
- No conviertas una hipotesis en un resultado demostrado.
- Si el estudio no permite una explicacion fiable, publica=false.

Devuelve exclusivamente JSON valido con esta estructura:

{{
  "publish": true,
  "category": "Rendimiento",
  "icon": "🏃",
  "type": "Investigacion cientifica",
  "title": "Pregunta atractiva",
  "teaser": "Resumen breve",
  "question": "Pregunta principal",
  "study": "Que investigaron",
  "method": "Como lo investigaron",
  "results": "Resultados reales",
  "explained": "Explicacion para estudiantes de ESO",
  "limits": "Limitaciones del estudio",
  "takeaway": "Idea clave",
  "read_minutes": 5,
  "quiz": [
    {{
      "q": "Pregunta",
      "options": ["A", "B", "C"],
      "correct": 0,
      "explanation": "Explicacion"
    }}
  ]
}}

REQUISITOS DEL QUIZ:

- Exactamente cinco preguntas.
- Tres opciones por pregunta.
- Una unica respuesta correcta.
- Preguntas basadas en el estudio.
- Explicaciones comprensibles.

CATEGORIAS:

{", ".join(CATEGORIES)}

REFERENCIA:

Fuente: {study["source"]}
Titulo: {study["title"]}
Autores: {study["authors"]}
Revista: {study["journal"]}
Ano: {study["year"]}
DOI: {study["doi"]}
PMID: {study["pmid"]}
Enlace: {study["url"]}

RESUMEN ORIGINAL:

{study["abstract"]}
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

    body = json.dumps(payload).encode("utf-8")

    content = http_request(
        url,
        data=body,
        timeout=120,
        attempts=3
    )

    result = json.loads(content.decode("utf-8"))

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

    required = [
        "category", "icon", "type", "title", "teaser",
        "question", "study", "method", "results",
        "explained", "limits", "takeaway"
    ]

    for field in required:

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

        if not isinstance(options, list) or len(options) != 3:
            return False

        if any(
            not isinstance(option, str) or not option.strip()
            for option in options
        ):
            return False

        if type(question.get("correct")) is not int:
            return False

        if question["correct"] not in (0, 1, 2):
            return False

        if not isinstance(question.get("explanation"), str):
            return False

        if len(question["explanation"].strip()) < 8:
            return False

    return True


# ------------------------------------------------------------
# GUARDAR ARTICULO
# ------------------------------------------------------------

def save_article(data, study, generated):

    article = {

        "id": study["id"],
        "source": study["source"],

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
        "url": study["url"],

        "published_at": TODAY.isoformat(),
        "evidence_window_years": 5

    }

    data["articles"].insert(0, article)

    data["updated_at"] = TODAY.isoformat()

    data["automation"] = {
        "sources": ["PubMed", "Europe PMC"],
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

    print("ARTICULO GUARDADO:", article["title"])


# ------------------------------------------------------------
# PROCESO PRINCIPAL
# ------------------------------------------------------------

def main():

    print("====================================")
    print("          SPORT LAB")
    print("     MOTOR CIENTIFICO AUTOMATICO")
    print("====================================")

    if not GEMINI_API_KEY:
        raise RuntimeError(
            "Falta GEMINI_API_KEY en GitHub Secrets."
        )

    print(
        "Fecha:",
        datetime.now(
            timezone.utc
        ).astimezone(
            ZoneInfo("Europe/Madrid")
        ).isoformat()
    )

    data = load_articles()

    candidates = collect_studies(data)

    if not candidates:

        print(
            "No hay estudios adecuados. "
            "No se publicara contenido de relleno."
        )

        return

    for study in candidates[:8]:

        try:

            print(
                "Analizando estudio:",
                study["title"]
            )

            generated = generate_article(study)

            if not validate_article(generated):

                print(
                    "Estudio descartado: "
                    "el articulo no supera la validacion."
                )

                continue

            save_article(
                data,
                study,
                generated
            )

            print("PUBLICACION COMPLETADA.")

            return

        except Exception as error:

            print(
                "No se pudo procesar este estudio:",
                error
            )

            continue

    print(
        "No se encontro ningun articulo que "
        "superara las comprobaciones."
    )


if __name__ == "__main__":
    main()
