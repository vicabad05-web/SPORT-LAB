#!/usr/bin/env python3
"""SPORT LAB: busca estudios OA de los últimos 5 años y crea un artículo educativo.
Requiere GEMINI_API_KEY como variable de entorno. Solo usa biblioteca estándar.
La IA debe ceñirse al texto del artículo científico; los metadatos bibliográficos
se copian de Europe PMC y nunca se inventan. Si no hay evidencia suficiente, no publica.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTICLES_FILE = ROOT / "data" / "articles.json"
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
ALLOWED_CATEGORIES = {"Cerebro", "Rendimiento", "Cuerpo humano", "Nutrición", "Descanso", "Salud"}


def get_json(url, headers=None, timeout=45):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "SPORT-LAB/2.0 educational science project"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def europe_pmc_candidates(existing_ids):
    today = date.today()
    start = today.replace(year=today.year - 5)
    query = (
        'OPEN_ACCESS:Y AND HAS_ABSTRACT:Y AND (TITLE_ABS:exercise OR TITLE_ABS:"physical activity" '
        'OR TITLE_ABS:sport OR TITLE_ABS:sleep OR TITLE_ABS:nutrition OR TITLE_ABS:hydration) '
        'AND (TITLE_ABS:adolescent OR TITLE_ABS:children OR TITLE_ABS:youth) '
        f'AND FIRST_PDATE:[{start.isoformat()} TO {today.isoformat()}]'
    )
    params = urllib.parse.urlencode({"query": query, "format": "json", "pageSize": 100, "sort": "FIRST_PDATE_D desc"})
    data = get_json("https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + params)
    out = []
    for item in data.get("resultList", {}).get("result", []):
        pmcid, pmid, doi = item.get("pmcid"), item.get("pmid"), item.get("doi")
        if not pmcid or not (pmid or doi):
            continue
        ident = "pmid-" + str(pmid) if pmid else "doi-" + re.sub(r"[^a-zA-Z0-9]+", "-", doi)
        if ident in existing_ids:
            continue
        try:
            xml_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
            req = urllib.request.Request(xml_url, headers={"User-Agent": "SPORT-LAB/2.0"})
            with urllib.request.urlopen(req, timeout=30) as response:
                xml_text = response.read().decode("utf-8", errors="replace")
            root = ET.fromstring(xml_text)
            paragraphs = []
            for node in root.iter():
                if node.tag.split("}")[-1] in {"title", "p"}:
                    txt = " ".join("".join(node.itertext()).split())
                    if len(txt) > 45:
                        paragraphs.append(txt)
            full_text = "\n".join(paragraphs)
            if len(full_text) < 1800:
                continue
        except Exception:
            continue
        out.append({
            "id": ident, "title": item.get("title", ""),
            "authors": item.get("authorString", ""),
            "journal": item.get("journalTitle", ""),
            "year": str(item.get("pubYear", "")),
            "doi": doi or "", "pmid": str(pmid or ""), "pmcid": pmcid,
            "abstract": item.get("abstractText", ""),
            "pub_types": item.get("pubTypeList", {}).get("pubType", []),
            "source_url": f"https://europepmc.org/article/MED/{pmid}" if pmid else f"https://europepmc.org/article/PMC/{pmcid}",
            "full_text": full_text[:26000]
        })
    return out


def call_gemini(candidate, api_key):
    prompt = f'''Eres el redactor científico de SPORT LAB, una plataforma educativa para alumnado español de 12 a 16 años.
Redacta UN artículo didáctico en español usando EXCLUSIVAMENTE la evidencia que aparece en la ficha y el texto científico incluidos abajo.
No inventes cifras, resultados, mecanismos, muestras, fechas, autores ni conclusiones. Distingue asociación de causalidad. Si el artículo no permite responder con seguridad, devuelve {{"publish": false, "reason": "..."}}.
No hagas recomendaciones médicas individuales. Lenguaje claro, atractivo y riguroso. No afirmes que un resultado se aplica a todos los adolescentes si la muestra no lo permite.

Devuelve JSON puro con estas claves exactas:
{{"publish":true,"category":"Cerebro|Rendimiento|Cuerpo humano|Nutrición|Descanso|Salud","icon":"emoji","type":"TIPO DE ESTUDIO","title":"pregunta atractiva","teaser":"2 frases","question":"pregunta central","study":"qué investigaron","method":"cómo lo estudiaron, muestra y método si constan","results":"resultados fieles al texto","explained":"explicación para ESO","limits":"limitaciones explícitas y cautelas","takeaway":"idea clave prudente","read_minutes":5,"quiz":[{{"q":"pregunta","options":["A","B","C"],"correct":0,"explanation":"por qué"}}]}}
El cuestionario debe contener EXACTAMENTE 5 preguntas, 3 opciones cada una, una sola respuesta correcta, y explicaciones. Debe evaluar el contenido real, no conocimientos externos. No incluyas autores, revista, DOI ni URL en el JSON: se añadirán desde los metadatos originales.

FICHA BIBLIOGRÁFICA VERIFICADA (no modificar):
Título: {candidate['title']}
Autores: {candidate['authors']}
Revista: {candidate['journal']}
Año: {candidate['year']}
DOI: {candidate['doi']}
PMID: {candidate['pmid']}
PMCID: {candidate['pmcid']}
Resumen: {candidate['abstract']}

TEXTO DEL ARTÍCULO (extracto del texto completo en Europe PMC):
{candidate['full_text']}
'''
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={urllib.parse.quote(api_key)}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"Gemini API devolvió HTTP {exc.code}: {detail}")
    text = result["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def validate_article(a):
    if not isinstance(a, dict) or a.get("publish") is not True:
        return False, (a or {}).get("reason", "La IA ha indicado que la evidencia no es suficiente.")
    required = ["category", "icon", "type", "title", "teaser", "question", "study", "method", "results", "explained", "limits", "takeaway", "quiz"]
    if any(not isinstance(a.get(k), str) or len(a[k].strip()) < 25 for k in required[:-1]):
        return False, "Faltan secciones o alguna explicación es demasiado breve."
    if a["category"] not in ALLOWED_CATEGORIES:
        return False, "Categoría no permitida."
    quiz = a["quiz"]
    if not isinstance(quiz, list) or len(quiz) != 5:
        return False, "El cuestionario no tiene exactamente cinco preguntas."
    for q in quiz:
        if not isinstance(q, dict) or not isinstance(q.get("q"), str) or not isinstance(q.get("options"), list) or len(q["options"]) != 3 or not isinstance(q.get("correct"), int) or q["correct"] not in (0, 1, 2) or not isinstance(q.get("explanation"), str):
            return False, "Formato de pregunta no válido."
        if any(not isinstance(opt, str) or not opt.strip() for opt in q["options"]):
            return False, "Hay opciones vacías en el cuestionario."
    return True, "OK"


def main():
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Falta el secreto GEMINI_API_KEY. Añádelo en Settings > Secrets and variables > Actions.")
    # GitHub cron está en UTC: se invoca a las 06:00 y 07:00 UTC y solo continúa a las 08:xx de Madrid.
    madrid_now = datetime.now(timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo("Europe/Madrid"))
    if os.getenv("GITHUB_EVENT_NAME") == "schedule" and madrid_now.hour != 8:
        print(f"Ejecución descartada: ahora son las {madrid_now:%H:%M} en Madrid; se espera a las 08:00.")
        return
    data = json.loads(ARTICLES_FILE.read_text(encoding="utf-8"))
    existing = data.get("articles", [])
    existing_ids = {a.get("id") for a in existing}
    candidates = europe_pmc_candidates(existing_ids)
    print(f"Europe PMC: {len(candidates)} estudios recientes con texto completo localizados.")
    for candidate in candidates[:6]:
        try:
            generated = call_gemini(candidate, api_key)
            ok, reason = validate_article(generated)
            if not ok:
                print(f"Descartado {candidate['id']}: {reason}")
                continue
            year = int(candidate["year"])
            if year < date.today().year - 5 or year > date.today().year:
                print(f"Descartado {candidate['id']}: fuera de la ventana de cinco años.")
                continue
            slug = candidate["id"]
            article = {k: generated[k] for k in ["category", "icon", "type", "title", "teaser", "question", "study", "method", "results", "explained", "limits", "takeaway", "quiz"]}
            article.update({
                "id": slug, "year": str(year), "read_minutes": int(generated.get("read_minutes", 5)),
                "authors": candidate["authors"], "paper_title": candidate["title"],
                "journal": candidate["journal"],
                "identifier": " · ".join(x for x in [f"DOI: {candidate['doi']}" if candidate["doi"] else "", f"PMID: {candidate['pmid']}" if candidate["pmid"] else "", f"PMCID: {candidate['pmcid']}"] if x),
                "url": candidate["source_url"], "published_at": date.today().isoformat(), "evidence_window_years": 5
            })
            existing.insert(0, article)
            data["articles"] = existing
            data["updated_at"] = date.today().isoformat()
            data["automation"] = {"source": "Europe PMC", "model": MODEL, "policy": "Estudios publicados en los últimos cinco años; texto completo OA; sin aprobación manual."}
            ARTICLES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"PUBLICADO: {article['title']} ({year}) — {candidate['source_url']}")
            return
        except Exception as exc:
            print(f"Error procesando {candidate['id']}: {exc}")
            time.sleep(2)
    print("No se ha encontrado un estudio apto entre los candidatos revisados. Se mantiene la biblioteca actual; no se publica contenido de relleno.")


if __name__ == "__main__":
    main()
