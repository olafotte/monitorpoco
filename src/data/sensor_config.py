"""
Módulo para gerenciamento do histórico de altura do sensor (distância até a borda superior do poço).
Carrega, salva e aplica configurações contidas no arquivo JSON global (sensor_heights.json).
"""

import json
import os
from typing import Dict, Any, List, Optional
import pandas as pd

DEFAULT_JSON_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "sensor_heights.json")

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": "1.0",
    "description": "Histórico de altura do sensor em relação à borda superior do poço",
    "history": [
        {
            "id": 1,
            "start_date": "2026-07-01T00:00:00",
            "end_date": "2026-08-03T00:00:00",
            "dist_borda_cm": 80.0,
            "description": "01/Jul a 03/Ago/2026: Sensor a 80cm da superfície"
        },
        {
            "id": 2,
            "start_date": "2026-08-03T00:00:00",
            "end_date": None,
            "dist_borda_cm": 70.0,
            "description": "Após 03/Ago/2026: Sensor a 70cm da superfície"
        }
    ]
}


def load_sensor_height_config(filepath: str = DEFAULT_JSON_PATH) -> Dict[str, Any]:
    """Carrega o arquivo JSON de configuração da altura do sensor."""
    if not os.path.exists(filepath):
        save_sensor_height_config(DEFAULT_CONFIG, filepath)
        return DEFAULT_CONFIG

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "history" not in data or not isinstance(data["history"], list):
                return DEFAULT_CONFIG
            return data
    except Exception as exc:
        print(f"Erro ao carregar {filepath}: {exc}")
        return DEFAULT_CONFIG


def save_sensor_height_config(config_data: Dict[str, Any], filepath: str = DEFAULT_JSON_PATH) -> bool:
    """Salva as configurações de altura no arquivo JSON."""
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as exc:
        print(f"Erro ao salvar {filepath}: {exc}")
        return False


def get_active_dist_borda(config_data: Dict[str, Any], fallback: float = 70.0) -> float:
    """Retorna a distância da borda do período ativo atual (último registro com end_date null)."""
    history = config_data.get("history", [])
    if not history:
        return abs(fallback)

    # Procura o último registro com end_date é None, ou simplesmente o último da lista
    for item in reversed(history):
        if item.get("end_date") is None:
            return abs(float(item.get("dist_borda_cm", fallback)))

    return abs(float(history[-1].get("dist_borda_cm", fallback)))


def get_dist_borda_series(
    timestamps: pd.Series,
    history: List[Dict[str, Any]],
    active_dist_override: Optional[float] = None
) -> pd.Series:
    """
    Retorna uma pd.Series de dist_borda_cm com base no histórico JSON para cada timestamp.
    Se active_dist_override for fornecido, substitui a distância do período ativo (atual).
    """
    if timestamps.empty:
        return pd.Series(dtype=float)

    # Detectar fuso horário das timestamps recebidas
    ts_converted = pd.to_datetime(timestamps)
    target_tz = getattr(ts_converted.dt, "tz", None)

    default_val = abs(active_dist_override) if active_dist_override is not None else 70.0
    if not history:
        return pd.Series(default_val, index=timestamps.index)

    def parse_dt(dt_val):
        if not dt_val:
            return None
        dt = pd.to_datetime(dt_val)
        if target_tz is not None:
            if dt.tzinfo is None:
                dt = dt.tz_localize(target_tz)
            else:
                dt = dt.tz_convert(target_tz)
        elif dt.tzinfo is not None:
            dt = dt.tz_convert("UTC").tz_localize(None)
        return dt

    # Ordenar o histórico por data de início
    sorted_history = sorted(history, key=lambda x: str(x.get("start_date", "")))
    result = pd.Series(index=timestamps.index, dtype=float)

    # Preencher em ordem cronológica
    for idx, item in enumerate(sorted_history):
        start_str = item.get("start_date")
        end_str = item.get("end_date")
        is_active = (end_str is None) or (idx == len(sorted_history) - 1)

        raw_v = (
            active_dist_override
            if (is_active and active_dist_override is not None)
            else float(item.get("dist_borda_cm", default_val))
        )
        val = abs(float(raw_v))

        min_dt = pd.Timestamp("1970-01-01").tz_localize(target_tz) if target_tz else pd.Timestamp("1970-01-01")
        max_dt = pd.Timestamp("2100-01-01").tz_localize(target_tz) if target_tz else pd.Timestamp("2100-01-01")

        start_dt = parse_dt(start_str) or min_dt
        end_dt = parse_dt(end_str) or max_dt

        # Máscara para timestamps que entram neste intervalo
        mask = (ts_converted >= start_dt) & (ts_converted <= end_dt)
        result[mask] = val

    # Para datas anteriores ao primeiro registro, usar a primeira distância cadastrada
    if sorted_history:
        first_start = parse_dt(sorted_history[0].get("start_date"))
        if first_start:
            first_val = abs(float(sorted_history[0].get("dist_borda_cm", default_val)))
            result[ts_converted < first_start] = first_val

    # Garantir que não existam NaNs
    result = result.fillna(default_val)
    return result
