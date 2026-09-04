import json
import re
import time
import unicodedata
from datetime import date, datetime
from urllib.parse import urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo
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

MESES = {
    "enero": 1,
    "ene": 1,
    "febrero": 2,
    "feb": 2,
    "marzo": 3,
    "mar": 3,
    "abril": 4,
    "abr": 4,
    "mayo": 5,
    "may": 5,
    "junio": 6,
    "jun": 6,
    "julio": 7,
    "jul": 7,
    "agosto": 8,
    "ago": 8,
    "septiembre": 9,
    "setiembre": 9,
    "sep": 9,
    "sept": 9,
    "octubre": 10,
    "oct": 10,
    "noviembre": 11,
    "nov": 11,
    "diciembre": 12,
    "dic": 12,
}


def hoy_chile():
    try:
        return datetime.now(ZoneInfo("America/Santiago")).date()
    except Exception:
        return datetime.now().date()

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


def _como_lista(valor):
    if valor is None:
        return []
    if isinstance(valor, list):
        return valor
    return [valor]


def normalizar_precio(valor):
    if valor is None or valor == "" or valor == "No especificado":
        return "No especificado"
    if isinstance(valor, bool):
        return "No especificado"
    if isinstance(valor, (int, float)):
        if valor <= 0:
            return "No especificado"
        return int(valor)
    digitos = re.sub(r"[^\d]", "", str(valor))
    if not digitos:
        return "No especificado"
    numero = int(digitos)
    if numero <= 0:
        return "No especificado"
    return numero


def _precios_de_ofertas(ofertas):
    precios = []
    for oferta in _como_lista(ofertas):
        if not isinstance(oferta, dict):
            continue
        for clave in ("lowPrice", "highPrice", "price"):
            valor = oferta.get(clave)
            if valor is None or valor == "":
                continue
            normalizado = normalizar_precio(valor)
            if normalizado != "No especificado":
                precios.append(normalizado)
        precios.extend(_precios_de_ofertas(oferta.get("offers")))
    return precios


def extraer_precio_desde_html(soup):
    precios = []
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string.strip())
        except json.JSONDecodeError:
            continue
        for nodo in _como_lista(data):
            if not isinstance(nodo, dict):
                continue
            items = _como_lista(nodo.get("@graph")) if nodo.get("@graph") else [nodo]
            for item in items:
                if isinstance(item, dict):
                    precios.extend(_precios_de_ofertas(item.get("offers")))
    if precios:
        return min(precios)

    texto = soup.get_text(" ", strip=True)
    candidatos = []
    for match in re.findall(r"(?:desde\s*)?\$\s*([\d.]+)", texto, re.I):
        normalizado = normalizar_precio(match)
        if normalizado != "No especificado" and 1000 <= normalizado <= 10_000_000:
            candidatos.append(normalizado)
    if candidatos:
        return min(candidatos)
    return "No especificado"


def _sin_acentos(texto):
    nfkd = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _mes_a_numero(nombre):
    clave = _sin_acentos(nombre).lower().strip(".,")
    return MESES.get(clave)


def parsear_fechas(texto):
    if not texto or texto in ("No especificado", "No disponible"):
        return []
    texto = str(texto).strip()
    hoy = hoy_chile()
    encontradas = []

    for match in re.finditer(
        r"(\d{4})-(\d{2})-(\d{2})(?:[T\s]\d{2}:\d{2})?", texto
    ):
        encontradas.append(
            date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        )

    for match in re.finditer(
        r"(\d{1,2})\s+y\s+(\d{1,2})\s+de\s+([A-Za-zÁÉÍÓÚáéíóúñÑ]+)(?:\s+de)?\s+(\d{4})",
        texto,
        re.I,
    ):
        mes = _mes_a_numero(match.group(3))
        anio = int(match.group(4))
        if mes:
            for dia in (int(match.group(1)), int(match.group(2))):
                try:
                    encontradas.append(date(anio, mes, dia))
                except ValueError:
                    continue

    for match in re.finditer(
        r"(\d{1,2})\s+de\s+([A-Za-zÁÉÍÓÚáéíóúñÑ]+)(?:\s+de)?\s+(\d{4})",
        texto,
        re.I,
    ):
        mes = _mes_a_numero(match.group(2))
        if not mes:
            continue
        try:
            encontradas.append(
                date(int(match.group(3)), mes, int(match.group(1)))
            )
        except ValueError:
            continue

    if not encontradas:
        for match in re.finditer(
            r"(\d{1,2})\s+de\s+([A-Za-zÁÉÍÓÚáéíóúñÑ]+)\b", texto, re.I
        ):
            mes = _mes_a_numero(match.group(2))
            if not mes:
                continue
            try:
                encontradas.append(date(hoy.year, mes, int(match.group(1))))
            except ValueError:
                continue

    if not encontradas:
        for match in re.finditer(
            r"\b(\d{1,2})\s+([A-Za-zÁÉÍÓÚáéíóúñÑ]{3,})\b", texto
        ):
            mes = _mes_a_numero(match.group(2))
            if not mes:
                continue
            try:
                encontradas.append(date(hoy.year, mes, int(match.group(1))))
            except ValueError:
                continue

    unicas = []
    vistos = set()
    for f in encontradas:
        if f not in vistos:
            vistos.add(f)
            unicas.append(f)
    return unicas


