"""
Resumen con IA (Claude) -> convierte titulares en algo para 'leer y reaccionar'.

Dos usos:
  - summarize_alert(item): una noticia -> mini analisis (que paso / por que importa).
  - summarize_digest(items): varias noticias -> boletin corto por temas.

Si NO hay API key o la llamada falla, devuelve None -> el bot cae de vuelta
al formato simple (titular). Asi nunca se rompe por culpa de la IA.
"""
import json
import requests
import config as C

API_URL = "https://api.anthropic.com/v1/messages"


def _call(prompt, max_tokens=400):
    if not C.ANTHROPIC_API_KEY:
        return None
    headers = {
        "x-api-key": C.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": C.AI_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        r = requests.post(API_URL, headers=headers, data=json.dumps(body), timeout=30)
        if r.status_code != 200:
            print("IA respondio", r.status_code, r.text[:200])
            return None
        data = r.json()
        return data["content"][0]["text"].strip()
    except Exception as e:
        print("IA error:", e)
        return None


def summarize_alert(title, source):
    """Una noticia -> analisis brevisimo en español para reaccionar rapido."""
    prompt = (
        "Eres un analista financiero. Te doy UN titular de noticia. "
        "Responde en español, MUY breve, en este formato exacto (sin nada mas):\n"
        "Qué pasó: <una frase simple>\n"
        "Por qué importa: <una frase: efecto probable en mercados/cripto>\n"
        "Reacción: <una frase corta y accionable>\n\n"
        "Si el titular es irrelevante para los mercados, responde solo: IRRELEVANTE\n\n"
        f"Titular ({source}): {title}"
    )
    return _call(prompt, max_tokens=250)


def summarize_digest(headlines):
    """Lista de titulares -> boletin corto agrupado por tema, en español."""
    joined = "\n".join(f"- {h}" for h in headlines[:25])
    prompt = (
        "Eres un analista financiero. Te doy titulares de las ultimas horas. "
        "Hazme un resumen en español para leer en 30 segundos y reaccionar. "
        "Agrupa por tema (Fed/Tasas, Cripto, Geopolitica, Trump/Politica, Otros). "
        "Por cada tema: 1-3 vinetas cortas con lo esencial y, si aplica, el efecto "
        "probable en el mercado entre parentesis. Omite temas sin noticias. "
        "No inventes datos que no esten en los titulares. Se conciso.\n\n"
        f"Titulares:\n{joined}"
    )
    return _call(prompt, max_tokens=700)
