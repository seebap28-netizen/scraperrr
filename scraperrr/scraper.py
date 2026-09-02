from bs4 import BeautifulSoup
import json
import re

def parsear_cualquier_evento_ticketmaster(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 1. METADATOS GLOBALES (Funciona en el 99% de eventos por estándares Schema.org)
    metadata = {}
    script_ld = soup.find('script', type='application/ld+json')
    if script_ld and script_ld.string:
        try:
            ld_json = json.loads(script_ld.string.strip())
            metadata = {
                "nombre": ld_json.get('name'),
                "descripcion_fechas": ld_json.get('description'),
                "url": ld_json.get('url'),
                "recinto": ld_json.get('location', {}).get('name'),
                "direccion": ld_json.get('location', {}).get('address', {}).get('streetAddress'),
                "ciudad": ld_json.get('location', {}).get('address', {}).get('addressLocality')
            }
        except json.JSONDecodeError:
            pass

    # Fallback para el ID del Evento (Spotify Pixel o Data Attributes de la vista)
    event_id = None
    # Intento A: Vías de analítica
    for script in soup.find_all('script'):
        if script.string and 'product_id' in script.string:
            match = re.search(r'product_id:\s*"([^"]+)"', script.string)
            if match:
                event_id = match.group(1)
                break
                
    # Intento B: Atributos HTML internos de Ticketmaster (data-fetch_summary)
    if not event_id:
        view_elem = soup.find(attrs={"data-fetch_summary": True})
        if view_elem:
            try:
                fetch_data = json.loads(view_elem["data-fetch_summary"].replace('&quot;', '"'))
                event_id = fetch_data.get("model", {}).get("id")
            except Exception:
                pass

    metadata['event_id'] = event_id

    # 2. EXTRAER ENTRADAS / TARJETAS (Estrategia Genérica por Contenedores)
    entradas_encontradas = []

    # Buscamos enlaces de compra dentro de contenedores de tarjetas genéricos
    # Funciona buscando etiquetas de hipervínculos hacia eventos ('/event/')
    enlaces_evento = soup.find_all('a', href=re.compile(r'/event/'))

    for enlace in enlaces_evento:
        url = enlace.get('href', '').strip()
        
        # Subimos al contenedor padre más cercano que represente una tarjeta/bloque
        tarjeta_padre = enlace.find_parent(class_=re.compile(r'(card|block|item|ticket|tier)', re.IGNORECASE))
        
        if tarjeta_padre:
            # Extraer título de la tarjeta si existe
            titulo_elem = tarjeta_padre.find(class_=re.compile(r'(title|name|header)', re.IGNORECASE))
            titulo = titulo_elem.get_text(" ", strip=True) if titulo_elem else "Entrada"

            # Extraer precio si existe
            precio_elem = tarjeta_padre.find(class_=re.compile(r'(price|monto|valor)', re.IGNORECASE))
            precio = precio_elem.get_text(" ", strip=True) if precio_elem else "No especificado"

            # Evitar duplicados agrupando por tarjeta
            item_existente = next((item for item in entradas_encontradas if item["titulo"] == titulo and item["precio"] == precio), None)
            
            opcion_pago = {
                "texto_boton": enlace.get_text(" ", strip=True) or "Comprar",
                "url": url
            }

            if item_existente:
                if opcion_pago not in item_existente["opciones_pago"]:
                    item_existente["opciones_pago"].append(opcion_pago)
            else:
                entradas_encontradas.append({
                    "titulo": titulo,
                    "precio": precio,
                    "opciones_pago": [opcion_pago]
                })

    return {
        "metadatos_evento": metadata,
        "entradas": entradas_encontradas
    }