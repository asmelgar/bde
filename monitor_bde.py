#!/usr/bin/env python3
"""
Vigila la convocatoria BdE Técnico Generalista 2026A11 y avisa por Telegram
cuando (a) cambia la "Fecha de actualización" a una MÁS reciente, o
(b) aparece un PDF nuevo en la página.

Estado en state.json (se commitea al repo). Es "monotónico": la fecha solo
avanza y los PDFs solo se acumulan, así una copia en caché del BdE nunca
provoca falsas alarmas.

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
from datetime import datetime
from urllib.parse import urljoin
import requests

URL = ("https://www.bde.es/wbe/es/sobre-banco/trabajar-banco/trabajar-bde/"
       "convocatorias/tecnico-generalista-2026a11.html")

STATE_FILE = "state.json"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0 Safari/537.36"),
    "Accept-Language": "es-ES,es;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


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
    # Si los 3 intentos fallan, no tumbamos el workflow entero por un
    # problema de red pasajero de Telegram: solo lo dejamos en el log.
    print(f"AVISO: no se pudo enviar el Telegram tras 3 intentos: {last_err}")


def fetch_page() -> str:
    resp = requests.get(URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_date(raw_html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    m = re.search(r"Fecha de actualizaci[oó]n:\s*(\d{2}/\d{2}/\d{4})", text)
    if not m:
        raise RuntimeError("No se encontró 'Fecha de actualización'.")
    return m.group(1)


def parse_pdfs(raw_html: str) -> set:
    hrefs = re.findall(r'href=["\']([^"\']+?\.pdf)["\']',
                       raw_html, flags=re.IGNORECASE)
    return {urljoin(URL, h) for h in hrefs}


def to_date(value: str) -> datetime:
    return datetime.strptime(value, "%d/%m/%Y")


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


def pdf_name(url: str) -> str:
    return url.rsplit("/", 1)[-1]


def main() -> int:
    try:
        raw = fetch_page()
        current_date = parse_date(raw)
        current_pdfs = parse_pdfs(raw)
    except Exception as e:
        send_telegram(
            "⚠️ <b>Monitor BdE 2026A11</b>\n"
            f"No pude leer la página.\n<code>{e}</code>\n"
            f'<a href="{URL}">Revisar a mano</a>'
        )
        return 1

    state = load_state()
    prev_date = state.get("fecha")
    known_pdfs = set(state.get("pdfs", []))

    # ---- Primera ejecución: fijamos base y confirmamos ----
    if not state:
        save_state({"fecha": current_date, "pdfs": sorted(current_pdfs)})
        send_telegram(
            "✅ <b>Monitor BdE 2026A11 activado</b>\n"
            f"Fecha de actualización: <b>{current_date}</b>\n"
            f"PDFs vigilados: {len(current_pdfs)}\n"
            "Te avisaré cuando cambie la fecha o aparezca un PDF nuevo.\n"
            f'<a href="{URL}">Abrir convocatoria</a>'
        )
        return 0

    alerts = []

    # ---- Señal 1: fecha más reciente ----
    date_is_newer = False
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
            f'• <a href="{u}">{pdf_name(u)}</a>' for u in new_pdfs
        )
        alerts.append(f"📄 <b>PDF(s) nuevo(s):</b>\n{listado}")

    # ---- Notificar y persistir (solo avanza / acumula) ----
    if alerts:
        cuerpo = "\n\n".join(alerts)
        send_telegram(
            "🚨 <b>¡Cambio en la convocatoria BdE 2026A11!</b>\n\n"
            f"{cuerpo}\n\n"
            f'<a href="{URL}">Abrir convocatoria</a>'
        )
        new_date = current_date if date_is_newer else prev_date
        save_state({
            "fecha": new_date,
            "pdfs": sorted(known_pdfs | current_pdfs),
        })
        print(f"CAMBIO: fecha_nueva={date_is_newer}, pdfs_nuevos={len(new_pdfs)}")
    else:
        print(f"Sin cambios (fecha {current_date}, {len(current_pdfs)} PDFs).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
