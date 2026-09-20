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
