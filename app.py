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
import io

# --- 1. CONFIGURACIÓN Y ESTILO ---
st.set_page_config(page_title="DeSeo - Screaming Flor", layout="wide", page_icon="🌸")

st.markdown("""
    <style>
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; border: 1px solid #eee; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] { 
        background-color: #f0f2f6; 
        border-radius: 5px 5px 0 0; 
        padding: 10px 20px;
    }
    .critical-card {
        background-color: #fff1f0;
        border-left: 5px solid #ff4d4f;
        padding: 15px;
        margin-bottom: 10px;
        border-radius: 5px;
    }
    </style>
""", unsafe_allow_html=True)

# --- 2. LOGICA TÉCNICA (Backend) ---
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/114.0.0.0 Safari/537.36"}

def normalize_url(u: str) -> str:
    u = (u or "").strip()
    if not u: return ""
    if u.startswith("www."): u = "https://" + u
    if not u.startswith("http"): u = "https://" + u
    return u

def fetch_bytes(url: str) -> bytes:
    r = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
    r.raise_for_status()
    return r.content

def parse_sitemap_xml(xml_bytes: bytes):
    sitemaps, urls = [], []
    try:
        root = ET.fromstring(xml_bytes)
        def findall_local(tag_name: str): return root.findall(f".//{{*}}{tag_name}")
        sitemap_locs = [el.text.strip() for el in findall_local("sitemap") for el in el.findall(".//{*}loc") if el.text]
        if sitemap_locs:
            sitemaps.extend(sitemap_locs)
            return sitemaps, urls
        locs = [el.text.strip() for el in findall_local("loc") if el.text]
        urls.extend(locs)
    except: pass
    return sitemaps, urls

def load_sitemap_urls(sitemap_url: str, max_urls: int = 50) -> list[str]:
    collected, to_process, seen = [], [normalize_url(sitemap_url)], set()
    while to_process and len(collected) < max_urls:
        current = to_process.pop(0)
        if current in seen: continue
        seen.add(current)
        try:
            content = fetch_bytes(current)
            if current.endswith(".gz"): content = gzip.decompress(content)
            sitemaps, urls = parse_sitemap_xml(content)
            for sm in sitemaps: to_process.append(normalize_url(sm))
            for u in urls:
                u = normalize_url(u)
                if u not in collected:
                    collected.append(u)
                    if len(collected) >= max_urls: break
        except: continue
    return collected

def audit_one(url: str, base_domain: str | None = None) -> dict:
    out = {"url": url}
    started = time.time()
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        out.update({
            "final_url": r.url, "status_code": r.status_code, "https": (urlparse(r.url).scheme == "https"),
            "response_ms": int((time.time() - started) * 1000)
        })
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        meta_desc = ""
        m_tag = soup.find("meta", attrs={"name": "description"})
        if m_tag: meta_desc = (m_tag.get("content") or "").strip()
        
        out.update({
            "title": title, "title_len": len(title), "title_ok": 10 <= len(title) <= 70,
            "meta_desc": meta_desc, "meta_desc_len": len(meta_desc), "meta_desc_ok": 50 <= len(meta_desc) <= 160,
            "h1_count": len(soup.find_all("h1")), "word_count_est": len(re.sub(r"\s+", " ", soup.get_text()).split()),
            "meta_noindex": "noindex" in (soup.find("meta", attrs={"name": "robots"}).get("content") or "").lower() if soup.find("meta", attrs={"name": "robots"}) else False
        })
    except Exception as e: out["error"] = str(e)
    return out

def add_issue_flags(df: pd.DataFrame, low_content: int) -> pd.DataFrame:
    df = df.copy()
    df["issue_status"] = df["status_code"].fillna(0).astype(int) != 200
    df["issue_title"] = ~df.get("title_ok", True)
    df["issue_h1"] = df.get("h1_count", 0) != 1
    df["issue_low_content"] = df.get("word_count_est", 0) < low_content
    
    cols = [c for c in df.columns if c.startswith("issue_")]
    df["issues_count"] = df[cols].sum(axis=1)
    df["severity"] = df["issues_count"].apply(lambda x: "Alta" if x >= 3 else ("Media" if x >= 1 else "OK"))
    return df

# --- 3. INTERFAZ (Frontend) ---
st.title("🌸 DeSeo: Screaming Flor")
st.markdown("Auditoría SEO educativa para emprendedores y alumnos.")

with st.sidebar:
    st.header("📥 Configuración")
    modo = st.radio("Cargar por:", ["Sitemap XML", "Lista de URLs"])
    max_urls = st.slider("Máximo de páginas", 5, 100, 20)
    low_content = st.slider("Umbral contenido bajo", 50, 500, 250)

# Input según modo
urls = []
if modo == "Sitemap XML":
    s_url = st.text_input("URL del Sitemap", placeholder="https://tusitio.com/sitemap.xml")
    if s_url and st.button("Validar Sitemap"):
        urls = load_sitemap_urls(s_url, max_urls)
        st.success(f"Se encontraron {len(urls)} URLs")
else:
    u_text = st.text_area("Pega tus URLs (una por línea)")
    if u_text:
        urls = [normalize_url(u) for u in u_text.splitlines() if u.strip()][:max_urls]

if st.button("🚀 Iniciar Auditoría", type="primary", disabled=not urls):
    res_list = []
    progress = st.progress(0)
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(audit_one, u): u for u in urls}
        for i, fut in enumerate(as_completed(futures)):
            res_list.append(fut.result())
            progress.progress((i + 1) / len(urls))
            
    df = pd.DataFrame(res_list)
    df = add_issue_flags(df, low_content)

    # --- RESULTADOS VISUALES ---
    st.divider()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Analizadas", len(df))
    c2.metric("🔴 Críticos", len(df[df.severity == "Alta"]), delta_color="inverse")
    c3.metric("🟡 Advertencias", len(df[df.severity == "Media"]))
    c4.metric("🟢 Saludables", len(df[df.severity == "OK"]))

    t1, t2, t3 = st.tabs(["🎯 Prioridades", "🔍 Detalle Técnico", "📊 Datos Completos"])
    
    with t1:
        st.subheader("Hoja de Ruta: ¿Qué arreglar primero?")
        issues = df[df.issues_count > 0].sort_values("issues_count", ascending=False)
        if not issues.empty:
            for _, row in issues.head(5).iterrows():
                st.markdown(f"""
                <div class="critical-card">
                    <strong>URL:</strong> {row['url']}<br>
                    ❌ Errores detectados: {int(row['issues_count'])} | Severidad: {row['severity']}
                </div>
                """, unsafe_allow_html=True)
        else:
            st.success("¡Todo se ve perfecto por aquí!")

    with t2:
        st.write("Estado de salud On-Page e Indexación")
        st.dataframe(df[["url", "status_code", "title", "title_ok", "h1_count", "severity"]], use_container_width=True)

    with t3:
        st.write("Todos los datos extraídos para análisis profundo")
        st.dataframe(df)
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Descargar reporte CSV", data=csv, file_name="auditoria_deseo.csv", mime="text/csv")

st.markdown("---")
st.caption("DeSeo Suite MVP - Herramienta Pedagógica")
