"""
Módulo de cálculo das Curvas IDF (Intensidade-Duração-Frequência) via Distribuição de Gumbel.
"""

from typing import Dict, Tuple, Any
import pandas as pd
import streamlit as st

from src.data.weather import fetch_historical_5years_precip
from src.data.adolfo_konder import load_adolfo_konder_dataset
from src.models.gumbel import fit_gumbel, gumbel_quantile


@st.cache_data(ttl="1h", max_entries=10)
def compute_gumbel_idf_results(
    latitude: float,
    longitude: float,
    era5_factor: float
) -> Tuple[Dict[int, Dict[int, float]], Dict[int, Any]]:
    """
    Calcula os resultados de Gumbel IDF para durações de 1h a 24h e períodos de retorno de 2 a 100 anos.
    Retorna (idf_res, gumbel_params).
    """
    try:
        hist5_df, hist_err = fetch_historical_5years_precip(latitude, longitude, start_date_str="")
        if hist5_df.empty:
            return {}, {}

        konder_df = load_adolfo_konder_dataset()
        if not konder_df.empty:
            hist5_df = pd.DataFrame({"precipitation": konder_df["precip_15min"].resample("1h").sum()}).dropna()

        hist5_df = hist5_df.copy()
        hist5_df["precipitation"] = hist5_df["precipitation"] * era5_factor

        duracoes_h = [1, 2, 3, 6, 12, 24]
        trs = [2, 5, 10, 25, 50, 100]

        idf_res: Dict[int, Dict[int, float]] = {}
        gumbel_p: Dict[int, Any] = {}
        for dur in duracoes_h:
            rolled = hist5_df["precipitation"].rolling(window=dur, min_periods=dur).sum()
            rolled_intensity = rolled / dur
            annual_max = rolled_intensity.groupby(hist5_df.index.year).max().dropna().values
            if len(annual_max) < 2:
                continue
            alpha, u = fit_gumbel(annual_max)
            gumbel_p[dur] = (alpha, u, annual_max)
            idf_res[dur] = {}
            for tr in trs:
                idf_res[dur][tr] = max(gumbel_quantile(alpha, u, tr), 0.0)

        return idf_res, gumbel_p
    except Exception:
        return {}, {}