def extraer_fechas_desde_html(soup):
    fechas = []
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string.strip())
        except json.JSONDecodeError:
            continue
        for nodo in _como_lista(data):
            if not isinstance(nodo, dict):
                continue
            items = _como_lista(nodo.get("@graph")) if nodo.get("@graph") else [nodo]
            for item in items:
                if not isinstance(item, dict):
                    continue
                for clave in ("startDate", "endDate"):
                    fechas.extend(parsear_fechas(item.get(clave)))
    return fechas


def fecha_referencia_evento(evento):
    fechas = parsear_fechas(evento.get("fecha"))
    fechas.extend(evento.get("_fechas_schema") or [])
    if not fechas:
        return None
    return max(fechas)


def evento_ya_paso(evento, hoy=None):
    if hoy is None:
        hoy = hoy_chile()
    ref = fecha_referencia_evento(evento)
    if ref is None:
        return False
    return ref < hoy


def normalizar_url_evento(url):
    if not url:
        return ""
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/").lower()
    return urlunparse(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, "", "", "")
    )


def _texto_clave(valor):
    texto = _sin_acentos(valor or "").lower()
    texto = re.sub(r"[^a-z0-9\s]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def clave_duplicado(evento):
    url = normalizar_url_evento(evento.get("url_evento"))
    nombre = _texto_clave(evento.get("evento"))
    recinto = _texto_clave(
        (evento.get("recinto") or "").split(",")[0].split(" - ")[0]
    )
    ref = fecha_referencia_evento(evento)
    fecha_key = ref.isoformat() if ref else _texto_clave(evento.get("fecha"))
    huella = None
    if nombre and nombre != "no especificado":
        huella = (nombre, fecha_key, recinto)
    return url, huella


def _calidad_evento(evento):
    score = 0
    if evento.get("precio") not in (None, "", "No especificado"):
        score += 2
    if evento.get("imagen_url") not in (None, "", "No disponible"):
        score += 1
    if evento.get("latitud") not in (None, ""):
        score += 1
    if evento.get("fecha") not in (None, "", "No especificado"):
        score += 1
    return score


def filtrar_eventos(eventos):
    hoy = hoy_chile()
    vigentes = []
    descartados_pasados = 0
    for evento in eventos:
        if evento_ya_paso(evento, hoy):
            descartados_pasados += 1
            continue
        vigentes.append(evento)

    unicos = []
    por_url = {}
    por_huella = {}
    duplicados = 0

    for evento in vigentes:
        url, huella = clave_duplicado(evento)
        idx_existente = None
        if url and url in por_url:
            idx_existente = por_url[url]
        elif huella and huella in por_huella:
            idx_existente = por_huella[huella]

        if idx_existente is not None:
            duplicados += 1
            actual = unicos[idx_existente]
            if _calidad_evento(evento) > _calidad_evento(actual):
                unicos[idx_existente] = evento
                if url:
                    por_url[url] = idx_existente
                if huella:
                    por_huella[huella] = idx_existente
            continue

        idx = len(unicos)
        unicos.append(evento)
        if url:
            por_url[url] = idx
        if huella:
            por_huella[huella] = idx

    print(
        f"Filtro: {descartados_pasados} eventos pasados y {duplicados} duplicados descartados."
    )
    return unicos


def limpiar_evento(evento):
    return {k: v for k, v in evento.items() if not str(k).startswith("_")}


def extraer_datos_evento_con_ollama(texto_pagina, url_evento):
    system_prompt = (
        "Eres un extractor de datos de eventos preciso. Tu objetivo es leer el texto de la página de un evento "
        "y extraer la información clave.\n"
        "REGLAS:\n"
        "1. Identifica el nombre del evento principal, la fecha exacta, el lugar/recinto y el precio más bajo.\n"
        "2. Si la fecha contiene días y mes (ej: '28 de Febrero 2027', '24 de Octubre 2026'), extráela completa.\n"
        "3. El precio debe ser el valor más bajo en CLP, solo números (ej: 23000). Si dice 'Desde $23.000', usa 23000.\n"
        "4. Si no encuentras un dato explícito, escribe 'No especificado'.\n"
        "5. Responde ÚNICAMENTE en formato JSON plano (sin bloques ```json)."
    )

    user_prompt = f"""
    Texto extraído del evento ({url_evento}):
    
    "{texto_pagina[:2000]}"

    Devuelve este JSON exacto:
    {{
      "evento": "Nombre del evento",
      "recinto": "Lugar o estadio exacto",
      "fecha": "Fecha exacta con día, mes y año",
      "precio": "Precio más bajo en CLP, solo número"
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
            "precio": "No especificado",
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
            {
                normalizar_url_evento(urljoin(url_base, a["href"]))
                for a in enlaces
                if a.get("href")
            }
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
                precio_html = extraer_precio_desde_html(s)
                fechas_schema = extraer_fechas_desde_html(s)
                if fechas_schema and max(fechas_schema) < hoy_chile():
                    print("  ⏭️ Evento pasado, se omite.")
                    continue

                for tag in s(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                texto = s.get_text(separator=" ", strip=True)

                datos = extraer_datos_evento_con_ollama(texto, url)
                datos["imagen_url"] = img_url
                datos["precio"] = (
                    precio_html
                    if precio_html != "No especificado"
                    else normalizar_precio(datos.get("precio"))
                )
                datos["fuente"] = "ticketmaster"
                datos["_fechas_schema"] = fechas_schema
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
                            urls_descubiertas.add(
                                normalizar_url_evento(urljoin(url_base, h))
                            )

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
                    precio_html = extraer_precio_desde_html(soup)
                    fechas_schema = extraer_fechas_desde_html(soup)
                    if fechas_schema and max(fechas_schema) < hoy_chile():
                        print("  ⏭️ Evento pasado, se omite.")
                        continue

                    for tag in soup(
                        ["script", "style", "nav", "footer", "header"]
                    ):
                        tag.decompose()
                    texto = soup.get_text(separator=" ", strip=True)

                    datos = extraer_datos_evento_con_ollama(texto, url)
                    datos["imagen_url"] = img_url
                    datos["precio"] = (
                        precio_html
                        if precio_html != "No especificado"
                        else normalizar_precio(datos.get("precio"))
                    )
                    datos["fuente"] = "puntoticket"
                    datos["_fechas_schema"] = fechas_schema
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
            url_evento = normalizar_url_evento(_normalizar_url_ticketplus(ev))
            if not url_evento or url_evento in vistos:
                continue
            vistos.add(url_evento)

            recinto = (ev.get("location") or "No especificado").strip()
            fecha = (ev.get("date") or "No especificado").strip()
            if evento_ya_paso({"fecha": fecha}):
                continue
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
                    "precio": normalizar_precio(ev.get("price")),
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

    todos_los_eventos = [
        limpiar_evento(ev) for ev in filtrar_eventos(todos_los_eventos)
    ]

    # Guardar resultados
    if todos_los_eventos:
        archivo_salida = "eventos_ambos_portales.json"
        with open(archivo_salida, "w", encoding="utf-8") as f:
            json.dump(todos_los_eventos, f, indent=2, ensure_ascii=False)

        print(
            f"\n✅ Proceso completado. Se extrajeron {len(todos_los_eventos)} eventos en total."
        )