"""
Precio de Bitcoin en vivo (CoinGecko, gratis, sin API key).

Se usa para dar CONTEXTO en las alertas: precio actual y % del dia.
Cachea en memoria y en disco unos minutos para no golpear la API
(CoinGecko limita las peticiones gratuitas).
"""
import os, json, time
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(HERE, "price_cache.json")
TTL = 120   # segundos: no pedir mas seguido que esto

_MEM = None   # cache en memoria del proceso

def _read_disk():
    if os.path.exists(CACHE_FILE):
        try:
            return json.load(open(CACHE_FILE))
        except Exception:
            pass
    return None

def get_btc(force=False):
    """Devuelve dict {price, change_24h, ts} o None si falla.
    price = USD actual, change_24h = % variacion 24h."""
    global _MEM
    now = time.time()
    if _MEM and not force and (now - _MEM.get("ts", 0)) < TTL:
        return _MEM
    disk = _read_disk()
    if disk and not force and (now - disk.get("ts", 0)) < TTL:
        _MEM = disk
        return _MEM
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin", "vs_currencies": "usd",
                    "include_24hr_change": "true"},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        d = r.json()["bitcoin"]
        data = {"price": d["usd"],
                "change_24h": d.get("usd_24h_change", 0.0),
                "ts": now}
        json.dump(data, open(CACHE_FILE, "w"))
        _MEM = data
        return data
    except Exception:
        # ante fallo/rate-limit, devuelve la ultima copia si existe
        if disk:
            _MEM = disk
            return disk
        return None

def btc_line():
    """Linea corta lista para pegar en un mensaje de Telegram. '' si falla."""
    d = get_btc()
    if not d:
        return ""
    p = d["price"]
    ch = d.get("change_24h", 0.0)
    flecha = "🟢" if ch >= 0 else "🔴"
    precio = f"${p:,.0f}" if p >= 100 else f"${p:,.2f}"
    return f"₿ BTC {precio}  {flecha} {ch:+.1f}% (24h)"
