"""
Utilitários de fusos horários, formatação de tempo e conversões.
"""

from typing import Any, Union
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

LOCAL_TZ = ZoneInfo("America/Sao_Paulo")


def now_local() -> pd.Timestamp:
    """Retorna o timestamp atual no fuso horário local (America/Sao_Paulo)."""
    return pd.Timestamp.now(tz=LOCAL_TZ)


def normalize_local_ts(ts: Any) -> Union[pd.Timestamp, Any]:
    """Converte e normaliza timestamps para o fuso local (America/Sao_Paulo)."""
    if ts is None or pd.isna(ts):
        return pd.NaT
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        return ts.tz_localize(LOCAL_TZ)
    return ts.tz_convert(LOCAL_TZ)


def to_brasilia_naive(ts_or_series: Any) -> Any:
    """Garante que o timestamp, DatetimeIndex ou Series esteja no fuso de Brasília (America/Sao_Paulo)
    e remove o offset de fuso horário (tz-naive) para que renderizadores como Plotly exibam
    com precisão a hora de Brasília no eixo X e no cursor, sem desvios indesejados para UTC.
    """
    if ts_or_series is None:
        return None
    if isinstance(ts_or_series, pd.Series):
        if ts_or_series.empty:
            return ts_or_series
        s = pd.to_datetime(ts_or_series)
        if s.dt.tz is None:
            return s
        return s.dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)
    elif isinstance(ts_or_series, pd.DatetimeIndex):
        if ts_or_series.empty:
            return ts_or_series
        if ts_or_series.tz is None:
            return ts_or_series
        return ts_or_series.tz_convert(LOCAL_TZ).tz_localize(None)
    elif isinstance(ts_or_series, (list, tuple)):
        return [to_brasilia_naive(item) for item in ts_or_series]
    elif isinstance(ts_or_series, (pd.Timestamp, np.datetime64)):
        if pd.isna(ts_or_series):
            return ts_or_series
        t = pd.Timestamp(ts_or_series)
        if t.tzinfo is None:
            return t
        return t.tz_convert(LOCAL_TZ).tz_localize(None)
    return ts_or_series


def format_hours(hours: float) -> str:
    """Formata horas decimais em formato legível de dias, horas e minutos."""
    if hours == float("inf") or hours < 0 or pd.isna(hours):
        return "N/A"
    tot_min = round(hours * 60)
    h = tot_min // 60
    m = tot_min % 60
    if h >= 48:
        days = h // 24
        rem_h = h % 24
        return f"{days}d {rem_h}h {m:02d}min ({h}h)"
    elif h > 0:
        return f"{h}h {m:02d}min"
    else:
        return f"{m}min"


def format_hours_status(hours: float, current_val: float, target_val: float) -> str:
    """Formata tempo restante ou indica se a marca já foi ultrapassada."""
    if current_val <= target_val:
        return "⚡ Já Atingido"
    if hours == float("inf") or hours < 0 or pd.isna(hours):
        return "N/A"
    tot_min = round(hours * 60)
    h = tot_min // 60
    m = tot_min % 60
    if h >= 48:
        days = h // 24
        rem_h = h % 24
        return f"{days}d {rem_h}h {m:02d}min ({h}h)"
    elif h > 0:
        return f"{h}h {m:02d}min"
    else:
        return f"{m}min"
