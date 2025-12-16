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
    Marca issues “educativos” típicos para roadmap
