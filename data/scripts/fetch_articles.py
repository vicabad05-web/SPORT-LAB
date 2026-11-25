
#!/usr/bin/env python3
# SPORT LAB - Buscador y redactor científico automático
# Estudios de los últimos cinco años. Publicación sin aprobación manual.

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

ROOT = Path(__file__).resolve().parents[1]
ARTICLES_FILE = ROOT / "data" / "articles.json"

MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

CATEGORIES = {
    "Cerebro",
    "Rendimiento",
    "Cuerpo humano",
    "Nutrición",
    "Descanso",
    "Salud"
}


def get_json(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "SPORT-LAB/1.0"}
    )

    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def get_full_text(pmcid):
    url = (
        "https://www.ebi.ac.uk/europepmc/"
        f"webservices/rest/{pmcid}/fullTextXML"
    )

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "SPORT-LAB/1.0"}
    )

    with urllib.request.urlopen(request, timeout=40) as response:
        content = response.read().decode("utf-8", errors="replace")

    root = ET.fromstring(content)

    paragraphs = []

    for node in root.iter():
        tag = node.tag.split("}")[-1]

        if tag in ("title", "p"):
            text = " ".join("".join(node.itertext()).split())

            if len(text) > 45:
                paragraphs.append(text)

    return "\n".join(paragraphs)[:26000]


def find_studies(existing_ids):
    today = date.today()
    start = today.replace(year=today.year - 5)

    query = (
        'OPEN_ACCESS:Y AND HAS_ABSTRACT:Y AND '
        '(TITLE_ABS:exercise OR TITLE_ABS:"physical activity" '
        'OR TITLE_ABS:sport OR TITLE_ABS:sleep '
        'OR TITLE_ABS:nutrition OR TITLE_ABS:hydration) '
        'AND (TITLE_ABS:adolescent OR TITLE_ABS:children '
        'OR TITLE_ABS:youth) '
        f'AND FIRST_PDATE:[{start.isoformat()} TO {today.isoformat()}]'
    )

    params = urllib.parse.urlencode({
        "query": query,
        "format": "json",
        "pageSize": 100,
        "sort": "FIRST_PDATE_D"
    })

    url = (
        "https://www.ebi.ac.uk/europepmc/"
        "webservices/rest/search?" + params
    )

    data = get_json(url)
    candidates = []

    results = data.get("resultList", {}).get("result", [])

    for item in results:
        pmcid = item.get("pmcid")
        pmid = item.get("pmid")
        doi = item.get("doi")

        if not pmcid:
            continue

        identifier = (
            "pmid-" + str(pmid)
            if pmid
            else "pmcid-" + str(pmcid)
        )

        if identifier in existing_ids:
            continue

        try:
            year = int(item.get("pubYear", 0))

            if year < start.year or year > today.year:
                continue

            full_text = get_full_text(pmcid)

            if len(full_text) < 1800:
                continue

            candidates.append({
                "id": identifier,
                "title": item.get("title", ""),
                "authors": item.get("authorString", ""),
                "journal": item.get("journalTitle", ""),
                "year": str(year),
                "doi": doi or "",
                "pmid": str(pmid or ""),
                "pmcid": pmcid,
                "abstract": item.get("abstractText", ""),
                "url": (
                    f"https://europepmc.org/article/MED/{pmid}"
                    if pmid
                    else f"https://europepmc.org/article/PMC/{pmcid}"
                ),
                "full_text": full_text
            })

        except Exception as error:
            print("Estudio descartado:", error)
            continue

    return candidates


