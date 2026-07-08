"""
Traduccion al español (gratis, sin API key) via endpoint publico de Google.
Con cache en disco para no traducir dos veces lo mismo.
Si falla, devuelve el texto original (nunca rompe el bot).
"""
import os, json, requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(HERE, "translate_cache.json")

def _load():
    if os.path.exists(CACHE_FILE):
        try:
            return json.load(open(CACHE_FILE))
        except Exception:
            return {}
    return {}

def _save(cache):
    # limita el tamaño para no crecer infinito
    if len(cache) > 1000:
        cache = dict(list(cache.items())[-1000:])
    json.dump(cache, open(CACHE_FILE, "w"), ensure_ascii=False)

_CACHE = _load()

def to_es(text, sl="en"):
    """Traduce a español. Devuelve el original si falla."""
    if not text:
        return text
    key = text.strip()
    if key in _CACHE:
        return _CACHE[key]
    try:
        r = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": sl, "tl": "es", "dt": "t", "q": key},
            timeout=15,
        )
        data = r.json()
        out = "".join(seg[0] for seg in data[0] if seg[0])
        if out:
            _CACHE[key] = out
            _save(_CACHE)
            return out
    except Exception as e:
        print("Traduccion fallo (uso original):", e)
    return text
