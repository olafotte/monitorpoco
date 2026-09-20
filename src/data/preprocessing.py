"""
Módulo de pré-processamento, alinhamento temporal, detecção e imputação de gaps.
"""

import json
from typing import Optional
import numpy as np
import pandas as pd
import streamlit as st

from src.config import (
    FUNDODOPOCO,
    DEFAULT_FATOR_M3H_PARA_CMH,
    DEFAULT_D_ON,
    DEFAULT_D_OFF,
    DEFAULT_D_ON2,
    DEFAULT_D_OFF2,
    DEFAULT_R_GND,
    DEFAULT_VAZAO_BOMBA_M3H,
)
from src.data.sensor_config import get_dist_borda_series


@st.cache_data(ttl="1h", max_entries=10)
def preprocess_time_series(
    input_df: pd.DataFrame,
    sensor_history_json_str: str = "",
    active_dist_cm: float = 70.0,
    freq_mode: str = "auto",
) -> pd.DataFrame:
    """
    Padroniza fuso horário, calcula distâncias geométricas e constrói a grade temporal contínua.
    """
    df = input_df.copy()
    ts_col = pd.to_datetime(df["timestamp"], errors="coerce")

    # Ignorar leituras corrompidas do ESP32 com ano < 2025 (ex: 1970)
    valid_years = ts_col.dt.year >= 2025
    df = df[valid_years].copy()
    ts_col = ts_col[valid_years]

    if ts_col.dt.tz is None:
        df["timestamp"] = ts_col.dt.tz_localize("UTC").dt.tz_convert("America/Sao_Paulo")
    else:
        df["timestamp"] = ts_col.dt.tz_convert("America/Sao_Paulo")

    sensor_history = json.loads(sensor_history_json_str) if sensor_history_json_str else []

    df["nivel_cm_raw"] = pd.to_numeric(df["nivel_cm"], errors="coerce")

    # Atribuir histórico dinâmico de dist_borda_cm a cada timestamp
    df["dist_borda_cm"] = get_dist_borda_series(
        df["timestamp"],
        history=sensor_history,
        active_dist_override=active_dist_cm
    )

    # Nível da água a partir do TOPO DO POÇO (0.0 cm = Topo do Poço, medidas positivas para baixo)
    df["dist_borda_agua_cm"] = df["dist_borda_cm"].abs() + df["nivel_cm_raw"]
    df["nivel_cm"] = df["dist_borda_agua_cm"]
    df["distancia_sensor_cm"] = df["nivel_cm"]

    df["altura_agua_cm"] = (FUNDODOPOCO - df["distancia_sensor_cm"]).clip(lower=0)

    # Determinar a frequência da grade de amostragem
    if freq_mode == "1min":
        target_freq = "1min"
    elif freq_mode == "5min":
        target_freq = "5min"
    else:
        ts_valid = pd.to_datetime(df["timestamp"]).dropna().sort_values()
        if len(ts_valid) > 5:
            diffs_min = pd.to_timedelta(ts_valid.diff()).dt.total_seconds() / 60.0  # type: ignore
            has_high_freq = (diffs_min.median() <= 2.5) or (((diffs_min > 0) & (diffs_min <= 2.5)).sum() >= 3)
            target_freq = "1min" if has_high_freq else "5min"
        else:
            target_freq = "5min"

    df["dt_round"] = pd.DatetimeIndex(df["timestamp"]).round(target_freq)

    df_dedup = df.drop_duplicates(subset=["dt_round"]).sort_values("dt_round").copy()
    full_idx = pd.date_range(
        start=df_dedup["dt_round"].min(),
        end=df_dedup["dt_round"].max(),
        freq=target_freq,
    )
    df_grid = pd.DataFrame({"dt_round": full_idx})
    merged = pd.merge(df_grid, df_dedup, on="dt_round", how="left")

    if merged["dist_borda_cm"].isna().any():
        merged["dist_borda_cm"] = get_dist_borda_series(
            merged["dt_round"],
            history=sensor_history,
            active_dist_override=active_dist_cm
        )
    return merged


def simulate_gap(
    start_val: float,
    end_val: float,
    steps: int,
    dt_min: float = 5.0,
    r_in_cm_min: float = DEFAULT_R_GND / 60.0,
    r_pump_cm_min: float = (DEFAULT_VAZAO_BOMBA_M3H * DEFAULT_FATOR_M3H_PARA_CMH) / 60.0,
    d_trigger_on: float = DEFAULT_D_ON,
    d_trigger_off: float = DEFAULT_D_OFF,
    tem_bomba2: bool = False,
    d_trigger_on2: float = DEFAULT_D_ON2,
    d_trigger_off2: float = DEFAULT_D_OFF2,
) -> np.ndarray:
    """
    Simula dinamicamente a oscilação do nível durante um gap longo através do modelo de bombeamento.
    """
    vals = np.zeros(steps)
    vals[0] = start_val
    state1 = 1 if start_val <= d_trigger_on else 0
    state2 = 1 if (tem_bomba2 and start_val <= d_trigger_on2) else 0
    curr = start_val
    for i in range(1, steps - 1):
        if state1 == 0 and curr <= d_trigger_on:
            state1 = 1
        elif state1 == 1 and curr >= d_trigger_off:
            state1 = 0

        if tem_bomba2:
            if state2 == 0 and curr <= d_trigger_on2:
                state2 = 1
            elif state2 == 1 and curr >= d_trigger_off2:
                state2 = 0
        else:
            state2 = 0

        r_pump_active = (state1 + state2) * r_pump_cm_min
        curr += (r_pump_active - r_in_cm_min) * dt_min
        vals[i] = curr
    vals[-1] = end_val
    return vals


