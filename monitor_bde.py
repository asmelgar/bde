#!/usr/bin/env python3
"""
Vigila la convocatoria BdE Técnico Generalista 2026A11 y avisa por Telegram
cuando (a) cambia la "Fecha de actualización" a una MÁS reciente, o
(b) aparece un PDF nuevo en la página.

Estado en state.json (se commitea al repo). Es "monotónico": la fecha solo
avanza y los PDFs solo se acumulan, así una copia en caché del BdE nunca
provoca falsas alarmas.

Si la página no se puede leer:
  - imprime un diagnóstico en el log (HTTP, servidor, título, inicio del texto)
  - avisa por Telegram la primera vez y, si sigue fallando, cada 6 h
  - avisa cuando se recupera

Secrets necesarios (GitHub -> Settings -> Secrets and variables -> Actions):
  - TELEGRAM_TOKEN     -> token del bot de @BotFather
  - TELEGRAM_CHAT_ID   -> tu chat_id
"""

import os
import re
import sys
import json
import html
import time
from datetime import datetime, timezone
from urllib.parse import urljoin
import requests

URL = ("https://www.bde.es/wbe/es/sobre-banco/trabajar-banco/trabajar-bde/"
       "convocatorias/tecnico-generalista-2026a11.html")

STATE_FILE = "state.json"
AVISO_FALLO_CADA_H = 6  # horas entre avisos repetidos de fallo
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


# ---------------------------------------------------------------- Telegram --
def send_telegram(text: str) -> None:
    api = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    last_err = None
    for intento in range(3):  # 3 intentos con espera creciente
        try:
            r = requests.post(api, data={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            }, timeout=60)
            r.raise_for_status()
            return
        except requests.exceptions.RequestException as e:
            last_err = e
            if intento < 2:
                time.sleep(5 * (intento + 1))  # 5s, luego 10s
    print(f"AVISO: no se pudo enviar el Telegram tras 3 intentos: {last_err}")


