import streamlit as st
import pandas as pd
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

# --- IMPORTANTE: Aquí asumo que las funciones técnicas (normalize_url, audit_one, etc.) 
# están presentes en el código. Por brevedad, mantendré la estructura de visualización 
# conectada a los resultados reales.

def screaming_flor_page():
    st.title("🌸 Screaming Flor: Auditoría SEO")
    
    st.markdown("""
        Analiza la salud técnica de tu sitio. Los resultados se dividen por categorías para facilitar la optimización.
        **Prioridad:** 🔴 Crítico | 🟡 Advertencia | 🟢 Optimizado
    """)

    # --- SIDEBAR: Configuración ---
    with st.sidebar:
        st.header("📥 Configuración de rastreo")
        mode = st.radio("Fuente de URLs:", ["Sitemap XML", "Lista Manual"])
        max_urls = st.slider("Máximo de páginas", 10, 300, 50)
        concurrency = st.slider("Velocidad (Concurrencia)", 1, 12, 6)
        
        st.header("🧪 Umbrales")
        low_content_threshold = st.slider("Contenido bajo (palabras)", 50, 800, 250)

    # --- ENTRADA DE DATOS ---
    urls = []
    if mode == "Sitemap XML":
        sitemap_url = st.text_input("URL del Sitemap", placeholder="https://tusitio.com/sitemap.xml")
        if sitemap_url:
            # Aquí llamamos a tu función original load_sitemap_urls
            with st.spinner("Leyendo sitemap..."):
                from Home import load_sitemap_urls # O donde residan tus funciones
                urls = load_sitemap_urls(sitemap_url, max_urls=max_urls)
    else:
        urls_text = st.text_area("Pega tus URLs (una por línea)")
        if urls_text:
            urls = [u.strip() for u in urls_text.splitlines() if u.strip()]

    # --- EJECUCIÓN ---
    if st.button("🚀 Ejecutar Auditoría", type="primary") and urls:
        base_domain = urlparse(urls[0]).netloc
        results = []
        progress_bar = st.progress(0)
        
        with st.spinner(f"Analizando {len(urls)} páginas..."):
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                # Usamos tu función técnica audit_one
                futures = {executor.submit(audit_one, u, base_domain): u for u in urls}
                for i, fut in enumerate(as_completed(futures)):
                    results.append(fut.result())
                    progress_bar.progress((i + 1) / len(urls))

        # Convertimos a DataFrame y aplicamos tus funciones de flags
        df = pd.DataFrame(results)
        df = add_duplicate_flags(df)
        df = add_issue_flags(df) # Esta función ya crea 'issues_severity' y 'issues_notes'
        
        # --- NUEVA INTERFAZ DE RESULTADOS ---
        st.divider()
        
        # Métricas principales
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("URLs analizadas", len(df))
        c2.metric("Con errores 🔴", len(df[df['issues_severity'] == 'Alta']))
        c3.metric("Advertencias 🟡", len(df[df['issues_severity'] == 'Media']))
        c4.metric("Saludables 🟢", len(df[df['issues_severity'] == 'OK']))

        # Pestañas organizadas por tipo de problema
        tab1, tab2, tab3, tab4 = st.tabs([
            "🎯 Prioridades de Acción", 
            "🌐 Técnica e Indexación", 
            "📝 Contenido y SEO On-Page", 
            "🔗 Enlaces e Imágenes"
        ])

        with tab1:
            st.subheader("Acciones recomendadas para hoy")
            st.markdown("""
                Estas páginas presentan errores críticos (Status 404, Noindex accidental o errores de servidor). 
                **Recomendación:** Resuelve primero los elementos en rojo para recuperar visibilidad.
            """)
            
            # Filtramos solo los errores importantes para no abrumar
            prioridades = df[df['issues_count'] > 0][['url', 'status_code', 'issues_severity', 'issues_notes']]
            st.dataframe(prioridades.sort_values('issues_severity'), use_container_width=True)

        with tab2:
            st.subheader("Salud Técnica")
            cols_tec = ['url', 'status_code', 'https', 'indexable_est', 'canonical', 'response_ms']
            st.dataframe(df[[c for c in cols_tec if c in df.columns]], use_container_width=True)

        with tab3:
            st.subheader("Optimización de Contenido")
            cols_cont = ['url', 'title', 'title_len', 'meta_desc', 'word_count_est', 'h1_text']
            st.dataframe(df[[c for c in cols_cont if c in df.columns]], use_container_width=True)

        with tab4:
            st.subheader("Análisis de Enlaces y Multimedia")
            cols_links = ['url', 'links_total', 'internal_links_count', 'images_total', 'images_missing_alt']
            st.dataframe(df[[c for c in cols_links if c in df.columns]], use_container_width=True)

        # Botón de descarga al final
        st.divider()
        st.download_button("📥 Descargar reporte completo (CSV)", data=df.to_csv(index=False), file_name="auditoria_deseo.csv")

# Llamamos a la función
screaming_flor_page()