def ask_gemini(study, api_key):
    prompt = f"""
Eres el redactor científico de SPORT LAB.

Tu público son estudiantes españoles de ESO de 12 a 16 años.

Redacta un artículo educativo en español utilizando
EXCLUSIVAMENTE la información del estudio científico.

No inventes resultados, cifras, muestras, autores ni conclusiones.
Distingue correlación de causalidad.
No des consejos médicos individuales.

Si la evidencia es insuficiente, devuelve:
{{"publish": false, "reason": "Motivo"}}

Devuelve JSON puro con estas claves:

{{
  "publish": true,
  "category": "Cerebro",
  "icon": "🧠",
  "type": "Tipo de estudio",
  "title": "Pregunta atractiva",
  "teaser": "Resumen breve",
  "question": "Pregunta central",
  "study": "Qué investigaron",
  "method": "Cómo lo estudiaron",
  "results": "Resultados reales",
  "explained": "Explicación para ESO",
  "limits": "Limitaciones del estudio",
  "takeaway": "Idea clave",
  "read_minutes": 5,
  "quiz": [
    {{
      "q": "Pregunta",
      "options": ["A", "B", "C"],
      "correct": 0,
      "explanation": "Explicación"
    }}
  ]
}}

IMPORTANTE:
- EXACTAMENTE cinco preguntas.
- Tres opciones por pregunta.
- Una única respuesta correcta.
- Preguntas basadas en el estudio.
- Lenguaje claro y atractivo.
- No presentes hipótesis como resultados demostrados.

REFERENCIA ORIGINAL:
Título: {study['title']}
Autores: {study['authors']}
Revista: {study['journal']}
Año: {study['year']}
DOI: {study['doi']}
PMID: {study['pmid']}

RESUMEN:
{study['abstract']}

TEXTO CIENTÍFICO:
{study['full_text']}
"""

    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json"
        }
    }

    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{MODEL}:generateContent?"
        + urllib.parse.urlencode({"key": api_key})
    )

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Error Gemini HTTP {error.code}: {detail[:1000]}"
        )

    text = result["candidates"][0]["content"]["parts"][0]["text"]

    return json.loads(text)


def validate(article):
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

        if not isinstance(value, str) or len(value.strip()) < 25:
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

        if any(not isinstance(option, str) or not option.strip()
               for option in options):
            return False

        if type(question.get("correct")) is not int:
            return False

        if question["correct"] not in (0, 1, 2):
            return False

        if not isinstance(question.get("explanation"), str):
            return False

    return True


def main():
    api_key = os.getenv("GEMINI_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("Falta GEMINI_API_KEY en GitHub Secrets.")

    madrid_now = datetime.now(
        timezone.utc
    ).astimezone(ZoneInfo("Europe/Madrid"))

    if (
        os.getenv("GITHUB_EVENT_NAME") == "schedule"
        and madrid_now.hour != 8
    ):
        print("Todavía no son las 08:00 en Madrid. Se cancela esta ejecución.")
        return

    if not ARTICLES_FILE.exists():
        data = {"articles": []}
    else:
        data = json.loads(
            ARTICLES_FILE.read_text(encoding="utf-8")
        )

    existing = data.get("articles", [])

    existing_ids = {
        article.get("id")
        for article in existing
    }

    candidates = find_studies(existing_ids)

    print("Estudios candidatos:", len(candidates))

    for study in candidates[:6]:
        try:
            generated = ask_gemini(study, api_key)

            if not validate(generated):
                print("Artículo descartado por validación:", study["id"])
                continue

            today = date.today()
            publication_year = int(study["year"])

            if not today.year - 5 <= publication_year <= today.year:
                continue

            article = {
                key: generated[key]
                for key in [
                    "category", "icon", "type", "title", "teaser",
                    "question", "study", "method", "results",
                    "explained", "limits", "takeaway", "quiz"
                ]
            }

            article.update({
                "id": study["id"],
                "year": study["year"],
                "read_minutes": int(
                    generated.get("read_minutes", 5)
                ),
                "authors": study["authors"],
                "paper_title": study["title"],
                "journal": study["journal"],
                "identifier": (
                    f"DOI: {study['doi']} · PMID: {study['pmid']}"
                    if study["doi"] and study["pmid"]
                    else f"DOI: {study['doi'] or 'No disponible'}"
                ),
                "url": study["url"],
                "published_at": today.isoformat(),
                "evidence_window_years": 5
            })

            existing.insert(0, article)

            data["articles"] = existing
            data["updated_at"] = today.isoformat()

            data["automation"] = {
                "source": "Europe PMC",
                "model": MODEL,
                "period": "Últimos cinco años"
            }

            ARTICLES_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8"
            )

            print("ARTÍCULO PUBLICADO:", article["title"])
            return

        except Exception as error:
            print("Error procesando estudio:", error)
            time.sleep(2)

    print(
        "No se encontró un estudio adecuado. "
        "No se publica contenido de relleno."
    )


if __name__ == "__main__":
    main()
