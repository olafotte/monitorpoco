"""
Módulo de scraping e leitura de dados dos rankings da Defesa Civil de Blumenau.
"""

import datetime
import json
import os
import re
from typing import Dict
import urllib3
import pandas as pd
import requests
import streamlit as st
from urllib3.exceptions import InsecureRequestWarning

urllib3.disable_warnings(InsecureRequestWarning)

# Diretório raiz do projeto
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_CACHE_PATH = os.path.join(_PROJECT_ROOT, "dc_rankings_cache.json")


def _json_to_dict(payload: dict) -> Dict[str, pd.DataFrame]:
    """Converte o JSON salvo de volta para dict {dur: DataFrame}."""
    out = {}
    for dur, rows in payload.get("dados", {}).items():
        df = pd.DataFrame(rows)
        if "Acumulado_mm" in df.columns:
            df["Acumulado_mm"] = pd.to_numeric(df["Acumulado_mm"], errors="coerce")
        out[dur] = df
    return out


def _load_json_cache(cache_path: str = _DEFAULT_CACHE_PATH) -> Dict[str, pd.DataFrame]:
    """Lê dados salvos do cache local json."""
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return _json_to_dict(json.load(f))
        except Exception:
            pass
    return {}


@st.cache_data(ttl="1h", max_entries=5)
def fetch_defesa_civil_rankings() -> Dict[str, pd.DataFrame]:
    """Retorna rankings de maiores chuvas da Defesa Civil de Blumenau.

    Estratégia híbrida:
    - LOCAL com bs4 instalado: faz scraping em tempo real, atualiza dc_rankings_cache.json.
    - STREAMLIT CLOUD (sem bs4): lê dc_rankings_cache.json do repositório.
    Retorna dict {duração: DataFrame} com colunas [Estação, Região, Data/Hora, Acumulado_mm].
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return _load_json_cache()

    url = "https://defesacivil.blumenau.sc.gov.br/d/maiores-chuvas"
    headers_req = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    resultado = {}

    try:
        resp = requests.get(url, headers=headers_req, timeout=20, verify=False)
        if resp.status_code != 200:
            return _load_json_cache()
        soup = BeautifulSoup(resp.text, "html.parser")

        page_headers = soup.find_all("div", class_="page-header")
        for ph in page_headers:
            h3 = ph.find("h3")
            if not h3:
                continue
            titulo_raw = h3.get_text(separator=" ", strip=True)
            match = re.search(r"em\s+([\d]+h|[\d]+min)", titulo_raw, re.IGNORECASE)
            duracao = match.group(1) if match else titulo_raw

            small = h3.find("small")
            ultima_atualizacao = (
                small.get_text(strip=True).replace("Última atualização:", "").strip()
                if small else ""
            )

            table = ph.find_next_sibling("table")
            if not table:
                continue
            rows_data = []
            tbody = table.find("tbody")
            rows = tbody.find_all("tr") if tbody else table.find_all("tr")
            for tr in rows:
                cells = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(cells) == 4:
                    try:
                        acumulado_float = float(cells[3].replace(",", "."))
                    except ValueError:
                        acumulado_float = None
                    rows_data.append({
                        "Estação": cells[0],
                        "Região": cells[1],
                        "Data/Hora": cells[2],
                        "Acumulado_mm": acumulado_float,
                        "ultima_atualizacao": ultima_atualizacao,
                    })
            if rows_data:
                df_dur = pd.DataFrame(rows_data)
                resultado[duracao] = df_dur

        # Atualizar o cache JSON se scraping tiver sucesso
        if resultado:
            cache_payload = {
                "gerado_em": datetime.datetime.now().isoformat(),
                "fonte": url,
                "dados": {
                    dur: df.to_dict(orient="records")
                    for dur, df in resultado.items()
                },
            }
            try:
                with open(_DEFAULT_CACHE_PATH, "w", encoding="utf-8") as f:
                    json.dump(cache_payload, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

    except Exception:
        return _load_json_cache()

    return resultado
