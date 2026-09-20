"""
Módulo de cálculo de tendências de subida recente, tempos de ativação e projeção até transbordo.
"""

from typing import Optional, Dict, Any
import numpy as np
import pandas as pd

from src.config import (
    DEFAULT_D_ON,
    DEFAULT_D_ON2,
    DEFAULT_FACTOR_MM_CM,
)


def compute_recent_rising_trend(
    df: pd.DataFrame,
    dist_borda_cm: float,
    d_on: float = DEFAULT_D_ON,
    tem_bomba2: bool = False,
    d_on2: float = DEFAULT_D_ON2,
    weather_df: Optional[pd.DataFrame] = None,
    factor_mm_cm: float = DEFAULT_FACTOR_MM_CM,
    window_size: int = 20,
) -> Optional[Dict[str, Any]]:
    """
    Analisa a tendência recente das últimas N leituras (padrão: 20 leituras ~1h40min)
    para calcular a velocidade/taxa atual de subida do nível do poço e estimar o tempo
    até ligar bomba 1, ligar bomba 2 (se ativa), atingir sensor e transbordar.
    """
    valid = df.dropna(subset=["dt_round"]).copy()
    if "nivel_cm" in valid.columns:
        valid["val_d"] = valid["nivel_cm"].fillna(valid["nivel_imputed"])
    else:
        valid["val_d"] = valid["nivel_imputed"]

    if len(valid) < 3:
        return None

    segment = valid.iloc[-min(window_size, len(valid)):].copy()

    t0 = segment["dt_round"].iloc[0]
    t_latest = segment["dt_round"].iloc[-1]
    times_h = (segment["dt_round"] - t0).dt.total_seconds() / 3600.0

    if times_h.iloc[-1] <= 0:
        return None

    x_arr = np.asarray(times_h.values, dtype=np.float64)
    y_arr = np.asarray(segment["val_d"].values, dtype=np.float64)
    slope, intercept = np.polyfit(x_arr, y_arr, 1)
    rate_cmh = -slope  # taxa positiva de subida recente em cm/h

    curr_d = segment["val_d"].iloc[-1]

    # Tempos de projeção linear base (sem chuva)
    time_bomba1_h = max(0.0, (curr_d - d_on) / rate_cmh) if (curr_d > d_on and rate_cmh > 0) else (0.0 if curr_d <= d_on else float("inf"))
    time_bomba2_h = max(0.0, (curr_d - d_on2) / rate_cmh) if (tem_bomba2 and curr_d > d_on2 and rate_cmh > 0) else (0.0 if (tem_bomba2 and curr_d <= d_on2) else float("inf"))
    time_sensor_h = max(0.0, curr_d / rate_cmh) if (curr_d > 0 and rate_cmh > 0) else (0.0 if curr_d <= 0 else float("inf"))
    time_overflow_h = max(0.0, (curr_d + dist_borda_cm) / rate_cmh) if (curr_d > -dist_borda_cm and rate_cmh > 0) else (0.0 if curr_d <= -dist_borda_cm else float("inf"))

    # Simulação integrada com previsão ERA5/Open-Meteo se disponível
    time_bomba1_era5_h = None
    time_bomba2_era5_h = None
    time_sensor_era5_h = None
    time_overflow_era5_h = None
    era5_sim_df = None
    has_future_rain = False
    total_rain_era5 = 0.0
    t_bomba1_era5_dt = None
    t_bomba2_era5_dt = None
    t_sensor_era5_dt = None
    t_overflow_era5_dt = None

    if weather_df is not None and not weather_df.empty:
        sim_times = []
        sim_d = []
        c_d = curr_d
        c_t = t_latest

        t_bomba1_era5_dt = c_t if curr_d <= d_on else None
        t_bomba2_era5_dt = c_t if (tem_bomba2 and curr_d <= d_on2) else None
        t_sensor_era5_dt = c_t if curr_d <= 0 else None
        t_overflow_era5_dt = c_t if curr_d <= -dist_borda_cm else None

        w_df_clean = weather_df.copy()
        if isinstance(w_df_clean.index, pd.DatetimeIndex):
            if w_df_clean.index.tz is None:
                w_df_clean.index = w_df_clean.index.tz_localize("America/Sao_Paulo")
            elif str(w_df_clean.index.tz) != "America/Sao_Paulo":
                w_df_clean.index = w_df_clean.index.tz_convert("America/Sao_Paulo")

        max_sim_steps = 12 * 168  # até 7 dias em passos de 5min
        for _ in range(max_sim_steps):
            sim_times.append(c_t)
            sim_d.append(c_d)

            if t_bomba1_era5_dt is None and c_d <= d_on:
                t_bomba1_era5_dt = c_t
            if tem_bomba2 and t_bomba2_era5_dt is None and c_d <= d_on2:
                t_bomba2_era5_dt = c_t
            if t_sensor_era5_dt is None and c_d <= 0:
                t_sensor_era5_dt = c_t
            if t_overflow_era5_dt is None and c_d <= -dist_borda_cm:
                t_overflow_era5_dt = c_t
                break

            hour_floor = c_t.floor("1h")
            precip_mmh = w_df_clean.loc[hour_floor, "precipitation"] if hour_floor in w_df_clean.index else 0.0
            if isinstance(precip_mmh, pd.Series):
                precip_mmh = precip_mmh.iloc[0]

            if precip_mmh > 0:
                has_future_rain = True
                total_rain_era5 += precip_mmh * (5.0 / 60.0)

            r_total = rate_cmh + precip_mmh * factor_mm_cm
            c_d -= r_total * (5.0 / 60.0)
            c_t += pd.Timedelta(minutes=5)

        if t_bomba1_era5_dt is not None:
            time_bomba1_era5_h = (t_bomba1_era5_dt - t_latest).total_seconds() / 3600.0
        if tem_bomba2 and t_bomba2_era5_dt is not None:
            time_bomba2_era5_h = (t_bomba2_era5_dt - t_latest).total_seconds() / 3600.0
        if t_sensor_era5_dt is not None:
            time_sensor_era5_h = (t_sensor_era5_dt - t_latest).total_seconds() / 3600.0
        if t_overflow_era5_dt is not None:
            time_overflow_era5_h = (t_overflow_era5_dt - t_latest).total_seconds() / 3600.0

        era5_sim_df = pd.DataFrame({"dt_round": sim_times, "nivel_imputed": sim_d})

    return {
        "start_time": t0,
        "latest_time": t_latest,
        "duration_h": times_h.iloc[-1],
        "start_d": segment["val_d"].iloc[0],
        "curr_d": curr_d,
        "num_points": len(segment),
        "rate_cmh": rate_cmh,
        "d_on": d_on,
        "tem_bomba2": tem_bomba2,
        "d_on2": d_on2,
        "time_bomba1_h": time_bomba1_h,
        "time_bomba2_h": time_bomba2_h,
        "time_sensor_h": time_sensor_h,
        "time_overflow_h": time_overflow_h,
        "time_bomba1_era5_h": time_bomba1_era5_h,
        "time_bomba2_era5_h": time_bomba2_era5_h,
        "time_sensor_era5_h": time_sensor_era5_h,
        "time_overflow_era5_h": time_overflow_era5_h,
        "has_future_rain": has_future_rain,
        "total_rain_era5": total_rain_era5,
        "era5_sim_df": era5_sim_df,
        "segment_df": segment,
        "slope": slope,
        "intercept": intercept,
    }
