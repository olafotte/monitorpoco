"""
Módulo de ingestão dos dados pluviométricos reais da Estação Ponte Adolfo Konder.
"""
import glob
import os
import re
import pandas as pd
import streamlit as st


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
