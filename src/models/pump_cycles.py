"""
Módulo de detecção hidrodinâmica de ciclos de bombeamento, segmentação e volumes expulsos.
"""

from typing import Tuple, Dict, Any, Optional
import numpy as np
import pandas as pd

from src.config import (
    DEFAULT_FATOR_M3H_PARA_CMH,
    DEFAULT_VAZAO_BOMBA_M3H,
)


def detect_pump_cycles_and_volumes(
    df: pd.DataFrame,
    fator_m3h_para_cmh: float = DEFAULT_FATOR_M3H_PARA_CMH,
    max_dur_min: float = 9.0,
    vazao_bomba_m3h: float = DEFAULT_VAZAO_BOMBA_M3H,
    min_dur_min: float = 1.5,
    min_delta_cm: float = 3.0
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Detecta acionamentos reais da bomba por telemetria e por variação física de nível (descidas rápidas),
    calculando o volume exato expulso (Litros/m³) com base no ponto de nível mais alto (água mais alta / menor distância ao topo) 
    logo antes do acionamento e no nível mais baixo (água mais baixa / maior distância ao topo) logo após o fim do acionamento.
    
    Filtros de segurança contra ruído de medição do sensor ultrassônico:
      - Ignora acionamentos com duração > max_dur_min (padrão: 9 min).
      - Ignora acionamentos com duração < min_dur_min (padrão: 1.5 min).
      - Ignora quedas < min_delta_cm (padrão: 3.0 cm).
      - Aplica mediana móvel de 3 pontos ao sinal de nível para suprimir spikes de ruído antes da detecção.
    """
    if df is None or df.empty or "dt_round" not in df.columns:
        return pd.DataFrame(), {}

    col_nivel = "nivel_imputed" if "nivel_imputed" in df.columns else "nivel_cm"
    if col_nivel not in df.columns:
        return pd.DataFrame(), {}

    df_calc = df.sort_values("dt_round").copy()
    vol_por_cm_L = (100.0 / fator_m3h_para_cmh * 10.0) if fator_m3h_para_cmh > 0 else 55.0

    # Pré-filtragem de Ruído do Sensor Ultrassônico via Mediana Móvel de 3 pontos
    df_calc["nivel_smooth"] = df_calc[col_nivel].rolling(window=3, center=True, min_periods=1).median()

    cycles = []
    seen_timestamps = set()

    # Método A: Sinal de Hardware status_bomba (se disponível na telemetria)
    if "status_bomba" in df_calc.columns and df_calc["status_bomba"].notna().any():
        sb = pd.to_numeric(df_calc["status_bomba"], errors="coerce").fillna(0)
        df_calc["bomba_on"] = (sb > 0).astype(int)
        df_calc["bomba_change"] = df_calc["bomba_on"].diff()

        starts = df_calc[df_calc["bomba_change"] == 1].index
        for idx_start in starts:
            ends = df_calc[(df_calc.index > idx_start) & (df_calc["bomba_change"] == -1)].index
            if ends.empty:
                idx_end = df_calc.index[-1]
            else:
                idx_end = ends[0]

            pos_start = df_calc.index.get_loc(idx_start)
            pos_end = df_calc.index.get_loc(idx_end)

            w_start_a = max(0, pos_start - 2)
            w_start_b = min(len(df_calc) - 1, pos_start + 2)
            d_min = df_calc.iloc[w_start_a:w_start_b + 1]["nivel_smooth"].min()
            t_start = df_calc.iloc[pos_start]["dt_round"]

            w_end_a = max(0, pos_end - 2)
            w_end_b = min(len(df_calc) - 1, pos_end + 2)
            d_max = df_calc.iloc[w_end_a:w_end_b + 1]["nivel_smooth"].max()
            t_end = df_calc.iloc[pos_end]["dt_round"]

            delta_cm = d_max - d_min
            dur_min_val = (t_end - t_start).total_seconds() / 60.0

            if delta_cm >= min_delta_cm and min_dur_min <= dur_min_val <= max_dur_min:
                v_expulso_L = delta_cm * vol_por_cm_L
                v_bomba_nom_L = (vazao_bomba_m3h * 1000.0) * (dur_min_val / 60.0)
                fator_rec_L_cm = v_bomba_nom_L / delta_cm if delta_cm > 0 else vol_por_cm_L
                cycles.append({
                    "t_liga": t_start,
                    "t_desliga": t_end,
                    "duracao_min": round(dur_min_val, 1),
                    "d_alto_cm": round(d_min, 1),
                    "d_baixo_cm": round(d_max, 1),
                    "delta_cm": round(delta_cm, 1),
                    "vol_expulso_L": round(v_expulso_L, 1),
                    "vol_bomba_L": round(v_bomba_nom_L, 1),
                    "fator_recalib_L_cm": round(fator_rec_L_cm, 1),
                    "vol_expulso_m3": round(v_expulso_L / 1000.0, 3),
                })
                seen_timestamps.add(t_start)

    # Método B: Detecção Física Híbrida por Queda Rápida do Nível
    vals = df_calc["nivel_smooth"].values
    ts = df_calc["dt_round"].values
    n = len(vals)

    i = 0
    while i < n - 1:
        j = i + 1
        while j < n:
            dur_sec = (ts[j] - ts[i]).astype("timedelta64[s]").astype(float)
            dur_m = dur_sec / 60.0
            if dur_m > max_dur_min:
                break
            delta_d = vals[j] - vals[i]
            if delta_d < -1.0:
                break
            j += 1

        if j > i + 1:
            best_j = i + 1
            max_drop = vals[best_j] - vals[i]
            for k in range(i + 1, min(j, n)):
                drop_k = vals[k] - vals[i]
                if drop_k > max_drop:
                    max_drop = drop_k
                    best_j = k

            dur_m_best = (ts[best_j] - ts[i]).astype("timedelta64[s]").astype(float) / 60.0
            t_st = pd.to_datetime(ts[i])
            t_en = pd.to_datetime(ts[best_j])

            if max_drop >= min_delta_cm and min_dur_min <= dur_m_best <= max_dur_min:
                is_dup = any(abs((t_st - st_t).total_seconds()) < 300 for st_t in seen_timestamps)
                if not is_dup:
                    v_expulso_L = max_drop * vol_por_cm_L
                    v_bomba_nom_L = (vazao_bomba_m3h * 1000.0) * (dur_m_best / 60.0)
                    fator_rec_L_cm = v_bomba_nom_L / max_drop if max_drop > 0 else vol_por_cm_L
                    cycles.append({
                        "t_liga": t_st,
                        "t_desliga": t_en,
                        "duracao_min": round(dur_m_best, 1),
                        "d_alto_cm": round(vals[i], 1),
                        "d_baixo_cm": round(vals[best_j], 1),
                        "delta_cm": round(max_drop, 1),
                        "vol_expulso_L": round(v_expulso_L, 1),
                        "vol_bomba_L": round(v_bomba_nom_L, 1),
                        "fator_recalib_L_cm": round(fator_rec_L_cm, 1),
                        "vol_expulso_m3": round(v_expulso_L / 1000.0, 3),
                    })
                    seen_timestamps.add(t_st)
                i = best_j
                continue
        i += 1

    df_cycles = pd.DataFrame(cycles)
    if not df_cycles.empty:
        df_cycles = df_cycles.sort_values("t_liga").reset_index(drop=True)

    stats: Dict[str, Any] = {}
    if not df_cycles.empty:
        f_rec_mean = float(df_cycles["fator_recalib_L_cm"].mean()) if "fator_recalib_L_cm" in df_cycles.columns else 55.0
        stats = {
            "total_acionamentos": len(df_cycles),
            "volume_total_m3": float(df_cycles["vol_expulso_m3"].sum()),
            "volume_medio_L": float(df_cycles["vol_expulso_L"].mean()),
            "volume_bomba_medio_L": float(df_cycles["vol_bomba_L"].mean()) if "vol_bomba_L" in df_cycles.columns else 0.0,
            "fator_recalib_medio_L_cm": f_rec_mean,
            "fator_recalib_medio_cmh": float(1000.0 / f_rec_mean) if f_rec_mean > 0 else 18.182,
            "area_poco_recalib_m2": float(f_rec_mean / 10.0),
            "volume_max_L": float(df_cycles["vol_expulso_L"].max()),
            "volume_min_L": float(df_cycles["vol_expulso_L"].min()),
            "delta_cm_medio": float(df_cycles["delta_cm"].mean()),
        }

    return df_cycles, stats


def segment_operational_periods(
    df: pd.DataFrame,
    fator_m3h_para_cmh: float = DEFAULT_FATOR_M3H_PARA_CMH,
    vazao_bomba_m3h: float = DEFAULT_VAZAO_BOMBA_M3H,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Segmenta a série temporal em períodos completos contínuos de Enchimento (Subida) e Esvaziamento (Bombeamento)."""
    if df is None or df.empty or "dt_round" not in df.columns:
        return pd.DataFrame(), pd.DataFrame()

    col_nivel = "nivel_imputed" if "nivel_imputed" in df.columns else "nivel_cm"
    if col_nivel not in df.columns:
        return pd.DataFrame(), pd.DataFrame()

    df_calc = df.sort_values("dt_round").copy()

    # 1. Ciclos de bombeamento reais detectados pelo detector híbrido
    df_cycles, _ = detect_pump_cycles_and_volumes(
        df_calc,
        fator_m3h_para_cmh=fator_m3h_para_cmh,
        vazao_bomba_m3h=vazao_bomba_m3h,
    )
    period_esv = []

    if not df_cycles.empty:
        for _, row in df_cycles.iterrows():
            dur_h = max(row["duracao_min"] / 60.0, 0.0167)
            rate = row["delta_cm"] / dur_h
            rate = min(rate, 700.0)
            period_esv.append({
                "t_start": row["t_liga"],
                "t_end": row["t_desliga"],
                "dur_h": dur_h,
                "d_start": row["d_alto_cm"],
                "d_end": row["d_baixo_cm"],
                "total_cm": row["delta_cm"],
                "rate_cmh": rate,
            })

    # 2. Segmentar períodos de subida / enchimento
    df_calc["smooth"] = df_calc[col_nivel].rolling(window=3, center=True, min_periods=1).median()
    df_calc["diff"] = df_calc["smooth"].diff()

    df_calc["direction"] = 0
    df_calc.loc[df_calc["diff"] < -0.05, "direction"] = -1
    df_calc["block"] = (df_calc["direction"] != df_calc["direction"].shift()).cumsum()

    period_ench = []
    for _, group in df_calc.groupby("block"):
        if len(group) < 2 or group["direction"].iloc[0] != -1:
            continue
        t_start = group["dt_round"].iloc[0]
        t_end = group["dt_round"].iloc[-1]
        dur_h = (t_end - t_start).total_seconds() / 3600.0
        if dur_h < 0.05:
            continue

        d_start = group[col_nivel].iloc[0]
        d_end = group[col_nivel].iloc[-1]
        total_cm = d_start - d_end

        if total_cm > 0.3:
            rate = total_cm / dur_h
            period_ench.append({
                "t_start": t_start,
                "t_end": t_end,
                "dur_h": dur_h,
                "d_start": d_start,
                "d_end": d_end,
                "total_cm": total_cm,
                "rate_cmh": rate,
            })

    return pd.DataFrame(period_ench), pd.DataFrame(period_esv)


def compute_rate_statistics(
    df: pd.DataFrame,
    fator_m3h_para_cmh: float = DEFAULT_FATOR_M3H_PARA_CMH,
    vazao_bomba_m3h: float = DEFAULT_VAZAO_BOMBA_M3H,
) -> Optional[Dict[str, Any]]:
    """Calcula estatísticas de taxas operacionais baseadas em períodos completos."""
    df_ench, df_esv = segment_operational_periods(
        df,
        fator_m3h_para_cmh=fator_m3h_para_cmh,
        vazao_bomba_m3h=vazao_bomba_m3h,
    )
    if df_ench.empty and df_esv.empty:
        return None

    return {
        "ench_media": df_ench["rate_cmh"].median() if not df_ench.empty else 0.0,
        "ench_pico": float(df_ench["rate_cmh"].max()) if not df_ench.empty else 0.0,
        "ench_min": float(df_ench["rate_cmh"].min()) if not df_ench.empty else 0.0,
        "ench_count": len(df_ench),
        "esv_media": df_esv["rate_cmh"].median() if not df_esv.empty else 0.0,
        "esv_pico": float(df_esv["rate_cmh"].max()) if not df_esv.empty else 0.0,
        "esv_min": float(df_esv["rate_cmh"].min()) if not df_esv.empty else 0.0,
        "esv_count": len(df_esv),
    }
