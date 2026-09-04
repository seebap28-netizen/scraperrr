import json
import re
import time
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from geopy.geocoders import Nominatim
from playwright.sync_api import sync_playwright
import ollama
import requests

MODELO_OLLAMA = (
    "llama3"  # Cambia por tu modelo activo en Ollama (ej: 'llama3.2', 'mistral')
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

geolocator = Nominatim(user_agent="ticket_scraper_chile_v9")
_coords_cache = {}

TICKETPLUS_BASE = "https://ticketplus.cl"
TICKETPLUS_EVENTS_URL = f"{TICKETPLUS_BASE}/es/events/more_events.json"

RECINTOS_CONOCIDOS = {
    "estadio nacional": (-33.4643, -70.6062),
    "parque estadio nacional": (-33.4643, -70.6062),
    "parque o'higgins": (-33.4658, -70.6601),
    "parque o’higgins": (-33.4658, -70.6601),
    "movistar arena": (-33.4625, -70.6608),
    "estadio bicentenario la florida": (-33.5358, -70.5786),
    "estadio bicentenario de la florida": (-33.5358, -70.5786),
    "parque padre hurtado": (-33.4306, -70.5556),
    "plaza de la ciudadanía": (-33.4445, -70.6537),
    "estadio lucio farina": (-32.8716, -71.2479),
    "gran arena monticello": (-33.9933, -70.7011),
    "teatro caupolican": (-33.4542, -70.6534),
    "teatro caupolicán": (-33.4542, -70.6534),
    "teatro cariola": (-33.4447, -70.6517),
    "teatro coliseo": (-33.4445, -70.6537),
    "estadio monumental": (-33.5065, -70.6075),
    "metropolitan santiago": (-33.3986, -70.5489),
    "teatro nescafe de las artes": (-33.4261, -70.6206),
    "estadio santa laura": (-33.4047, -70.6586),
    "teatro oriente": (-33.4231, -70.6133),
    "espacio riesco": (-33.3768, -70.6183),
    "centro de eventos múnich": (-33.6186, -70.8872),
    "parque ciudad empresarial": (-33.3853, -70.6161),
    "claro arena": (-33.3981, -70.5361),
}


def obtener_coordenadas(recinto):
    if not recinto or recinto in ["No especificado", "Ubicación no especificada"]:
        return None, None
    recinto_key = recinto.lower().strip()
    if recinto_key in _coords_cache:
        return _coords_cache[recinto_key]
    for key, coords in RECINTOS_CONOCIDOS.items():
        if key in recinto_key:
            _coords_cache[recinto_key] = coords
            return coords[0], coords[1]
    recinto_limpio = re.sub(
        r"(?i)\b(de santiago de chile|jardines|centro de eventos|convention & event center)\b",
        "",
        recinto,
    ).strip()
    try:
        time.sleep(1)
        location = geolocator.geocode(f"{recinto_limpio}, Chile", timeout=5)
        if location:
            coords = (location.latitude, location.longitude)
            _coords_cache[recinto_key] = coords
            return coords
    except Exception:
        pass
    _coords_cache[recinto_key] = (None, None)
    return None, None


def extraer_datos_evento_con_ollama(texto_pagina, url_evento):
    system_prompt = (
        "Eres un extractor de datos de eventos preciso. Tu objetivo es leer el texto de la página de un evento "
        "y extraer la información clave.\n"
        "REGLAS:\n"
        "1. Identifica el nombre del evento principal, la fecha exacta y el lugar/recinto.\n"
        "2. Si la fecha contiene días y mes (ej: '28 de Febrero 2027', '24 de Octubre 2026'), extráela completa.\n"
        "3. Si no encuentras un dato explícito, escribe 'No especificado'.\n"
        "4. Responde ÚNICAMENTE en formato JSON plano (sin bloques ```json)."
    )

    user_prompt = f"""
    Texto extraído del evento ({url_evento}):
    
    "{texto_pagina[:2000]}"

    Devuelve este JSON exacto:
    {{
      "evento": "Nombre del evento",
      "recinto": "Lugar o estadio exacto",
      "fecha": "Fecha exacta con día, mes y año"
    }}
    """

    try:
        response = ollama.chat(
            model=MODELO_OLLAMA,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            options={"temperature": 0.0},
        )
        content = response["message"]["content"].strip()
        if "```" in content:
            content = (
                content.replace("```json", "").replace("```", "").strip()
            )
        data = json.loads(content)
        data["url_evento"] = url_evento
        return data
    except Exception as e:
        print(f"  ⚠️ Error en Ollama: {e}")
        return {
            "evento": "No especificado",
            "recinto": "No especificado",
            "fecha": "No especificado",
            "url_evento": url_evento,
        }


# --- SCRAPING TICKETMASTER ---
def obtener_eventos_ticketmaster():
    print("\n🔍 Scraping Ticketmaster Chile...")
    url_base = "https://www.ticketmaster.cl"
    try:
        res = requests.get(url_base, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        enlaces = soup.select("a[href*='/event/']")
        urls = list(
            set(
                [
                    urljoin(url_base, a["href"])
                    for a in enlaces
                    if a.get("href")
                ]
            )
        )
        print(f"📦 Ticketmaster: {len(urls)} eventos encontrados.")

        resultados = []
        for i, url in enumerate(urls, 1):
            print(f"[{i}/{len(urls)}] 🤖 Ticketmaster: {url.split('/')[-1]}")
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                s = BeautifulSoup(r.text, "html.parser")

                meta_img = s.find("meta", property="og:image")
                img_url = meta_img["content"] if meta_img else "No disponible"

                for tag in s(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                texto = s.get_text(separator=" ", strip=True)

                datos = extraer_datos_evento_con_ollama(texto, url)
                datos["imagen_url"] = img_url
                lat, lng = obtener_coordenadas(datos.get("recinto"))
                datos["latitud"] = lat
                datos["longitud"] = lng
                resultados.append(datos)
        return resultados
    except Exception as e:
        print(f"❌ Error en Ticketmaster: {e}")
        return []


# --- SCRAPING PUNTOTICKET (CON PAGINACIÓN Y MULTIPÁGINA) ---
def obtener_eventos_puntoticket():
    print("\n🌐 Scraping PuntoTicket (Manejando Paginación)...")
    url_base = "https://www.puntoticket.com"
    resultados = []
    urls_descubiertas = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        try:
            print("  ⏳ Cargando portada de PuntoTicket...")
            page.goto(
                url_base, wait_until="domcontentloaded", timeout=30000
            )
            time.sleep(4)

            # Buscar cuántas páginas existen en el paginador
            paginas = page.query_selector_all(".pagination .page-link")
            total_paginas = len(paginas) if paginas else 1

            for num_pagina in range(1, total_paginas + 1):
                if num_pagina > 1:
                    print(f"  📄 Navegando a la página {num_pagina}...")
                    boton_pag = page.query_selector(
                        f".pagination .page-link[data-page='{num_pagina}']"
                    )
                    if boton_pag:
                        boton_pag.click()
                        time.sleep(3)

                # Extraer enlaces de artículos de la página actual
                hrefs = page.eval_on_selector_all(
                    "article.event-item a, .item-slide a",
                    "elements => elements.map(e => e.getAttribute('href'))",
                )

                for h in hrefs:
                    if h and not h.startswith("http"):
                        # Filtrar enlaces de secciones fijas
                        if not any(
                            x in h
                            for x in [
                                "giftcard",
                                "estacionamientos",
                                "ayuda",
                                "Account",
                                "Cliente",
                            ]
                        ):
                            urls_descubiertas.add(urljoin(url_base, h))

            urls_unicas = list(urls_descubiertas)
            print(
                f"📦 PuntoTicket: {len(urls_unicas)} eventos encontrados (todas las páginas)."
            )

            for i, url in enumerate(urls_unicas, 1):
                print(
                    f"[{i}/{len(urls_unicas)}] 🤖 PuntoTicket: {url.split('/')[-1]}"
                )
                try:
                    page.goto(
                        url, wait_until="domcontentloaded", timeout=20000
                    )
                    time.sleep(2)

                    html_content = page.content()
                    soup = BeautifulSoup(html_content, "html.parser")

                    meta_img = soup.find(
                        "meta", property="og:image"
                    ) or soup.find("link", rel="image_src")
                    img_url = (
                        meta_img.get("content")
                        if meta_img
                        else "No disponible"
                    )

                    for tag in soup(
                        ["script", "style", "nav", "footer", "header"]
                    ):
                        tag.decompose()
                    texto = soup.get_text(separator=" ", strip=True)

                    datos = extraer_datos_evento_con_ollama(texto, url)
                    datos["imagen_url"] = img_url
                    lat, lng = obtener_coordenadas(datos.get("recinto"))
                    datos["latitud"] = lat
                    datos["longitud"] = lng
                    resultados.append(datos)
                except Exception as e:
                    print(f"  ⚠️ Error procesando evento PuntoTicket: {e}")

        except Exception as e:
            print(f"❌ Error al cargar PuntoTicket: {e}")
        finally:
            browser.close()

    return resultados


def _normalizar_url_ticketplus(evento):
    url_evento = (evento.get("url") or "").strip()
    if url_evento:
        return url_evento
    slug = evento.get("id") or evento.get("name")
    if slug:
        return urljoin(TICKETPLUS_BASE + "/", f"events/{slug}")
    return ""


def obtener_eventos_ticketplus():
    print("\n🎫 Scraping Ticketplus Chile...")
    resultados = []
    vistos = set()
    pagina = 1
    per_page = 50
    max_paginas = 80

    while pagina <= max_paginas:
        url = f"{TICKETPLUS_EVENTS_URL}?per_page={per_page}&page={pagina}"
        try:
            res = requests.get(
                url,
                headers={**HEADERS, "Accept": "application/json"},
                timeout=20,
            )
            res.raise_for_status()
            payload = res.json()
        except Exception as e:
            print(f"❌ Error Ticketplus página {pagina}: {e}")
            break

        eventos = payload.get("results") or []
        if not eventos:
            break

        print(f"  📄 Página {pagina}: {len(eventos)} eventos")
        for ev in eventos:
            url_evento = _normalizar_url_ticketplus(ev)
            if not url_evento or url_evento in vistos:
                continue
            vistos.add(url_evento)

            recinto = (ev.get("location") or "No especificado").strip()
            fecha = (ev.get("date") or "No especificado").strip()
            nombre = (ev.get("title") or "No especificado").strip()
            img = ev.get("img") or "No disponible"
            recinto_geo = recinto.split(" - ")[0].split(",")[0].strip()
            lat, lng = obtener_coordenadas(recinto_geo or recinto)

            resultados.append(
                {
                    "evento": nombre or "No especificado",
                    "recinto": recinto or "No especificado",
                    "fecha": fecha or "No especificado",
                    "url_evento": url_evento,
                    "imagen_url": img,
                    "latitud": lat,
                    "longitud": lng,
                    "precio": ev.get("price"),
                    "fuente": "ticketplus",
                }
            )

        if len(eventos) < per_page:
            break
        pagina += 1
        time.sleep(0.4)

    print(f"📦 Ticketplus: {len(resultados)} eventos únicos.")
    return resultados


if __name__ == "__main__":
    todos_los_eventos = []

    # 1. Scraping Ticketmaster
    tm_eventos = obtener_eventos_ticketmaster()
    todos_los_eventos.extend(tm_eventos)

    # 2. Scraping PuntoTicket
    pt_eventos = obtener_eventos_puntoticket()
    todos_los_eventos.extend(pt_eventos)

    # 3. Scraping Ticketplus (listado JSON público de la home)
    tp_eventos = obtener_eventos_ticketplus()
    todos_los_eventos.extend(tp_eventos)

    # Guardar resultados
    if todos_los_eventos:
        archivo_salida = "eventos_ambos_portales.json"
        with open(archivo_salida, "w", encoding="utf-8") as f:
            json.dump(todos_los_eventos, f, indent=2, ensure_ascii=False)

        print(
            f"\n✅ Proceso completado. Se extrajeron {len(todos_los_eventos)} eventos en total."
        )