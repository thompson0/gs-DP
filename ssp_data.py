import csv
import gzip
import http.client
import os
import re
import shutil
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter

BASE_URL = "https://www.ssp.sp.gov.br/"
SOURCES = [
    "assets/estatistica/transparencia/spDados/SPDadosCriminais_2025.xlsx",
    "assets/estatistica/transparencia/spDados/SPDadosCriminais_2026.xlsx",
    "assets/estatistica/vitimas-violencia-domestica/Base_Violência contra mulher.xlsx",
]
MIN_YEAR = 2025
CATEGORY = {
    "FURTO - OUTROS": "furto",
    "ROUBO - OUTROS": "roubo",
    "ROUBO DE VEÍCULO": "roubo",
    "AMEAÇA": "ameaca",
    "DESCUMPRIMENTO DE MEDIDA PROTETIVA DE URGÊNCIA": "ameaca",
    "PORTE DE ARMA": "arma",
    "LESÃO CORPORAL DOLOSA": "lesao",
    "PERSEGUIR": "perseguicao",
    "EXTORSÃO MEDIANTE SEQUESTRO": "sequestro",
    "TENTATIVA DE HOMICÍDIO": "tentativa_homicidio",
    "OUTROS C/C/ DIGNIDADE SEXUAL": "sexual",
    "ESTUPRO": "sexual",
    "HOMICÍDIO DOLOSO": "homicidio",
    "FEMINICÍDIO": "homicidio",
    "LATROCÍNIO": "homicidio",
    "LESÃO CORPORAL SEGUIDA DE MORTE": "homicidio",
}
HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "data", "raw")
OUT_PATH = os.path.join(HERE, "data", "crimes_sp.csv.gz")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = -24.01, -23.35, -46.83, -46.36
ALIASES = {"HORA_OCORRENCIA_BO": "HORA_OCORRENCIA", "DESC_PERIODO": "DESCR_PERIODO",
           "NOME_MUNICIPIO_CIRCUNSCRICAO": "NOME_MUNICIPIO_CIRC", "NOME_MUNICIPIO_CIRCUNCRIÇÃO": "NOME_MUNICIPIO_CIRC"}
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def download(url, path, tries=5):
    part = path + ".part"
    for attempt in range(tries):
        done = os.path.getsize(part) if os.path.exists(part) else 0
        req = urllib.request.Request(url, headers={**UA, "Range": f"bytes={done}-"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r, open(part, "ab" if r.status == 206 else "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            os.replace(part, path)
            return
        except (OSError, http.client.HTTPException) as e:
            print(f"  falha ({e}), tentando de novo...")
    raise RuntimeError(f"Download failed: {url}")


def download_all():
    os.makedirs(RAW_DIR, exist_ok=True)
    paths = []
    for source in SOURCES:
        path = os.path.join(RAW_DIR, os.path.basename(source).replace(" ", "_"))
        if not os.path.exists(path):
            print(f"Baixando {source} ...")
            download(BASE_URL + urllib.parse.quote(source), path)
        paths.append(path)
    return paths


def _cells(row, shared):
    for c in row.iter(NS + "c"):
        v = c.find(NS + "v")
        x = None if v is None else v.text
        if x is not None and c.get("t") == "s":
            x = shared[int(x)]
        elif c.get("t") == "inlineStr":
            x = "".join(t.text or "" for t in c.iter(NS + "t"))
        yield c.get("r").rstrip("0123456789"), x


def read_xlsx(path):
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for _, el in ET.iterparse(z.open("xl/sharedStrings.xml")):
            if el.tag == NS + "si":
                shared.append("".join(t.text or "" for t in el.iter(NS + "t")))
                el.clear()
    sheets = [n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)]
    found = False
    for name in sorted(sheets, key=lambda n: int(re.search(r"\d+", n).group())):
        header = None
        for _, el in ET.iterparse(z.open(name)):
            if el.tag != NS + "row":
                continue
            cells = dict(_cells(el, shared))
            el.clear()
            if header is None:
                header = {col: ALIASES.get(k, k) for col, v in cells.items() if (k := (v or "").strip().upper())}
                if "LATITUDE" not in header.values():
                    break
                found = True
                continue
            yield {header[col]: v for col, v in cells.items() if col in header}
    if not found:
        raise ValueError(f"No sheet with LATITUDE header in {path}")


def period_of(descr, hora):
    d = (descr or "").lower()
    if "manh" in d or "tarde" in d:
        return "day"
    if "noite" in d or "madrugada" in d:
        return "night"
    try:
        if ":" in (hora or ""):
            hour = int(hora.split(":")[0])
        else:
            hour = int(float(hora) % 1 * 24 + 1e-6)
    except (TypeError, ValueError):
        return "unknown"
    return "day" if 6 <= hour <= 17 else "night"


def clean(r):
    if (r.get("NOME_MUNICIPIO_CIRC") or "").strip().upper() != "S.PAULO":
        return None
    crime = CATEGORY.get((r.get("NATUREZA_APURADA") or "").strip().upper())
    if crime is None or (r.get("DESCR_TIPOLOCAL") or "").strip() != "Via Pública":
        return None
    try:
        lat = float(str(r.get("LATITUDE")).replace(",", "."))
        lon = float(str(r.get("LONGITUDE")).replace(",", "."))
        year = int(float(r.get("ANO_BO")))
    except (TypeError, ValueError):
        return None
    if year < MIN_YEAR or not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
        return None
    return round(lat, 5), round(lon, 5), period_of(r.get("DESCR_PERIODO"), r.get("HORA_OCORRENCIA")), crime, year


def filter_rows(rows, seen, stats):
    out = []
    for r in rows:
        stats["read"] += 1
        rec = clean(r)
        if rec is None:
            stats["dropped"] += 1
            continue
        key = (r.get("NOME_DELEGACIA"), r.get("ANO_BO"), r.get("NUM_BO"), rec[3])
        if r.get("NUM_BO") in (None, "", "NULL"):
            stats["no_bo_number"] += 1
        elif key in seen:
            stats["dupes"] += 1
            continue
        seen.add(key)
        out.append(rec)
    return out


def main():
    t0 = time.time()
    seen, stats, records = set(), Counter(), []
    for path in download_all():
        t = time.time()
        recs = filter_rows(read_xlsx(path), seen, stats)
        records += recs
        print(f"{path}: {len(recs)} registros mantidos ({time.time() - t:.0f}s)")
    with gzip.open(OUT_PATH, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["lat", "lon", "period", "crime", "year"])
        w.writerows(records)
    print(dict(stats), "mantidas:", len(records), "por período:", dict(Counter(r[2] for r in records)))
    print(f"{OUT_PATH} ({os.path.getsize(OUT_PATH) / 1e6:.1f} MB) em {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
