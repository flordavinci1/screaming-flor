import streamlit as st
import requests
import pandas as pd
import gzip
import time
import re
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from requests.exceptions import RequestException
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET

# --- CONFIGURACIÓN Y ESTILO ---
st.set_page_config(page_title="DeSeo - Screaming Flor", layout="wide")

st.markdown("""
    <style>
    .stMetric { background-color: #f0f2f6; padding: 15px; border-radius: 10px; border: 1px solid #ddd; }
    .stTabs [data-baseweb="tab-list"] { gap: 10px; }
    .stTabs [data-baseweb="tab"] { background-color: #f8f9fa; border-radius: 5px; padding: 10px; }
    </style>
""", unsafe_allow_html=True)

# --- COPIAR AQUÍ TODAS TUS FUNCIONES (normalize_url, audit_one, add_issue_flags, etc.) ---
# [He omitido el pegado de las funciones aquí para no hacer el mensaje infinito, 
# pero asegúrate de que estén arriba de este bloque]

# ... (Aquí van tus funciones de lógica de extracción que ya tienes) ...

# --- INICIO DE LA UI ---
st.title("🌸 Screaming Flor – Auditoría SEO")
st.markdown("Identifica errores, prioriza tareas y aprende a optimizar tu sitio.")

with st.sidebar:
    st.header("📥 Configuración de Rastreo")
    mode = st.radio("Fuente de URLs", ["Desde Sitemap", "Lista Manual"])
    max_urls = st.slider("Máximo de URLs", 10, 300, 50)
    concurrency = st.slider("Velocidad (Concurrencia)", 1, 12, 6)
    
    st.header("🧪 Umbrales Educativos")
    low_content_threshold = st.slider("Contenido bajo (palabras)", 50, 800, 250)

# Carga de URLs (Tu lógica original)
urls = []
if mode == "Desde Sitemap":
    sitemap_url = st.text_input("URL del sitemap", placeholder="https://tusitio.com/sitemap.xml")
    if sitemap_url:
        with st.spinner("Leyendo sitemap..."):
            urls = load_sitemap_urls(sitemap_url, max_urls=max_urls)
else:
    urls_text = st.text_area("Pega tus URLs (una por línea)")
    if urls_text:
        urls = [normalize_url(u) for u in urls_text.splitlines() if normalize_url(u)]
        urls = list(dict.fromkeys(urls))[:max_urls]

st.divider()

if st.button("🚀 Ejecutar Auditoría", type="primary", disabled=(len(urls) == 0)):
    base_domain = urlparse(urls[0]).netloc if urls else None
    results = []
    
    progress_bar = st.progress(0)
    status_text = st.empty()

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {ex.submit(audit_one, u, base_domain, 15): u for u in urls}
        done = 0
        for fut in as_completed(futures):
            results.append(fut.result())
            done += 1
            progress_bar.progress(int(done / len(urls) * 100))
            status_text.text(f"Analizando página {done} de {len(urls)}...")

    # Procesamiento de Datos (Tu lógica original)
    df = pd.DataFrame(results)
    df = add_duplicate_flags(df)
    df["issue_low_content"] = df["word_count_est"].fillna(0).astype(int) < int(low_content_threshold)
    df = add_issue_flags(df)
    
    # --- NUEVA INTERFAZ DE RESULTADOS ---
    st.header("📊 Resultado del Diagnóstico")
    
    # 1. Métricas de resumen
    c1, c2, c3, c4 = st.columns(4)
    with c1: st.metric("URLs Analizadas", len(df))
    with c2: st.metric("🔴 Críticos", len(df[df["issues_severity"] == "Alta"]))
    with c3: st.metric("🟡 Advertencias", len(df[df["issues_severity"] == "Media"]))
    with c4: st.metric("🟢 Saludables", len(df[df["issues_severity"] == "OK"]))

    # 2. Tabs Pedagógicos
    t_prioridad, t_tecnico, t_contenido, t_raw = st.tabs([
        "🎯 Prioridades (Roadmap)", 
        "🌐 Técnico e Indexación", 
        "✍️ Contenido y On-Page", 
        "📄 Datos Crudos"
    ])

    with t_prioridad:
        st.subheader("¿Qué debería arreglar un alumno hoy?")
        high_issues = df[df["issues_count"] > 0].sort_values("issues_count", ascending=False)
        if not high_issues.empty:
            for _, row in high_issues.head(10).iterrows():
                with st.expander(f"🔴 {row['url']}"):
                    st.write(f"**Problemas detectados:** {row['issues_notes']}")
                    st.write("**Sugerencia:** Revisa el código de respuesta y las etiquetas meta.")
        else:
            st.success("¡Increíble! No se encontraron problemas críticos.")

    with t_tecnico:
        cols_tecnicas = ["url", "status_code", "https", "indexable_est", "canonical_self"]
        st.dataframe(df[[c for c in cols_tecnicas if c in df.columns]], use_container_width=True)
        st.info("💡 **Tip para profes:** Si 'Indexable' es Falso, Google no mostrará esta página.")

    with t_contenido:
        cols_content = ["url", "title", "title_ok", "meta_desc", "h1_ok", "word_count_est"]
        st.dataframe(df[[c for c in cols_content if c in df.columns]], use_container_width=True)

    with t_raw:
        st.write("Tabla completa para exportar a Excel/Screaming Frog.")
        st.dataframe(df)

    # Botones de descarga
    st.download_button("📥 Descargar Reporte Full", data=to_csv_bytes(df), file_name="deseo_audit.csv")

st
