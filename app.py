import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from requests.exceptions import RequestException

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/114.0.0.0 Safari/537.36"
    )
}

st.set_page_config(page_title="Batch SEO Audit Tool", layout="centered")
st.title("🧰 Auditoría SEO en Batch para Páginas Web")

st.write("""
Ingresá una lista de URLs para auditar (una URL por línea).
La herramienta analizará cada página y te mostrará recomendaciones básicas de SEO técnico.
""")

urls_text = st.text_area("URLs (una por línea):", height=200, placeholder="https://ejemplo1.com\nhttps://ejemplo2.com/pagina")

if urls_text:
    urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
    if len(urls) > 10:
        st.warning("Para mejor performance, recomendamos auditar máximo 10 URLs a la vez.")
    resultados = []

    with st.spinner(f"Analizando {len(urls)} URLs... esto puede tardar unos segundos"):
        for url in urls[:10]:
            resultado = {"url": url}
            try:
                r = requests.get(url, headers=HEADERS, timeout=10)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, 'html.parser')

                # Title & Meta description
                title = soup.title.string.strip() if soup.title else ""
                meta_desc_tag = soup.find("meta", attrs={"name": "description"})
                meta_desc = meta_desc_tag['content'].strip() if meta_desc_tag and 'content' in meta_desc_tag.attrs else ""

                # Chequeos didácticos simples
                resultado['title'] = title
                resultado['meta_desc'] = meta_desc
                resultado['title_ok'] = 10 < len(title) < 70
                resultado['meta_desc_ok'] = 50 < len(meta_desc) < 160

                # H1 count
                h1_tags = soup.find_all("h1")
                resultado['h1_count'] = len(h1_tags)
                resultado['h1_ok'] = len(h1_tags) == 1

                # Imágenes sin alt
                images = soup.find_all("img")
                missing_alt = [img.get('src','') for img in images if not img.get('alt')]
                resultado['images_total'] = len(images)
                resultado['images_missing_alt'] = len(missing_alt)

                # Links internos rotos simplificado (solo contamos, no chequeamos estado HTTP para acelerar)
                parsed_url = urlparse(url)
                base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
                internal_links = [a.get("href") for a in soup.find_all("a", href=True) if urlparse(urljoin(base_url, a['href'])).netloc == parsed_url.netloc]
                resultado['internal_links_count'] = len(internal_links)

                resultados.append(resultado)

            except RequestException as e:
                resultado['error'] = str(e)
                resultados.append(resultado)

    # Mostrar resultados por URL
    for res in resultados:
        st.markdown(f"### 🔗 {res['url']}")
        if 'error' in res:
            st.error(f"No se pudo analizar: {res['error']}")
            continue

        # Title
        if res['title']:
            color = "✅" if res['title_ok'] else "⚠️"
            st.write(f"{color} **Title:** {res['title']} (longitud: {len(res['title'])} caracteres)")
            if not res['title_ok']:
                st.info("Se recomienda que el título tenga entre 10 y 70 caracteres para SEO óptimo.")
        else:
            st.warning("No se encontró etiqueta <title>.")

        # Meta Description
        if res['meta_desc']:
            color = "✅" if res['meta_desc_ok'] else "⚠️"
            st.write(f"{color} **Meta Description:** {res['meta_desc']} (longitud: {len(res['meta_desc'])} caracteres)")
            if not res['meta_desc_ok']:
                st.info("Se recomienda que la meta descripción tenga entre 50 y 160 caracteres.")
        else:
            st.warning("No se encontró meta descripción.")

        # H1
        color = "✅" if res['h1_ok'] else "⚠️"
        st.write(f"{color} Cantidad de etiquetas H1: {res['h1_count']}")
        if not res['h1_ok']:
            st.info("Es recomendable tener exactamente una etiqueta H1 por página.")

        # Imágenes sin alt
        if res['images_total'] > 0:
            color = "✅" if res['images_missing_alt'] == 0 else "⚠️"
            st.write(f"{color} Imágenes en la página: {res['images_total']}")
            st.write(f"Imágenes sin atributo ALT: {res['images_missing_alt']}")
            if res['images_missing_alt'] > 0:
                st.info("Las imágenes deberían tener texto alternativo (alt) para accesibilidad y SEO.")
        else:
            st.info("No se encontraron imágenes en la página.")

        # Links internos (solo cantidad)
        st.write(f"Enlaces internos encontrados: {res['internal_links_count']}")

        st.markdown("---")


# CTA final
st.markdown("---")
st.markdown(
    """
    <div style="text-align: center;">
        <p>✨ Esta herramienta fue creada con fines educativos y de asistencia a profesionales que están comenzando en SEO.</p>
        <p>💌 ¿Te sirvió? ¿Tenés alguna sugerencia? ¿Querés charlar sobre SEO, educación digital o IA aplicada? Escribime a <a href="mailto:florencia@crawla.agency">florencia@crawla.agency</a></p>
        <br>
        <a href="https://www.linkedin.com/in/festevez3005/" target="_blank">
            <button style="background-color:#4B8BBE; color:white; padding:10px 20px; font-size:16px; border:none; border-radius:6px; cursor:pointer;">
                🌐 Conectá conmigo en LinkedIn
            </button>
        </a>
    </div>
    """,
    unsafe_allow_html=True
)