# ------------------------------------------------------------ Descarga/parse --
def fetch_page() -> requests.Response:
    last_err = None
    for intento in range(3):
        try:
            resp = requests.get(URL, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            # El BdE a veces no declara el charset en la cabecera y requests
            # cae a ISO-8859-1 ("TÃ©cnico"). La página es UTF-8: lo forzamos.
            resp.encoding = "utf-8"
            return resp
        except requests.exceptions.RequestException as e:
            last_err = e
            if intento < 2:
                time.sleep(10 * (intento + 1))  # 10s, luego 20s
    raise last_err


def visible_text(raw_html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw_html,
                  flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def diagnose(resp: requests.Response) -> str:
    raw = resp.text
    m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.IGNORECASE | re.DOTALL)
    title = html.unescape(m.group(1)).strip() if m else "(sin título)"
    server = resp.headers.get("Server", "?")
    return (f"HTTP {resp.status_code} · {len(raw)} bytes · "
            f"server={server} · título={title[:80]!r}")


def parse_date(raw_html: str) -> str:
    # ".{1,2}" en vez de "[oó]" por si vuelve a llegar mal codificado
    m = re.search(r"Fecha de actualizaci.{1,2}n:\s*(\d{2}/\d{2}/\d{4})",
                  visible_text(raw_html))
    if not m:
        raise RuntimeError("No se encontró 'Fecha de actualización'.")
    return m.group(1)


def parse_pdfs(raw_html: str) -> set:
    hrefs = re.findall(r'href=["\']([^"\']+?\.pdf)["\']',
                       raw_html, flags=re.IGNORECASE)
    return {urljoin(URL, h) for h in hrefs}


def to_date(value: str) -> datetime:
    return datetime.strptime(value, "%d/%m/%Y")


def pdf_name(url: str) -> str:
    return url.rsplit("/", 1)[-1]


# ------------------------------------------------------------------- Estado --
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------- Fallos --
def handle_failure(state: dict, err: str, diag) -> int:
    print(f"ERROR: {err}")
    if diag:
        print(f"DIAGNÓSTICO: {diag}")

    ahora = now_utc()
    debe_avisar = True
    ultimo = state.get("ultimo_aviso_fallo")
    if ultimo:
        try:
            horas = (ahora - datetime.fromisoformat(ultimo)).total_seconds() / 3600
            debe_avisar = horas >= AVISO_FALLO_CADA_H
        except ValueError:
            pass

    state.setdefault("fallo_desde", ahora.isoformat(timespec="minutes"))

    if debe_avisar:
        detalle = html.escape(err)
        if diag:
            detalle += "\n" + html.escape(diag)
        send_telegram(
            "⚠️ <b>Monitor BdE 2026A11</b>\n"
            "No pude leer la página.\n"
            f"<code>{detalle}</code>\n"
            f"Fallando desde: {state['fallo_desde']} UTC\n"
            f"Si sigue fallando, te aviso de nuevo en {AVISO_FALLO_CADA_H} h "
            "(y cuando se recupere).\n"
            f'<a href="{URL}">Revisar a mano</a>'
        )
        state["ultimo_aviso_fallo"] = ahora.isoformat(timespec="minutes")
    else:
        print("Aviso de fallo ya enviado hace poco; no repito el Telegram.")

    save_state(state)
    return 1


# --------------------------------------------------------------------- Main --
def main() -> int:
    state = load_state()
    diag = None

    try:
        resp = fetch_page()
        raw = resp.text
        diag = diagnose(resp)
        try:
            current_date = parse_date(raw)
        except RuntimeError:
            print("Texto recibido (primeros 800 caracteres):")
            print(visible_text(raw)[:800])
            raise
        current_pdfs = parse_pdfs(raw)
    except Exception as e:
        return handle_failure(state, f"{type(e).__name__}: {e}", diag)

    # ---- Recuperación tras un periodo de fallos ----
    fallo_desde = state.pop("fallo_desde", None)
    state.pop("ultimo_aviso_fallo", None)
    if fallo_desde:
        send_telegram(
            "✅ <b>Monitor BdE 2026A11 recuperado</b>\n"
            f"Fallaba desde {fallo_desde} UTC. Vuelvo a leer la página bien."
        )

    # ---- Primera ejecución: fijamos base y confirmamos ----
    if "fecha" not in state:
        save_state({"fecha": current_date, "pdfs": sorted(current_pdfs)})
        send_telegram(
            "✅ <b>Monitor BdE 2026A11 activado</b>\n"
            f"Fecha de actualización: <b>{current_date}</b>\n"
            f"PDFs vigilados: {len(current_pdfs)}\n"
            "Te avisaré cuando cambie la fecha o aparezca un PDF nuevo.\n"
            f'<a href="{URL}">Abrir convocatoria</a>'
        )
        return 0

    prev_date = state.get("fecha")
    known_pdfs = set(state.get("pdfs", []))
    alerts = []

    # ---- Señal 1: fecha más reciente ----
    try:
        date_is_newer = to_date(current_date) > to_date(prev_date)
    except (ValueError, TypeError):
        date_is_newer = current_date != prev_date  # por precaución
    if date_is_newer:
        alerts.append(f"📅 Fecha: {prev_date} → <b>{current_date}</b>")

    # ---- Señal 2: PDFs nuevos (acumulativo) ----
    new_pdfs = sorted(current_pdfs - known_pdfs)
    if new_pdfs:
        listado = "\n".join(
            f'• <a href="{u}">{html.escape(pdf_name(u))}</a>' for u in new_pdfs
        )
        alerts.append(f"📄 <b>PDF(s) nuevo(s):</b>\n{listado}")

    if alerts:
        send_telegram(
            "🚨 <b>¡Cambio en la convocatoria BdE 2026A11!</b>\n\n"
            + "\n\n".join(alerts)
            + f'\n\n<a href="{URL}">Abrir convocatoria</a>'
        )
        print(f"CAMBIO: fecha_nueva={date_is_newer}, pdfs_nuevos={len(new_pdfs)}")
    else:
        print(f"Sin cambios (fecha {current_date}, {len(current_pdfs)} PDFs).")

    # ---- Persistir (solo avanza / acumula). Si nada cambió, el fichero
    # queda idéntico y el paso de commit no hace nada. ----
    save_state({
        "fecha": current_date if date_is_newer else prev_date,
        "pdfs": sorted(known_pdfs | current_pdfs),
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
