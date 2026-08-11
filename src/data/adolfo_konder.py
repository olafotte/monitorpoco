"""
Módulo de ingestão, scraping ao vivo e calibração dos dados da Estação Ponte Adolfo Konder (Defesa Civil de Blumenau).
"""
import glob
import os
import re
import numpy as np
import pandas as pd
import requests
import streamlit as st
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _get_konder_files_mtime() -> float:
    """Retorna o timestamp da última modificação dos arquivos Ponte Adolfo Konder_pluv_*.txt."""
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    files = sorted(glob.glob(os.path.join(base_dir, "Ponte Adolfo Konder_pluv_*.txt")))
    if not files:
        files = sorted(glob.glob("Ponte Adolfo Konder_pluv_*.txt"))
    if not files:
        return 0.0
    return max(os.path.getmtime(f) for f in files)


@st.cache_data(ttl=86400)
def load_adolfo_konder_dataset(_mtime_key: float = 0.0) -> pd.DataFrame:
    """Lê e consolida todos os arquivos de benchmark Ponte Adolfo Konder_pluv_20XX.txt.
    Retorna DataFrame de série de 15 minutos em America/Sao_Paulo com colunas:
    - 'precip_15min': precipitação incremental medida a cada 15 min (mm)
    - 'precip_mmh': taxa horária equivalente a cada 15 min (mm/h = precip_15min * 4)
    - 'precipitation': chuva acumulada em 1 hora móvel (mm/h)
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    files = sorted(glob.glob(os.path.join(base_dir, "Ponte Adolfo Konder_pluv_*.txt")))
    if not files:
        files = sorted(glob.glob("Ponte Adolfo Konder_pluv_*.txt"))
    if not files:
        return pd.DataFrame(columns=["precip_15min", "precip_mmh", "precipitation"])

    all_dfs = []
    for filepath in files:
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            data_lines = [l.strip() for l in lines if l.strip() and l.strip()[0].isdigit()]
            rows = []
            for l in data_lines:
                parts = l.split("\t")
                if len(parts) >= 2:
                    rows.append((parts[0].strip(), parts[1].strip()))
                else:
                    subparts = re.split(r"\s{2,}", l)
                    if len(subparts) >= 2:
                        rows.append((subparts[0].strip(), subparts[1].strip()))

            if rows:
                df = pd.DataFrame(rows, columns=["raw_dt", "val_str"])
                df["datetime"] = pd.to_datetime(df["raw_dt"], format="%d/%m/%Y - %H:%M:%S", errors="coerce")
                df["precip_15min"] = pd.to_numeric(df["val_str"], errors="coerce").fillna(0.0)
                df = df.dropna(subset=["datetime"]).set_index("datetime")[["precip_15min"]]
                all_dfs.append(df)
        except Exception:
            pass

    if not all_dfs:
        return pd.DataFrame(columns=["precip_15min", "precip_mmh", "precipitation"])

    full_15min = pd.concat(all_dfs).sort_index()
    full_15min = full_15min[~full_15min.index.duplicated(keep="first")]

    if full_15min.index.tz is None:
        full_15min.index = full_15min.index.tz_localize("America/Sao_Paulo", ambiguous="NaT", nonexistent="shift_forward")
        full_15min = full_15min.dropna(how="all")

    full_15min["precip_mmh"] = full_15min["precip_15min"] * 4.0
    full_15min["precipitation"] = full_15min["precip_15min"].rolling(window=4, min_periods=1).sum()
    return full_15min


def get_adolfo_konder_data() -> pd.DataFrame:
    """Wrapper amigável que injeta o _mtime_key para garantia de cache dinâmico."""
    mtime = _get_konder_files_mtime()
    return load_adolfo_konder_dataset(_mtime_key=mtime)


@st.cache_data(ttl=300)
def fetch_live_adolfo_konder() -> dict:
    """Raspagem ao vivo da estação Ponte Adolfo Konder na Defesa Civil de Blumenau.
    Retorna dicionário com medições recentes de chuva (15m, 1h, 24h, 168h) e nível do Rio Itajaí-Açu.
    """
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    res = {
        "estacao": "Ponte Adolfo Konder",
        "regiao": "Central",
        "hora_leitura": None,
        "precip_15min_mm": 0.0,
        "precip_30min_mm": 0.0,
        "precip_1h_mm": 0.0,
        "precip_12h_mm": 0.0,
        "precip_24h_mm": 0.0,
        "precip_168h_mm": 0.0,
        "nivel_rio_m": None,
        "variacao_rio_m": None,
        "status_rio": "Desconhecido",
        "sucesso": False,
        "erro": None,
    }

    # 1. Raspagem de Pluviometria
    try:
        url_main = "https://defesacivil.blumenau.sc.gov.br/d/"
        r = requests.get(url_main, headers=headers, timeout=12, verify=False)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            tables = soup.find_all("table")
            if tables:
                for tr in tables[0].find_all("tr"):
                    cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                    if cells and "ponte adolfo konder" in cells[0].lower():
                        # ['Ponte Adolfo Konder', 'Central', '11/08/2026 12:30', '0,8', '1,2', '3,4', '4,4', '4,4', '36,2']
                        res["hora_leitura"] = cells[2] if len(cells) > 2 else ""
                        res["precip_15min_mm"] = float(cells[3].replace(",", ".")) if len(cells) > 3 else 0.0
                        res["precip_30min_mm"] = float(cells[4].replace(",", ".")) if len(cells) > 4 else 0.0
                        res["precip_1h_mm"] = float(cells[5].replace(",", ".")) if len(cells) > 5 else 0.0
                        res["precip_12h_mm"] = float(cells[6].replace(",", ".")) if len(cells) > 6 else 0.0
                        res["precip_24h_mm"] = float(cells[7].replace(",", ".")) if len(cells) > 7 else 0.0
                        res["precip_168h_mm"] = float(cells[8].replace(",", ".")) if len(cells) > 8 else 0.0
                        res["sucesso"] = True
                        break
    except Exception as e:
        res["erro"] = f"Erro pluviômetro: {str(e)}"

    # 2. Raspagem de Nível do Rio
    try:
        url_rio = "https://defesacivil.blumenau.sc.gov.br/d/nivel-do-rio"
        r_rio = requests.get(url_rio, headers=headers, timeout=12, verify=False)
        if r_rio.status_code == 200:
            soup_rio = BeautifulSoup(r_rio.text, "html.parser")
            tables_rio = soup_rio.find_all("table")
            if tables_rio:
                # Tabela 0: leituras
                rows = tables_rio[0].find_all("tr")
                if len(rows) > 1:
                    cells = [td.get_text(strip=True) for td in rows[1].find_all(["td", "th"])]
                    if len(cells) >= 2:
                        res["nivel_rio_m"] = float(cells[1].replace(",", "."))
                        if len(cells) >= 3:
                            res["variacao_rio_m"] = float(cells[2].replace(",", "."))
                # Tabela 1: status
                if len(tables_rio) > 1:
                    status_rows = tables_rio[1].find_all("tr")
                    if len(status_rows) >= 2:
                        status_cells = [td.get_text(strip=True) for td in status_rows[1].find_all(["td", "th"])]
                        if len(status_cells) >= 2:
                            res["status_rio"] = status_cells[1]
    except Exception as e:
        if res["erro"]:
            res["erro"] += f" | Erro nível: {str(e)}"
        else:
            res["erro"] = f"Erro nível: {str(e)}"

    return res


def calculate_station_era5_factor(konder_df: pd.DataFrame = None, weather_df: pd.DataFrame = None) -> dict:
    """Calcula dinamicamente o fator de correção do ERA5 baseado nas medições empíricas da Ponte Adolfo Konder.
    Se weather_df for fornecido e contiver dados sobrepostos, compara a precipitação direta.
    Caso contrário, calcula a relação média dos picos anuais históricos da estação contra a reanálise ERA5.
    """
    if konder_df is None or konder_df.empty:
        konder_df = get_adolfo_konder_data()

    yearly_factors = {}
    
    # 1. Se houver weather_df com sobreposição direta de datas:
    if weather_df is not None and not weather_df.empty and not konder_df.empty:
        try:
            # Resample da estação para 1 hora
            konder_h = konder_df["precip_15min"].resample("1h").sum()
            weather_h = weather_df["precipitation"].dropna()

            # Encontrar índice comum
            common_idx = konder_h.index.intersection(weather_h.index)
            if len(common_idx) >= 24: # Pelo menos 24h de sobreposição
                k_sub = konder_h.loc[common_idx]
                w_sub = weather_h.loc[common_idx]

                sum_k = k_sub.sum()
                sum_w = w_sub.sum()
                max_k = k_sub.max()
                max_w = w_sub.max()

                if sum_w > 0 and max_w > 0:
                    ratio_sum = sum_k / sum_w
                    ratio_max = max_k / max_w
                    # Fator ponderado (70% pela relação dos picos mais intensos, 30% pelo total acumulado)
                    calculated_factor = round(float(0.70 * ratio_max + 0.30 * ratio_sum), 2)
                    calculated_factor = max(1.0, min(calculated_factor, 5.0))
                    return {
                        "recommended_factor": calculated_factor,
                        "method": f"Sobreposição direta de datas ({len(common_idx)}h de medição)",
                        "ratio_max": round(float(ratio_max), 2),
                        "ratio_sum": round(float(ratio_sum), 2),
                        "max_station_mmh": round(float(max_k), 1),
                        "max_era5_mmh": round(float(max_w), 1),
                    }
        except Exception:
            pass

    # 2. Fallback: Calibração pelos picos históricos anuais do benchmark Ponte Adolfo Konder (2021-2025)
    # Referência de picos típicos ERA5 não-escalados em Blumenau para os mesmos anos:
    era5_yearly_peaks = {
        2021: 18.2,
        2022: 20.4,
        2023: 19.5,
        2024: 21.0,
        2025: 22.3,
    }

    if not konder_df.empty:
        konder_h = konder_df["precip_15min"].resample("1h").sum()
        for yr, era5_peak in era5_yearly_peaks.items():
            yr_str = str(yr)
            yr_data = konder_h[konder_h.index.year == yr]
            if not yr_data.empty:
                k_max = yr_data.max()
                if k_max > 0:
                    r_factor = k_max / era5_peak
                    yearly_factors[yr] = round(float(r_factor), 2)

    if yearly_factors:
        avg_factor = float(np.mean(list(yearly_factors.values())))
        # Arredondar para 2 casas
        recommended = round(avg_factor, 2)
    else:
        recommended = 2.60

    return {
        "recommended_factor": max(1.0, min(recommended, 5.0)),
        "method": "Média empírica dos picos anuais da Estação Ponte Adolfo Konder (2021-2025)",
        "yearly_factors": yearly_factors,
    }
