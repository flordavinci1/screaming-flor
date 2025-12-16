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

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/114.0.0.0 Safari/537.36"
    )
}

DEFAULT_TIMEOUT = 15


# ---------------------------
# Helpers: URL + Sitemap
# ---------------------------
def normalize_url(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    if u.startswith("www."):
        u = "https://" + u
    return u


def fetch_bytes(url: str, timeout: int = DEFAULT_TIMEOUT) -> bytes:
    r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r.content


def parse_sitemap_xml(xml_bytes: bytes) -> tuple[list[str], list[str]]:
    sitemaps, urls = [], []
    root = ET.fromstring(xml_bytes)

    def findall_local(tag_name: str):
        return root.findall(f".//{{*}}{tag_name}")

    sitemap_locs = [
        el.text.strip()
        for el in findall_local("sitemap")
        for el in el.findall(".//{*}loc")
        if el.text
    ]
    if sitemap_locs:
        sitemaps.extend(sitemap_locs)
        return sitemaps, urls

    locs = [el.text.strip() for el in findall_local("loc") if el.text]
    urls.extend(locs)
    return sitemaps, urls


def load_sitemap_urls(sitemap_url: str, max_urls: int = 300) -> list[str]:
    sitemap_url = normalize_url(sitemap_url)
    if not sitemap_url:
        return []

    collected: list[str] = []
    to_process = [sitemap_url]
    seen = set()

    while to_process and len(collected) < max_urls:
        current = to_process.pop(0)
        if current in seen:
            continue
        seen.add(current)

        try:
            content = fetch_bytes(current)
            if current.endswith(".gz"):
                content = gzip.decompress(content)

            sitemaps, urls = parse_sitemap_xml(content)

            for sm in sitemaps:
                sm = normalize_url(sm)
                if sm and sm not in seen:
                    to_process.append(sm)

            for u in urls:
                u = normalize_url(u)
                if u and u not in collected:
                    collected.append(u)
                    if len(collected) >= max_urls:
                        break

        except Exception:
            continue

    return collected


# ---------------------------
# Helpers: Audit checks
# ---------------------------
def safe_text(el) -> str:
    if not el:
        return ""
    return el.get_text(" ", strip=True) or ""


def extract_meta(soup: BeautifulSoup, name: str) -> str:
    tag = soup.find("meta", attrs={"name": name})
    if tag and tag.get("content"):
        return tag["content"].strip()
    return ""


def extract_meta_property(soup: BeautifulSoup, prop: str) -> str:
    tag = soup.find("meta", attrs={"property": prop})
    if tag and tag.get("content"):
        return tag["content"].strip()
    return ""


def audit_one(url: str, base_domain: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
    out = {"url": url}
    started = time.time()

    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        out["final_url"] = r.url
        out["status_code"] = r.status_code
        out["redirected"] = (r.url != url)
        out["https"] = (urlparse(r.url).scheme == "https")
        out["content_type"] = r.headers.get("Content-Type", "")
        out["response_ms"] = int((time.time() - started) * 1000)

        xrobots = r.headers.get("X-Robots-Tag", "")
        out["x_robots_tag"] = xrobots
        xrobots_lower = (xrobots or "").lower()
        out["xrobots_noindex"] = ("noindex" in xrobots_lower)

        html = r.text or ""
        soup = BeautifulSoup(html, "html.parser")

        # --- Title / Meta description ---
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        meta_desc = extract_meta(soup, "description")

        out["title"] = title
        out["title_len"] = len(title)
        out["title_ok"] = 10 <= len(title) <= 70

        out["meta_desc"] = meta_desc
        out["meta_desc_len"] = len(meta_desc)
        out["meta_desc_ok"] = 50 <= len(meta_desc) <= 160

        # Canonical
        canon = soup.find("link", rel=lambda x: x and "canonical" in x)
        canonical_url = canon.get("href", "").strip() if canon else ""
        out["canonical"] = canonical_url
        out["canonical_self"] = bool(canonical_url) and (normalize_url(canonical_url) == normalize_url(r.url))

        # Meta robots
        robots_meta = extract_meta(soup, "robots")
        out["meta_robots"] = robots_meta
        robots_lower = (robots_meta or "").lower()
        out["meta_noindex"] = ("noindex" in robots_lower)
        out["meta_nofollow"] = ("nofollow" in robots_lower)

        # Hreflang count
        hreflangs = soup.find_all("link", rel=lambda x: x and "alternate" in x)
        hreflang_count = 0
        for l in hreflangs:
            if l.get("hreflang") and l.get("href"):
                hreflang_count += 1
        out["hreflang_count"] = hreflang_count

        # Headings
        h1s = soup.find_all("h1")
        out["h1_count"] = len(h1s)
        out["h1_text"] = safe_text(h1s[0])[:200] if len(h1s) > 0 else ""
        out["h1_ok"] = (len(h1s) == 1)

        h2s = soup.find_all("h2")
        h3s = soup.find_all("h3")
        out["h2_count"] = len(h2s)
        out["h3_count"] = len(h3s)

        # Content (estimación)
        main_text = soup.get_text(" ", strip=True)
        main_text = re.sub(r"\s+", " ", main_text)
        out["word_count_est"] = len(main_text.split())

        # Images alt
        imgs = soup.find_all("img")
        out["images_total"] = len(imgs)
        missing_alt = 0
        for img in imgs:
            alt = img.get("alt")
            if alt is None or str(alt).strip() == "":
                missing_alt += 1
        out["images_missing_alt"] = missing_alt

        # Links internal/external + anchors vacíos
        anchors = soup.find_all("a", href=True)
        internal = 0
        external = 0
        empty_anchor_text = 0
        parsed_final = urlparse(r.url)
        netloc = parsed_final.netloc

        for a in anchors:
            href = a.get("href", "").strip()
            if not href:
                continue
            absu = urljoin(r.url, href)
            if urlparse(absu).scheme not in ("http", "https"):
                continue

            if urlparse(absu).netloc == netloc:
                internal += 1
            else:
                external += 1

            if safe_text(a) == "":
                empty_anchor_text += 1

        out["links_total"] = len(anchors)
        out["internal_links_count"] = internal
        out["external_links_count"] = external
        out["anchors_empty_text"] = empty_anchor_text

        # OG + schema blocks
        out["og_title"] = extract_meta_property(soup, "og:title")
        out["og_desc"] = extract_meta_property(soup, "og:description")

        ld_json = soup.find_all("script", attrs={"type": "application/ld+json"})
        out["ldjson_blocks"] = len(ld_json)

        # Indexable “estimado”
        out["indexable_est"] = (r.status_code == 200 and not out["meta_noindex"] and not out["xrobots_noindex"])

        # Dominio base (si viene de sitemap)
        out["same_domain_as_input"] = True
        if base_domain:
            out["same_domain_as_input"] = (urlparse(r.url).netloc == base_domain)

    except RequestException as e:
        out["error"] = str(e)
        out["response_ms"] = int((time.time() - started) * 1000)

    return out


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


# ---------------------------
# Post-processing: issues + duplicates
# ---------------------------
def add_duplicate_flags(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    def norm(s):
        if pd.isna(s):
            return ""
        s = str(s).strip().lower()
        s = re.sub(r"\s+", " ", s)
        return s

    # Normalizamos para detectar duplicados “reales”
    if "title" in df.columns:
        df["title_norm"] = df["title"].apply(norm)
        df["dup_title"] = df["title_norm"].ne("") & df["title_norm"].duplicated(keep=False)
    else:
        df["dup_title"] = False

    if "meta_desc" in df.columns:
        df["meta_desc_norm"] = df["meta_desc"].apply(norm)
        df["dup_meta_desc"] = df["meta_desc_norm"].ne("") & df["meta_desc_norm"].duplicated(keep=False)
    else:
        df["dup_meta_desc"] = False

    if "h1_text" in df.columns:
        df["h1_norm"] = df["h1_text"].apply(norm)
        df["dup_h1"] = df["h1_norm"].ne("") & df["h1_norm"].duplicated(keep=False)
    else:
        df["dup_h1"] = False

    if "canonical" in df.columns:
        df["canonical_norm"] = df["canonical"].apply(norm)
        df["dup_canonical"] = df["canonical_norm"].ne("") & df["canonical_norm"].duplicated(keep=False)
    else:
        df["dup_canonical"] = False

    return df


def add_issue_flags(df: pd.DataFrame) -> pd.DataFrame:
    """
    Marca issues “educativos” típicos para roadmap:
    - status != 200, noindex, no https, title/meta fuera de rango, H1 != 1,
      word_count muy bajo (umbral configurable), imágenes sin alt, anchors vacíos,
      canonical faltante / no self, duplicados de title/meta/h1/canonical
    """
    df = df.copy()

    # Base booleans con fallback
    df["issue_fetch_error"] = df["error"].notna() if "error" in df.columns else False
    df["issue_status"] = (df["status_code"].fillna(0).astype(int) != 200) if "status_code" in df.columns else False
    df["issue_noindex"] = df.get("meta_noindex", False) | df.get("xrobots_noindex", False)

    df["issue_https"] = ~df.get("https", True)
    df["issue_title"] = ~df.get("title_ok", True)
    df["issue_meta_desc"] = ~df.get("meta_desc_ok", True)
    df["issue_h1"] = ~df.get("h1_ok", True)

    # “Profundidad” (educativo): muy bajo puede indicar thin content (ojo, hay excepciones)
    if "word_count_est" in df.columns:
        df["issue_low_content"] = df["word_count_est"].fillna(0).astype(int) < 250
    else:
        df["issue_low_content"] = False

    if "images_missing_alt" in df.columns:
        df["issue_missing_alt"] = df["images_missing_alt"].fillna(0).astype(int) > 0
    else:
        df["issue_missing_alt"] = False

    if "anchors_empty_text" in df.columns:
        df["issue_empty_anchors"] = df["anchors_empty_text"].fillna(0).astype(int) > 0
    else:
        df["issue_empty_anchors"] = False

    # Canonical issues: faltante o no self (si existe)
    if "canonical" in df.columns:
        df["issue_canonical_missing"] = df["canonical"].fillna("").astype(str).str.strip().eq("")
    else:
        df["issue_canonical_missing"] = False

    if "canonical_self" in df.columns:
        # Solo aplica si canonical existe
        df["issue_canonical_not_self"] = (~df["canonical_self"]) & (~df.get("issue_canonical_missing", False))
    else:
        df["issue_canonical_not_self"] = False

    # Duplicados
    df["issue_dup_title"] = df.get("dup_title", False)
    df["issue_dup_meta_desc"] = df.get("dup_meta_desc", False)
    df["issue_dup_h1"] = df.get("dup_h1", False)
    df["issue_dup_canonical"] = df.get("dup_canonical", False)

    # Score simple (para ordenar)
    issue_cols = [c for c in df.columns if c.startswith("issue_")]
    df["issues_count"] = df[issue_cols].sum(axis=1).astype(int)

    # Una etiqueta rápida de severidad (educativa)
    def severity(n: int) -> str:
        if n >= 6:
            return "Alta"
        if n >= 3:
            return "Media"
        if n >= 1:
            return "Baja"
        return "OK"

    df["issues_severity"] = df["issues_count"].apply(severity)

    # Texto explicativo compacto (para alumnos)
    def build_issue_notes(row) -> str:
        notes = []
        if row.get("issue_fetch_error"): notes.append("Error de fetch")
        if row.get("issue_status"): notes.append("Status ≠ 200")
        if row.get("issue_noindex"): notes.append("Noindex")
        if row.get("issue_https"): notes.append("No HTTPS")
        if row.get("issue_title"): notes.append("Title fuera de rango")
        if row.get("issue_meta_desc"): notes.append("Meta desc fuera de rango")
        if row.get("issue_h1"): notes.append("H1 ≠ 1")
        if row.get("issue_low_content"): notes.append("Contenido bajo")
        if row.get("issue_missing_alt"): notes.append("Imágenes sin ALT")
        if row.get("issue_empty_anchors"): notes.append("Anchors sin texto")
        if row.get("issue_canonical_missing"): notes.append("Canonical faltante")
        if row.get("issue_canonical_not_self"): notes.append("Canonical no self")
        if row.get("issue_dup_title"): notes.append("Title duplicado")
        if row.get("issue_dup_meta_desc"): notes.append("Meta desc duplicada")
        if row.get("issue_dup_h1"): notes.append("H1 duplicado")
        if row.get("issue_dup_canonical"): notes.append("Canonical duplicado")
        return " | ".join(notes)

    df["issues_notes"] = df.apply(build_issue_notes, axis=1)

    return df


def build_duplicates_df(df: pd.DataFrame) -> pd.DataFrame:
    """Devuelve un DF solo con duplicados para exportar / revisar."""
    cols = ["url", "final_url", "title", "meta_desc", "h1_text", "canonical",
            "dup_title", "dup_meta_desc", "dup_h1", "dup_canonical"]
    cols = [c for c in cols if c in df.columns]
    dup = df[
        (df.get("dup_title", False)) |
        (df.get("dup_meta_desc", False)) |
        (df.get("dup_h1", False)) |
        (df.get("dup_canonical", False))
    ][cols].copy()
    return dup


# ---------------------------
# UI
# ---------------------------
st.set_page_config(page_title="Screaming Flor – Auditoría SEO", layout="wide")
st.title("🧰 Screaming Flor – Auditoría SEO (educativa)")

st.markdown(
    """
Esta herramienta permite auditar páginas en batch para aprender y aplicar:
- **SEO técnico básico** (status, redirects, HTTPS, señales de indexabilidad)
- **SEO on-page** (title, meta description, headings, imágenes, enlaces)
- **mínimos relevantes** (canonical, robots, hreflang, OG, schema básico)

Podés cargar URLs de dos maneras:
1) **Sitemap XML** (recomendado)  
2) **Lista pegada de URLs**
"""
)

with st.sidebar:
    st.header("📥 Fuente de URLs")
    mode = st.radio("¿Cómo querés cargar las páginas?", ["Desde Sitemap", "Pegar lista de URLs"], index=0)

    max_urls = st.slider("Máximo de URLs a auditar", min_value=10, max_value=300, value=50, step=10)
    concurrency = st.slider("Concurrencia (velocidad)", min_value=1, max_value=12, value=6, step=1)
    timeout = st.slider("Timeout por URL (segundos)", min_value=5, max_value=30, value=15, step=1)

    st.header("🧪 Umbrales educativos")
    low_content_threshold = st.slider("Contenido bajo (word_count)", 50, 800, 250, 25)
    st.caption("Tip: el umbral es orientativo; algunas páginas (p. ej. contacto) pueden ser cortas y estar OK.")

urls: list[str] = []

if mode == "Desde Sitemap":
    sitemap_url = st.text_input("URL del sitemap (xml o .gz)", placeholder="https://tusitio.com/sitemap.xml")
    if sitemap_url:
        with st.spinner("Leyendo sitemap..."):
            urls = load_sitemap_urls(sitemap_url, max_urls=max_urls)

        if urls:
            st.success(f"Se detectaron {len(urls)} URLs desde el sitemap (máx {max_urls}).")
            with st.expander("Ver URLs detectadas"):
                st.write("\n".join(urls[:200]))
                if len(urls) > 200:
                    st.caption("Mostrando las primeras 200.")
        else:
            st.warning("No se pudieron extraer URLs del sitemap. Revisá que la URL sea correcta y accesible.")

else:
    urls_text = st.text_area(
        "Pegá URLs (una por línea)",
        height=220,
        placeholder="https://ejemplo.com/\nhttps://ejemplo.com/servicio\nhttps://ejemplo.com/blog/post"
    )
    if urls_text:
        urls = [normalize_url(u) for u in urls_text.splitlines() if normalize_url(u)]
        urls = list(dict.fromkeys(urls))  # dedupe manteniendo orden
        urls = urls[:max_urls]
        st.info(f"URLs cargadas: {len(urls)} (máx {max_urls}).")

st.markdown("---")

run = st.button("🚀 Ejecutar auditoría", type="primary", disabled=(len(urls) == 0))

if run and urls:
    base_domain = urlparse(urls[0]).netloc if urls else None

    results = []
    errors = 0

    st.write(f"Auditoría en curso: **{len(urls)} URLs**")
    progress = st.progress(0)

    with st.spinner("Analizando páginas..."):
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futures = {ex.submit(audit_one, u, base_domain, timeout): u for u in urls}
            done = 0
            for fut in as_completed(futures):
                res = fut.result()
                results.append(res)
                if "error" in res:
                    errors += 1
                done += 1
                progress.progress(int(done / len(urls) * 100))

    df = pd.DataFrame(results)

    # Post-procesos
    df = add_duplicate_flags(df)
    df = add_issue_flags(df)
    # aplicar umbral elegido en UI
    if "word_count_est" in df.columns:
        df["issue_low_content"] = df["word_count_est"].fillna(0).astype(int) < int(low_content_threshold)
        # recomputar notes + count con el nuevo umbral
        df = add_issue_flags(df)  # vuelve a calcular issues_count y notes

    # Orden útil
    preferred_cols = [
        "url", "final_url", "status_code", "redirected", "https", "indexable_est",
        "issues_severity", "issues_count", "issues_notes",
        "meta_noindex", "xrobots_noindex", "meta_robots", "x_robots_tag",
        "title", "title_len", "title_ok", "dup_title",
        "meta_desc", "meta_desc_len", "meta_desc_ok", "dup_meta_desc",
        "h1_count", "h1_ok", "h1_text", "dup_h1",
        "h2_count", "h3_count",
        "canonical", "canonical_self", "dup_canonical",
        "hreflang_count",
        "word_count_est", "issue_low_content",
        "images_total", "images_missing_alt",
        "links_total", "internal_links_count", "external_links_count", "anchors_empty_text",
        "og_title", "og_desc", "ldjson_blocks",
        "content_type", "response_ms", "same_domain_as_input", "error"
    ]
    cols = [c for c in preferred_cols if c in df.columns] + [c for c in df.columns if c not in preferred_cols]
    df = df[cols].sort_values(by=["issues_count", "status_code"], ascending=[False, True], na_position="last")

    st.success(f"Auditoría finalizada. Errores de fetch: {errors}/{len(urls)}")

    # Resumen ejecutivo
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("URLs auditadas", len(df))
    with c2:
        st.metric("Con issues", int((df["issues_count"] > 0).sum()))
    with c3:
        st.metric("No indexables (estimación)", int((~df.get("indexable_est", True)).sum()) if "indexable_est" in df else 0)
    with c4:
        st.metric("Duplicados (Title/Meta/H1)", int((df.get("dup_title", False) | df.get("dup_meta_desc", False) | df.get("dup_h1", False)).sum()))

    st.markdown("### 📊 Resultados completos")
    st.dataframe(df, use_container_width=True)

    st.download_button(
        "⬇️ Descargar CSV (completo)",
        data=to_csv_bytes(df),
        file_name="screaming_flor_audit_full.csv",
        mime="text/csv"
    )

    # SOLO ISSUES
    issues_df = df[df["issues_count"] > 0].copy()
    st.markdown("---")
    st.markdown("### 🧯 Solo issues (para armar el roadmap)")
    st.caption("Este CSV es ideal para que el alumno copie/pegue en la tab de Priorización.")
    st.dataframe(issues_df.head(300), use_container_width=True)

    st.download_button(
        "⬇️ Descargar CSV (solo issues)",
        data=to_csv_bytes(issues_df),
        file_name="screaming_flor_audit_issues_only.csv",
        mime="text/csv"
    )

    # DUPLICADOS
    dup_df = build_duplicates_df(df)
    st.markdown("---")
    st.markdown("### ♻️ Duplicados detectados (Title / Meta / H1 / Canonical)")
    if len(dup_df) == 0:
        st.info("No se detectaron duplicados en Title/Meta/H1/Canonical dentro del set auditado.")
    else:
        st.dataframe(dup_df, use_container_width=True)
        st.download_button(
            "⬇️ Descargar CSV (duplicados)",
            data=to_csv_bytes(dup_df),
            file_name="screaming_flor_audit_duplicates.csv",
            mime="text/csv"
        )

    # Lectura rápida (alineada con tu documento)
    st.markdown("---")
    st.markdown("## 🧠 Lectura rápida (para el roadmap)")
    st.markdown(
        """
Usá estas categorías para completar tu documento:

- **Técnico / indexación:** status ≠ 200, noindex (meta o headers), problemas de HTTPS, redirects.
- **On-page:** titles / metas fuera de rango, H1 incorrecto, duplicados de Title/Meta/H1.
- **Contenido:** contenido bajo (word_count estimado por debajo del umbral definido).
- **Accesibilidad / SEO:** imágenes sin ALT, anchors sin texto, enlazado interno pobre.
- **Arquitectura:** canonical faltante o no self (señal para revisar duplicación/parametrización).
"""
    )

# CTA final
st.markdown("---")
st.markdown(
    """
    <div style="text-align: center;">
        <p>✨ Herramienta creada con fines educativos para aprender SEO de forma aplicada.</p>
        <p>💌 Feedback / sugerencias: <a href="mailto:florencia@crawla.agency">florencia@crawla.agency</a></p>
        <a href="https://www.linkedin.com/in/festevez3005/" target="_blank">
            <button style="padding:10px 20px; font-size:16px; border:none; border-radius:8px; cursor:pointer;">
                🌐 Conectá conmigo en LinkedIn
            </button>
        </a>
    </div>
    """,
    unsafe_allow_html=True
)
