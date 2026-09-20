"""
Módulo de integração com a API Open-Meteo (Precipitação, Temperatura e Arquivo Histórico).
"""

from typing import Tuple, Union
import numpy as np
import pandas as pd
import requests
import streamlit as st

from src.utils import LOCAL_TZ, now_local


def fetch_weather_data(lat: float, lon: float, start_date: str, end_date: str) -> pd.DataFrame:
    """Busca dados horários de precipitação e temperatura na API Open-Meteo.
    Retorna DataFrame com índice datetime e colunas 'precipitation' (mm/h) e 'temperature_2m' (°C).
    Inclui histórico (archive / past_days) e previsão (forecast) conforme as datas solicitadas."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    today = now_local().normalize()
    start_ts = pd.Timestamp(start_date, tz=LOCAL_TZ)
    end_ts = pd.Timestamp(end_date, tz=LOCAL_TZ)

    days_past = (today - start_ts).days
    days_future = (end_ts - today).days

    frames = []

    # 1. Primeira tentativa: API Forecast com past_days (cobre até 92 dias do passado + previsão em 1 chamada rápida)
    if days_past <= 90:
        url_forecast = "https://api.open-meteo.com/v1/forecast"
        p_past = max(1, min(days_past + 2, 92))
        p_fut = max(1, min(days_future + 2, 16))
        params_forecast = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "precipitation,temperature_2m",
            "timezone": "America/Sao_Paulo",
            "past_days": p_past,
            "forecast_days": p_fut,
            "models": "best_match",
        }
        try:
            r = requests.get(url_forecast, params=params_forecast, headers=headers, timeout=15)
            if r.status_code == 200:
                d = r.json()
                times = pd.to_datetime(d["hourly"]["time"]).tz_localize("America/Sao_Paulo")
                precip = d["hourly"].get("precipitation", [0.0] * len(times))
                temp = d["hourly"].get("temperature_2m", [None] * len(times))
                df_f = pd.DataFrame({"precipitation": precip, "temperature_2m": temp}, index=times)
                df_f = df_f[(df_f.index >= start_ts) & (df_f.index <= end_ts + pd.Timedelta(days=1))]
                if not df_f.empty:
                    frames.append(df_f)
        except Exception:
            pass

    # 2. Segunda tentativa / passado distante (> 90 dias): Archive API
    if not frames or days_past > 90:
        archive_end = min(end_ts, today - pd.Timedelta(days=2))
        if start_ts <= archive_end:
            url_archive = "https://archive-api.open-meteo.com/v1/archive"
            params_archive = {
                "latitude": lat,
                "longitude": lon,
                "start_date": start_ts.strftime("%Y-%m-%d"),
                "end_date": archive_end.strftime("%Y-%m-%d"),
                "hourly": "precipitation,temperature_2m",
                "timezone": "America/Sao_Paulo",
            }
            try:
                r = requests.get(url_archive, params=params_archive, headers=headers, timeout=20)
                if r.status_code == 200:
                    d = r.json()
                    times = pd.to_datetime(d["hourly"]["time"]).tz_localize("America/Sao_Paulo")
                    precip = d["hourly"].get("precipitation", [0.0] * len(times))
                    temp = d["hourly"].get("temperature_2m", [None] * len(times))
                    frames.append(pd.DataFrame({"precipitation": precip, "temperature_2m": temp}, index=times))
            except Exception:
                pass

        # Previsão complementar se buscou do archive
        forecast_start = max(start_ts, today)
        if forecast_start <= end_ts:
            url_forecast = "https://api.open-meteo.com/v1/forecast"
            params_forecast = {
                "latitude": lat,
                "longitude": lon,
                "hourly": "precipitation,temperature_2m",
                "timezone": "America/Sao_Paulo",
                "forecast_days": 8,
            }
            try:
                r = requests.get(url_forecast, params=params_forecast, headers=headers, timeout=15)
                if r.status_code == 200:
                    d = r.json()
                    times = pd.to_datetime(d["hourly"]["time"]).tz_localize("America/Sao_Paulo")
                    precip = d["hourly"].get("precipitation", [0.0] * len(times))
                    temp = d["hourly"].get("temperature_2m", [None] * len(times))
                    frames.append(pd.DataFrame({"precipitation": precip, "temperature_2m": temp}, index=times))
            except Exception:
                pass

    if not frames:
        return pd.DataFrame(columns=["precipitation", "temperature_2m"])

    result = pd.concat(frames).sort_index()
    result = result[~result.index.duplicated(keep="first")]
    if "temperature_2m" in result.columns:
        result["temperature_2m"] = pd.to_numeric(result["temperature_2m"], errors="coerce").ffill().bfill()
    return result


def build_precip_5min(weather_df: pd.DataFrame, grid_index: Union[pd.DatetimeIndex, pd.Series]) -> pd.Series:
    """Distribui precipitação horária nos intervalos da grade do sensor (mm por intervalo)."""
    grid_dt = pd.DatetimeIndex(grid_index)
    if weather_df.empty or len(grid_dt) < 2:
        return pd.Series(0.0, index=grid_dt, name="precipitation_5min")

    step_minutes = (grid_dt[1] - grid_dt[0]).total_seconds() / 60.0
    if step_minutes <= 0:
        step_minutes = 5.0

    hourly = weather_df["precipitation"].copy()
    precip_step = hourly * (step_minutes / 60.0)

    precip_reindexed = (
        precip_step.reindex(precip_step.index.union(grid_dt))
        .ffill()
        .reindex(grid_dt)
        .fillna(0.0)
    )
    precip_reindexed.name = "precipitation_5min"
    return precip_reindexed


def build_temp_grid(weather_df: pd.DataFrame, grid_index: Union[pd.DatetimeIndex, pd.Series]) -> pd.Series:
    """Interpola a temperatura horária para os instantes exatos da grade de medições do sensor."""
    grid_dt = pd.DatetimeIndex(grid_index)
    if weather_df.empty or "temperature_2m" not in weather_df.columns or len(grid_dt) < 2:
        return pd.Series(np.nan, index=grid_dt, name="temperatura_c")

    hourly_temp = weather_df["temperature_2m"].dropna()
    if hourly_temp.empty:
        return pd.Series(np.nan, index=grid_dt, name="temperatura_c")

    temp_reindexed = (
        hourly_temp.reindex(hourly_temp.index.union(grid_dt))
        .interpolate(method="time")
        .reindex(grid_dt)
        .ffill()
        .bfill()
    )
    temp_reindexed.name = "temperatura_c"
    return temp_reindexed


@st.cache_data(ttl="24h", max_entries=10)
def fetch_historical_5years_precip(
    lat: float,
    lon: float,
    start_date_str: str = "",
    end_date_str: str = ""
) -> Tuple[pd.DataFrame, str]:
    """Busca dados de precipitação horária via Open-Meteo Archive API.
    Se start_date_str for fornecido, usa essa data como início; caso contrário usa 5 anos atrás.
    Se end_date_str for fornecido, usa essa data como fim; caso contrário usa ontem.
    Retorna (DataFrame, erro_str) – erro_str é vazio em caso de sucesso."""
    today = now_local().normalize()
    if end_date_str:
        end_date = pd.Timestamp(end_date_str)
    else:
        end_date = today - pd.Timedelta(days=1)

    if start_date_str:
        start_date = pd.Timestamp(start_date_str)
    else:
        start_date = end_date - pd.DateOffset(years=5)

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "hourly": "precipitation",
        "timezone": "UTC",
    }
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    empty = pd.DataFrame(columns=["precipitation"])
    try:
        r = requests.get(url, params=params, headers=headers, timeout=90)
        if r.status_code == 200:
            d = r.json()
            times = (
                pd.to_datetime(d["hourly"]["time"])
                .tz_localize("UTC")
                .tz_convert("America/Sao_Paulo")
            )
            precip = d["hourly"]["precipitation"]
            df_h = pd.DataFrame({"precipitation": precip}, index=times)
            df_h["precipitation"] = pd.to_numeric(df_h["precipitation"], errors="coerce").fillna(0.0)
            return df_h, ""
        else:
            return empty, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as exc:
        return empty, f"{type(exc).__name__}: {exc}"
