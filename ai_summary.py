"""
Resumen con IA (Claude) -> convierte titulares en algo para 'leer y reaccionar'.

Dos usos:
  - summarize_alert(item): una noticia -> mini analisis (que paso / por que importa).
  - summarize_digest(items): varias noticias -> boletin corto por temas.

Si NO hay API key o la llamada falla, devuelve None -> el bot cae de vuelta
al formato simple (titular). Asi nunca se rompe por culpa de la IA.
"""
import os
import json
import time
import requests
import config as C

API_URL = "https://api.anthropic.com/v1/messages"

# Cortacircuitos: si la IA falla por saldo/permiso (400/401/403), no vale la
# pena reintentar en cada noticia de cada ciclo. Lo recordamos un rato para
# ahorrar tiempo y no llenar el log de errores repetidos.
HERE = os.path.dirname(os.path.abspath(__file__))
_BREAKER_FILE = os.path.join(HERE, "ai_breaker.json")
_BREAKER_TTL = 6 * 3600   # 6 horas apagada tras un fallo de saldo/permiso

def _breaker_open():
    """True si la IA esta 'apagada' por un fallo reciente."""
    try:
        until = json.load(open(_BREAKER_FILE)).get("until", 0)
        return time.time() < until
    except Exception:
        return False

def _trip_breaker():
    try:
        json.dump({"until": time.time() + _BREAKER_TTL}, open(_BREAKER_FILE, "w"))
    except Exception:
        pass


def _call(prompt, max_tokens=400):
    if not C.ANTHROPIC_API_KEY or _breaker_open():
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
            # sin saldo o key invalida -> apagar la IA un rato (no reintentar)
            if r.status_code in (400, 401, 403):
                _trip_breaker()
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