def fill_gaps(
    df: pd.DataFrame,
    r_gnd_param: float = DEFAULT_R_GND,
    r_pump_param: float = DEFAULT_VAZAO_BOMBA_M3H * DEFAULT_FATOR_M3H_PARA_CMH,
    d_on: float = DEFAULT_D_ON,
    d_off: float = DEFAULT_D_OFF,
    tem_bomba2: bool = False,
    d_on2: float = DEFAULT_D_ON2,
    d_off2: float = DEFAULT_D_OFF2,
) -> pd.DataFrame:
    """
    Preenche leituras faltantes por interpolação linear (curtos <= 20 min) ou simulação hidrodinâmica (longos).
    """
    result = df.copy()
    result["nivel_imputed"] = result["nivel_cm"].copy()

    if result.empty or "nivel_cm" not in result.columns:
        return result

    dt_step_min = 5.0
    if len(result) > 1 and "dt_round" in result.columns:
        step_sec = (result["dt_round"].iloc[1] - result["dt_round"].iloc[0]).total_seconds()
        if step_sec > 0:
            dt_step_min = step_sec / 60.0

    gap_starts = result[result["nivel_cm"].isna() & ~result["nivel_cm"].shift(1).isna()].index
    for g_start in gap_starts:
        g_ends = result.loc[g_start:]["nivel_cm"].dropna().index
        if len(g_ends) == 0:
            continue

        g_end = g_ends[0]
        gap_len = g_end - g_start + 1
        if g_start == 0:
            continue

        v_start = result.loc[g_start - 1, "nivel_cm"]
        v_end = result.loc[g_end, "nivel_cm"]
        if pd.isna(v_start) or pd.isna(v_end):
            continue

        t_start_gap = result.loc[g_start - 1, "dt_round"]
        t_end_gap = result.loc[g_end, "dt_round"]
        gap_minutes = (t_end_gap - t_start_gap).total_seconds() / 60.0

        if gap_minutes <= 20.0 or gap_len <= 4:
            lin_vals = np.linspace(v_start, v_end, gap_len + 1)[1:-1]
            result.loc[g_start:g_end - 1, "nivel_imputed"] = lin_vals.tolist()
        else:
            sim_dt_min = gap_minutes / gap_len if gap_len > 0 else dt_step_min
            sim_vals = simulate_gap(
                v_start,
                v_end,
                gap_len + 1,
                dt_min=sim_dt_min,
                r_in_cm_min=r_gnd_param / 60.0,
                r_pump_cm_min=r_pump_param / 60.0,
                d_trigger_on=d_on,
                d_trigger_off=d_off,
                tem_bomba2=tem_bomba2,
                d_trigger_on2=d_on2,
                d_trigger_off2=d_off2,
            )
            result.loc[g_start:g_end - 1, "nivel_imputed"] = sim_vals[1:-1].tolist()

    result["altura_agua_imputed_cm"] = (FUNDODOPOCO - result["nivel_imputed"]).clip(lower=0)
    return result


def compute_ultrasonic_temperature_error(df: pd.DataFrame, v_esp32: float = 343.0) -> pd.DataFrame:
    """Calcula o erro de distância do sensor ultrassônico decorrente da variação de temperatura no ar."""
    res = df.copy()
    if "temperatura_c" not in res.columns:
        res["temperatura_c"] = 20.0

    temp_c = res["temperatura_c"].fillna(20.0)
    v_real = 331.3 * np.sqrt(1.0 + temp_c / 273.15)
    res["v_som_m_s"] = v_real

    dist_raw = res["distancia_sensor_cm"] if "distancia_sensor_cm" in res.columns else res["nivel_cm"]
    dist_raw_clean = pd.to_numeric(dist_raw, errors="coerce").fillna(FUNDODOPOCO / 2.0)

    res["distancia_corrigida_cm"] = dist_raw_clean * (v_real / v_esp32)
    res["erro_distancia_cm"] = dist_raw_clean - res["distancia_corrigida_cm"]
    res["erro_relativo_pct"] = (1.0 - (v_real / v_esp32)) * 100.0

    return res
