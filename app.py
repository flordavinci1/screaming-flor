import streamlit as st
import requests
import pandas as pd
import gzip
import time
import re
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET

# --- CONFIG ---
st.set_page_config(page_title="DeSeo - Screaming Flor", layout="wide")

HEADERS = {"User-Agent": "Mozilla/5.0"}

# --- HELPERS ---
def normalize_url(u):
    u = (u or "").strip()
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    return u


def fetch_bytes(url):
    for _ in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            r.raise_for_status()
            return r.content
        except:
            time.sleep(1)
    return None


def parse_sitemap(xml_bytes):
    urls = []
    try:
        root = ET.fromstring(xml_bytes)
        for loc in root.findall(".//{*}loc"):
            if loc.text:
                urls.append(loc.text.strip())
    except Exception as e:
        st.warning(f"Error sitemap: {e}")
    return urls


def load_sitemap(sitemap_url, max_urls=20):
    content = fetch_bytes(sitemap_url)
    if not content:
        return []

    try:
        if sitemap_url.endswith(".gz"):
            content = gzip.decompress(content)
    except:
        pass

    urls = parse_sitemap(content)
    return urls[:max_urls]


def audit_url(url):
    result = {"url": url}
    start = time.time()

    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        result["status_code"] = r.status_code
        result["response_ms"] = int((time.time() - start) * 1000)

        if r.status_code != 200:
            return result

        soup = BeautifulSoup(r.text, "html.parser")

        # limpiar scripts
        for s in soup(["script", "style"]):
            s.decompose()

        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        h1 = soup.find_all("h1")

        text = soup.get_text()
        words = len(re.sub(r"\s+", " ", text).split())

        result.update({
            "title": title,
            "title_len": len(title),
            "h1_count": len(h1),
            "word_count": words
        })

    except Exception as e:
        result["error"] = str(e)

    return result


# --- SEO SCORE ---
def compute_score(row):
    score = 0

    if row.get("status_code") == 200:
        score += 25
    if 10 <= row.get("title_len", 0) <= 70:
        score += 25
    if row.get("h1_count", 0) == 1:
        score += 25
    if row.get("word_count", 0) > 300:
        score += 25

    return score


def score_label(score):
    if score >= 75:
        return "🟢 Alto"
    elif score >= 50:
        return "🟡 Medio"
    else:
        return "🔴 Bajo"


# --- UI ---
st.title("🌸 DeSeo - Screaming Flor")

mode = st.radio("Modo", ["Sitemap", "Manual"])
max_urls = st.slider("Max URLs", 5, 50, 10)

urls = []

if mode == "Sitemap":
    sitemap = st.text_input("Sitemap URL")
    if sitemap:
        urls = load_sitemap(sitemap, max_urls)
        st.write(f"URLs encontradas: {len(urls)}")

else:
    text = st.text_area("Pega URLs")
    if text:
        urls = [normalize_url(u) for u in text.splitlines() if u.strip()][:max_urls]


# --- RUN ---
if st.button("Auditar") and urls:

    results = []
    progress = st.progress(0)

    with st.spinner("Auditando..."):
        with ThreadPoolExecutor(max_workers=min(10, len(urls))) as executor:
            futures = {executor.submit(audit_url, u): u for u in urls}

            for i, f in enumerate(as_completed(futures)):
                results.append(f.result())
                progress.progress((i + 1) / len(urls))

    df = pd.DataFrame(results)

    # --- VALIDACIÓN SEGURA ---
    if df.empty:
        st.error("No se pudieron analizar URLs")
        st.stop()

    # score
    df["seo_score"] = df.apply(compute_score, axis=1)
    df["score_label"] = df["seo_score"].apply(score_label)

    st.subheader("Resultados")

    cols_to_show = [
        "url",
        "status_code",
        "title_len",
        "h1_count",
        "word_count",
        "seo_score",
        "score_label"
    ]

    # ✅ SIN STYLE (100% estable)
    st.dataframe(df[cols_to_show], use_container_width=True)

    st.download_button(
        "Descargar CSV",
        df.to_csv(index=False).encode("utf-8"),
        "reporte.csv",
        "text/csv"
    )
