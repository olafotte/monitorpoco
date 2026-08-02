import os
import json
import re
import warnings
import datetime as _dt
import textwrap

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
# pyrefly: ignore [missing-import]
from requests.packages.urllib3.exceptions import InsecureRequestWarning
import streamlit as st

from src.config import FUNDODOPOCO, RECOMMENDED_ERA5_FACTOR
from src.data.adolfo_konder import get_adolfo_konder_data as load_adolfo_konder_dataset
from src.models.gumbel import fit_gumbel, gumbel_quantile

st.set_page_config(page_title="Monitoramento de Poço - Condomínio", layout="wide")
st.title("📊 Monitoramento do Poço de Drenagem")
st.markdown(
    "Este aplicativo consulta o banco Turso diretamente, aplica imputação de gaps e traz também um modelo matemático de operação."
)

secrets_turso = st.secrets.get("turso", {})
TURSO_URL = secrets_turso.get("url") or os.environ.get("TURSO_URL")
TURSO_TOKEN = secrets_turso.get("token") or os.environ.get("TURSO_TOKEN")

if not TURSO_URL or not TURSO_TOKEN:
    st.error(
        "Credenciais do Turso não configuradas. Adicione .streamlit/secrets.toml "
        "ou defina TURSO_URL e TURSO_TOKEN como variáveis de ambiente."
    )
    st.stop()

if TURSO_URL.startswith("libsql://"):
    TURSO_URL = TURSO_URL.replace("libsql://", "https://", 1)

headers = {
    "Authorization": f"Bearer {TURSO_TOKEN}",
    "Content-Type": "application/json",
}

st.sidebar.header("⚙️ Configurações do Sistema")

with st.sidebar.expander("📐 Geometria do Poço & Sensor", expanded=True):
    d_on = st.number_input(
        "Nível Ativação Bomba 1 (cm)", value=71.5, step=0.5, help="Distância quando a 1ª bomba liga (água alta)"
    )
    d_off = st.number_input(
        "Nível Desativação Bomba 1 (cm)", value=92.5, step=0.5, help="Distância quando a 1ª bomba desliga (água baixa)"
    )
    dist_borda_cm = st.number_input(
        "Dist. Sensor → Borda Superior (cm)", value=33.0, step=1.0,
        help="Distância vertical do sensor até a borda superior do poço."
    )
    d_overflow = -dist_borda_cm
    capacidade_total_cm = FUNDODOPOCO + dist_borda_cm
    st.caption(f"↳ Transbordo: **−{dist_borda_cm:.0f} cm** | Capacidade total: **{capacidade_total_cm:.0f} cm**")

with st.sidebar.expander("⚡ Automação & Bombas em Paralelo", expanded=True):
    vazao_bomba_m3h = st.number_input(
        "Capacidade 1ª Bomba (m³/h)", value=14.0, step=0.5, help="Vazão nominal da 1ª bomba individual em m³/h"
    )
    fator_m3h_para_cmh = st.number_input(
        "Fator Conversão (cm/h por m³/h)", value=5.471, step=0.01, format="%.3f",
        help="Converte m³/h em cm/h de variação no poço (100 / Área do poço em m²)."
    )
    r_pump_param = vazao_bomba_m3h * fator_m3h_para_cmh
    st.caption(f"↳ Vazão 1ª Bomba: **{r_pump_param:.2f} cm/h**")

    tem_bomba2 = st.checkbox(
        "Instalar 2ª bomba em paralelo", value=False,
        help="Adiciona 2ª bomba ativada por 2ª bóia de emergência quando a 1ª bomba não der conta."
    )
    if tem_bomba2:
        vazao_bomba2_m3h = st.number_input(
            "Capacidade 2ª Bomba (m³/h)", value=float(vazao_bomba_m3h), step=0.5,
            help="Vazão nominal da 2ª bomba em m³/h (padrão: igual à 1ª bomba)"
        )
        d_on2 = st.number_input(
            "Nível Ativação 2ª Bomba (cm)", value=max(round(d_on - 30.0, 1), 0.0), step=0.5,
            help="Distância quando a 2ª bomba LIGA (bóia de emergência 30 cm mais alta que a 1ª)"
        )
        d_off2 = st.number_input(
            "Nível Desativação 2ª Bomba (cm)", value=max(round(d_off - 30.0, 1), 0.0), step=0.5,
            help="Distância quando a 2ª bomba DESLIGA"
        )
        r_pump2_param = vazao_bomba2_m3h * fator_m3h_para_cmh
        vazao_total_m3h = vazao_bomba_m3h + vazao_bomba2_m3h
        r_pump_total_param = r_pump_param + r_pump2_param
        st.caption(f"↳ Sistema Paralelo ({vazao_bomba_m3h:.1f} + {vazao_bomba2_m3h:.1f} m³/h): Total **{vazao_total_m3h:.1f} m³/h** (**{r_pump_total_param:.2f} cm/h**)")
    else:
        d_on2 = d_on - 30.0
        d_off2 = d_off - 30.0
        vazao_total_m3h = vazao_bomba_m3h
        r_pump_total_param = r_pump_param

with st.sidebar.expander("🌦️ Parâmetros Climáticos & ERA5", expanded=True):
    r_gnd_param = st.number_input("Taxa Lençol Freático (cm/h)", value=1.44, step=0.1)
    factor_mm_cm = st.number_input(
        "Fator Amplificação (cm poço / mm chuva)", value=2.83, step=0.1,
        help="Relação calibrada entre mm de chuva e variação de cm no poço"
    )
    default_era5_val = st.session_state.get("era5_corr_val", 2.60)
    era5_correction = st.number_input(
        "Fator de Correção ERA5", min_value=0.5, max_value=5.0, value=default_era5_val, step=0.05, format="%.2f",
        key="era5_corr_input"
    )
    if st.button("🎯 Aplicar Fator Calibrado (×2.60)"):
        st.session_state["era5_corr_val"] = RECOMMENDED_ERA5_FACTOR
        st.rerun()
    st.caption("📊 Estação Ponte Adolfo Konder indica fator recomendado de **×2.60**.")

with st.sidebar.expander("🔌 Conexão Banco Turso DB", expanded=False):
    record_limit = st.number_input(
        "Número de registros a carregar", min_value=1, max_value=100000, value=3000, step=50
    )
if era5_correction != 1.0:
    st.sidebar.caption(f"↳ ERA5 × {era5_correction:.2f} — todos os dados históricos serão escalados")

st.sidebar.header("🌍 Localização Meteorológica")
latitude = st.sidebar.number_input(
    "Latitude", value=-26.9265068, format="%.4f", help="Latitude do local monitorado"
)
longitude = st.sidebar.number_input(
    "Longitude", value=-49.0687619, format="%.4f", help="Longitude do local monitorado"
)

sql_query = (
    "SELECT timestamp, nivel_cm, status_bomba FROM leituras_poco "
    f"ORDER BY id DESC LIMIT {int(record_limit)}"
)

payload_variants = [
    {"stmt": {"sql": sql_query, "args": []}},
    {"statements": [{"stmt": {"sql": sql_query, "args": []}}]},
    {"statements": [{"sql": sql_query, "args": []}]},
    {"statements": [sql_query]},
]

response = None
last_error = None
for idx, payload in enumerate(payload_variants, start=1):
    try:
        resp = requests.post(TURSO_URL, json=payload, headers=headers, timeout=15)
    except Exception as exc:
        last_error = f"request error (variant {idx}): {exc}"
        continue

    if resp.status_code == 200:
        response = resp
        used_payload = payload
        break
    else:
        last_error = f"status {resp.status_code}: {resp.text} (variant {idx})"

if response is None:
    st.error("Erro ao conectar com o banco de dados Turso usando os formatos testados.")
    st.markdown("**Último erro:**")
    st.code(last_error or "Sem resposta")
    st.markdown("**Payloads testados (somente SQL mostrado):**")
    st.json([
        {
            "variant": i + 1,
            "payload_preview": {
                k: (v if k != "stmt" else {"sql": v.get("sql")}) for k, v in p.items()
            },
        }
        for i, p in enumerate(payload_variants)
    ])
    st.stop()

response_data = response.json()

if "results" in response_data and response_data.get("results"):
    result = response_data["results"][0]
    columns = result.get("columns", [])
    rows = result.get("rows", [])
elif (
    "result" in response_data
    and isinstance(response_data.get("result"), dict)
    and "cols" in response_data.get("result")
    and "rows" in response_data.get("result")
):
    columns = [c.get("name") for c in response_data["result"].get("cols", [])]
    rows = response_data["result"].get("rows", [])
elif "cols" in response_data and "rows" in response_data:
    columns = [c.get("name") for c in response_data.get("cols", [])]
    rows = response_data.get("rows", [])
else:
    st.error("A resposta do Turso não contém resultados válidos.")
    st.json(response_data)
    st.stop()

if not columns or not rows:
    st.warning("Não há dados suficientes para exibir. Verifique a tabela ou os registros do Turso.")
    st.stop()

if isinstance(rows[0], list) and rows[0] and isinstance(rows[0][0], dict):
    cleaned_rows = [[val.get("value") for val in row] for row in rows]
elif isinstance(rows[0], dict):
    cleaned_rows = [[row.get(col) for col in columns] for row in rows]
else:
    cleaned_rows = rows

try:
    df = pd.DataFrame(cleaned_rows, columns=columns)
except ValueError as exc:
    st.error(f"Erro ao montar o DataFrame: {exc}")
    st.json(response_data)
    st.stop()

if "timestamp" not in df.columns:
    st.error("A resposta do Turso não contém a coluna 'timestamp'.")
    st.json(df.head(5).to_dict(orient="records"))
    st.stop()

if "nivel_cm" not in df.columns:
    st.error("A resposta do Turso não contém a coluna 'nivel_cm'.")
    st.json(df.head(5).to_dict(orient="records"))
    st.stop()

if "status_bomba" not in df.columns:
    df["status_bomba"] = pd.NA


@st.cache_data
def preprocess_time_series(input_df: pd.DataFrame) -> pd.DataFrame:
    df = input_df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    # Ajustar para o horário de Brasília
    df["timestamp"] = df["timestamp"].dt.tz_localize("UTC").dt.tz_convert("America/Sao_Paulo")
    df["nivel_cm"] = pd.to_numeric(df["nivel_cm"], errors="coerce")
    df["distancia_sensor_cm"] = df["nivel_cm"]
    df["altura_agua_cm"] = (FUNDODOPOCO - df["distancia_sensor_cm"]).clip(lower=0)
    df["dt_round"] = df["timestamp"].dt.round("5min")

    df_dedup = df.drop_duplicates(subset=["dt_round"]).sort_values("dt_round").copy()
    full_idx = pd.date_range(
        start=df_dedup["dt_round"].min(),
        end=df_dedup["dt_round"].max(),
        freq="5min",
    )
    df_grid = pd.DataFrame({"dt_round": full_idx})
    return pd.merge(df_grid, df_dedup, on="dt_round", how="left")


def simulate_gap(
    start_val,
    end_val,
    steps,
    dt_min=5.0,
    r_in_cm_min=1.44 / 60.0,
    r_pump_cm_min=76.6 / 60.0,
    d_trigger_on=73.0,
    d_trigger_off=90.0,
    tem_bomba2=False,
    d_trigger_on2=43.0,
    d_trigger_off2=60.0,
):
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


def fill_gaps(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    result["nivel_imputed"] = result["nivel_cm"].copy()

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

        if gap_len <= 4:
            lin_vals = np.linspace(v_start, v_end, gap_len + 1)[1:-1]
            result.loc[g_start:g_end - 1, "nivel_imputed"] = lin_vals
        else:
            sim_vals = simulate_gap(
                v_start,
                v_end,
                gap_len + 1,
                dt_min=5.0,
                r_in_cm_min=r_gnd_param / 60.0,
                r_pump_cm_min=r_pump_param / 60.0,
                d_trigger_on=d_on,
                d_trigger_off=d_off,
                tem_bomba2=tem_bomba2,
                d_trigger_on2=d_on2,
                d_trigger_off2=d_off2,
            )
            result.loc[g_start:g_end - 1, "nivel_imputed"] = sim_vals[1:-1]

    result["altura_agua_imputed_cm"] = (FUNDODOPOCO - result["nivel_imputed"]).clip(lower=0)
    return result


@st.cache_data(ttl=1800)
def fetch_weather_data(lat: float, lon: float, start_date: str, end_date: str) -> pd.DataFrame:
    """Busca dados horários de precipitação na API Open-Meteo.
    Retorna DataFrame com índice datetime e coluna 'precipitation' (mm/h).
    Inclui histórico (archive) e previsão (forecast) conforme as datas solicitadas."""
    today = pd.Timestamp.now(tz="America/Sao_Paulo").normalize()
    start_ts = pd.Timestamp(start_date, tz="America/Sao_Paulo")
    end_ts = pd.Timestamp(end_date, tz="America/Sao_Paulo")

    frames = []

    # Histórico via open-meteo archive
    archive_end = min(end_ts, today - pd.Timedelta(days=1))
    if start_ts <= archive_end:
        url_archive = "https://archive-api.open-meteo.com/v1/archive"
        params_archive = {
            "latitude": lat,
            "longitude": lon,
            "start_date": start_ts.strftime("%Y-%m-%d"),
            "end_date": archive_end.strftime("%Y-%m-%d"),
            "hourly": "precipitation",
            "timezone": "America/Sao_Paulo",
        }
        try:
            r = requests.get(url_archive, params=params_archive, timeout=15)
            if r.status_code == 200:
                d = r.json()
                times = pd.to_datetime(d["hourly"]["time"]).tz_localize("America/Sao_Paulo")
                precip = d["hourly"]["precipitation"]
                frames.append(pd.DataFrame({"precipitation": precip}, index=times))
        except Exception:
            pass

    # Previsão via open-meteo forecast
    forecast_start = max(start_ts, today)
    if forecast_start <= end_ts:
        url_forecast = "https://api.open-meteo.com/v1/forecast"
        params_forecast = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "precipitation",
            "timezone": "America/Sao_Paulo",
            "forecast_days": 7,
        }
        try:
            r = requests.get(url_forecast, params=params_forecast, timeout=15)
            if r.status_code == 200:
                d = r.json()
                times = pd.to_datetime(d["hourly"]["time"]).tz_localize("America/Sao_Paulo")
                precip = d["hourly"]["precipitation"]
                frames.append(pd.DataFrame({"precipitation": precip}, index=times))
        except Exception:
            pass

    if not frames:
        return pd.DataFrame(columns=["precipitation"])

    result = pd.concat(frames).sort_index()
    result = result[~result.index.duplicated(keep="first")]
    return result


def build_precip_5min(weather_df: pd.DataFrame, grid_index: pd.DatetimeIndex) -> pd.Series:
    """Distribui precipitação horária em intervalos de 5 minutos (mm / 5min).
    Cada hora é dividida igualmente pelos 12 períodos de 5 min que a compõem."""
    if weather_df.empty:
        return pd.Series(0.0, index=grid_index, name="precipitation_5min")

    # Reindex na grade de 5min usando forward-fill para mapear cada hora
    hourly = weather_df["precipitation"].copy()
    # Dividir por 12 para obter mm por 5min
    precip_5min = hourly / 12.0
    # Reindexar para a grade do sensor
    precip_reindexed = precip_5min.reindex(
        precip_5min.index.union(grid_index)
    ).ffill().reindex(grid_index).fillna(0.0)
    precip_reindexed.name = "precipitation_5min"
    return precip_reindexed


@st.cache_data(ttl=86400)  # cache de 24h – dados históricos mudam pouco
def fetch_historical_5years_precip(lat: float, lon: float, start_date_str: str = "", end_date_str: str = "") -> tuple:
    """Busca dados de precipitação horária via Open-Meteo Archive API.
    Se start_date_str for fornecido, usa essa data como início; caso contrário usa 5 anos atrás.
    Se end_date_str for fornecido, usa essa data como fim; caso contrário usa ontem.
    Retorna (DataFrame, erro_str) – erro_str é vazio em caso de sucesso."""
    # Usar datas naïve para a query (evita tz_localize extra)
    today = pd.Timestamp.now().normalize()
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
        "timezone": "UTC",   # UTC evita DST — convertemos para local depois com tz_convert()
    }
    empty = pd.DataFrame(columns=["precipitation"])
    try:
        r = requests.get(url, params=params, timeout=90)
        if r.status_code == 200:
            d = r.json()
            # tz_localize("UTC") nunca tem ambiguidade; tz_convert() também não levanta DST
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








def gumbel_quantile(alpha: float, u: float, return_period: float) -> float:
    """Calcula quantil de Gumbel para um dado período de retorno Tr (anos)."""
    y_t = -np.log(-np.log(1.0 - 1.0 / return_period))
    return u + alpha * y_t


def format_hours(hours: float) -> str:
    """Formata horas decimais em formato legível de dias, horas e minutos."""
    if hours == float("inf") or hours < 0 or pd.isna(hours):
        return "N/A"
    tot_min = int(round(hours * 60))
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
    tot_min = int(round(hours * 60))
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


def segment_operational_periods(df: pd.DataFrame):
    """Segmenta a série temporal em períodos completos contínuos de Enchimento (Subida) e Esvaziamento (Bombeamento).

    Calcula a taxa física de cada período a partir da variação total dividida pelo tempo total decorrido do período:
    Rate = Δd_período / Δt_período (cm/h).
    """
    if df is None or df.empty or "dt_round" not in df.columns:
        return pd.DataFrame(), pd.DataFrame()

    col_nivel = "nivel_imputed" if "nivel_imputed" in df.columns else "nivel_cm"
    if col_nivel not in df.columns:
        return pd.DataFrame(), pd.DataFrame()

    df_calc = df.sort_values("dt_round").copy()
    df_calc["smooth"] = df_calc[col_nivel].ewm(span=3).mean()
    df_calc["diff"] = df_calc["smooth"].diff()

    df_calc["direction"] = 0
    df_calc.loc[df_calc["diff"] < -0.05, "direction"] = -1  # Subida (Enchimento)
    df_calc.loc[df_calc["diff"] > 0.05, "direction"] = 1   # Descida (Esvaziamento)
    df_calc["direction"] = df_calc["direction"].replace(0, np.nan).ffill().fillna(-1)

    df_calc["block"] = (df_calc["direction"] != df_calc["direction"].shift()).cumsum()

    period_ench = []
    period_esv = []

    for _, group in df_calc.groupby("block"):
        if len(group) < 2:
            continue
        t_start = group["dt_round"].iloc[0]
        t_end = group["dt_round"].iloc[-1]
        dur_h = (t_end - t_start).total_seconds() / 3600.0
        if dur_h < 0.08:  # Ignora micro-flutuações menores que ~5 minutos
            continue

        d_start = group[col_nivel].iloc[0]
        d_end = group[col_nivel].iloc[-1]
        dir_val = group["direction"].iloc[0]

        if dir_val == -1:
            total_cm = d_start - d_end
            if total_cm > 0.3:  # Mínimo 0.3 cm de subida total para período válido
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
        elif dir_val == 1:
            total_cm = d_end - d_start
            if total_cm > 0.3:  # Mínimo 0.3 cm de descida total para período válido
                rate = total_cm / dur_h
                period_esv.append({
                    "t_start": t_start,
                    "t_end": t_end,
                    "dur_h": dur_h,
                    "d_start": d_start,
                    "d_end": d_end,
                    "total_cm": total_cm,
                    "rate_cmh": rate,
                })

    return pd.DataFrame(period_ench), pd.DataFrame(period_esv)


def compute_rate_statistics(df: pd.DataFrame) -> dict:
    """Calcula estatísticas de taxas operacionais baseadas em períodos completos (Δd_período / Δt_período)."""
    df_ench, df_esv = segment_operational_periods(df)
    if df_ench.empty and df_esv.empty:
        return None

    res = {
        "ench_media": float(df_ench["rate_cmh"].median()) if not df_ench.empty else 0.0,
        "ench_pico": float(df_ench["rate_cmh"].max()) if not df_ench.empty else 0.0,
        "ench_min": float(df_ench["rate_cmh"].min()) if not df_ench.empty else 0.0,
        "ench_count": len(df_ench),
        "esv_media": float(df_esv["rate_cmh"].median()) if not df_esv.empty else 0.0,
        "esv_pico": float(df_esv["rate_cmh"].max()) if not df_esv.empty else 0.0,
        "esv_min": float(df_esv["rate_cmh"].min()) if not df_esv.empty else 0.0,
        "esv_count": len(df_esv),
    }
    return res


def plot_historical_extremes_chart(df: pd.DataFrame):
    """Gera gráfico interativo Plotly anotando exatamente quando ocorreram as máximas e mínimas históricas (nível e períodos de taxa extrema)."""
    if df is None or df.empty or "dt_round" not in df.columns:
        return None, None, None, None, None

    col_nivel = "nivel_imputed" if "nivel_imputed" in df.columns else "nivel_cm"
    if col_nivel not in df.columns:
        return None, None, None, None, None

    df_calc = df.sort_values("dt_round").copy()
    df_ench, df_esv = segment_operational_periods(df_calc)

    idx_max_water = df_calc[col_nivel].idxmin()  # Menor distância ao sensor = Nível mais alto da água
    idx_min_water = df_calc[col_nivel].idxmax()  # Maior distância ao sensor = Nível mais baixo da água

    row_max_w = df_calc.loc[idx_max_water]
    row_min_w = df_calc.loc[idx_min_water]

    row_peak_rise = df_ench.iloc[df_ench["rate_cmh"].idxmax()] if not df_ench.empty else None
    row_peak_drop = df_esv.iloc[df_esv["rate_cmh"].idxmax()] if not df_esv.empty else None

    fig = go.Figure()

    # Série temporal contínua
    fig.add_trace(
        go.Scatter(
            x=df_calc["dt_round"],
            y=df_calc[col_nivel],
            mode="lines",
            name="Nível do Poço (cm)",
            line=dict(color="#2b5c8f", width=1.8),
        )
    )

    # 1. Água mais alta
    d1 = row_max_w[col_nivel]
    t1 = row_max_w["dt_round"].strftime("%d/%m/%Y às %H:%M")
    fig.add_trace(
        go.Scatter(
            x=[row_max_w["dt_round"]],
            y=[d1],
            mode="markers+text",
            name="Água Mais Alta (Nível Máximo)",
            marker=dict(symbol="star", size=14, color="#d9534f", line=dict(width=1.5, color="black")),
            text=[f"🔴 Nível Máx: {d1:.1f} cm ({t1})"],
            textposition="top center",
        )
    )

    # 2. Água mais baixa
    d2 = row_min_w[col_nivel]
    t2 = row_min_w["dt_round"].strftime("%d/%m/%Y às %H:%M")
    fig.add_trace(
        go.Scatter(
            x=[row_min_w["dt_round"]],
            y=[d2],
            mode="markers+text",
            name="Água Mais Baixa (Nível Mínimo)",
            marker=dict(symbol="diamond", size=12, color="#0275d8", line=dict(width=1.5, color="black")),
            text=[f"🔵 Nível Mín: {d2:.1f} cm ({t2})"],
            textposition="bottom center",
        )
    )

    # 3. Maior taxa de subida por período completo
    if row_peak_rise is not None:
        r3 = row_peak_rise["rate_cmh"]
        t3 = row_peak_rise["t_start"].strftime("%d/%m/%Y às %H:%M")
        fig.add_trace(
            go.Scatter(
                x=[row_peak_rise["t_start"]],
                y=[row_peak_rise["d_start"]],
                mode="markers+text",
                name="Maior Taxa de Subida por Período",
                marker=dict(symbol="triangle-up", size=13, color="#f0ad4e", line=dict(width=1.5, color="black")),
                text=[f"⚡ Pico Subida Período: +{r3:.1f} cm/h ({t3})"],
                textposition="top left",
            )
        )

    # 4. Maior taxa de descida por período completo
    if row_peak_drop is not None:
        r4 = row_peak_drop["rate_cmh"]
        t4 = row_peak_drop["t_start"].strftime("%d/%m/%Y às %H:%M")
        fig.add_trace(
            go.Scatter(
                x=[row_peak_drop["t_start"]],
                y=[row_peak_drop["d_start"]],
                mode="markers+text",
                name="Maior Taxa de Descida por Período",
                marker=dict(symbol="triangle-down", size=13, color="#5cb85c", line=dict(width=1.5, color="black")),
                text=[f"🌊 Pico Descida Período: -{r4:.1f} cm/h ({t4})"],
                textposition="bottom right",
            )
        )

    fig.update_layout(
        title="📈 Ocorrência Temporal dos Extremos de Nível e Períodos Operacionais",
        yaxis=dict(autorange="reversed", title="Distância até o Sensor (cm)"),
        xaxis=dict(title="Data / Hora"),
        legend=dict(orientation="h", y=-0.25, x=0.5, xanchor="center"),
        height=520,
        margin=dict(l=20, r=40, t=50, b=60),
    )

    return fig, row_max_w, row_min_w, row_peak_rise, row_peak_drop


def plot_rate_histograms(df: pd.DataFrame):
    """Gera dois histogramas Plotly com a distribuição das taxas médias calculadas por período completo."""
    df_ench, df_esv = segment_operational_periods(df)

    fig_ench = None
    if not df_ench.empty:
        fig_ench = px.histogram(
            df_ench,
            x="rate_cmh",
            nbins=100,
            title="🟢 Distribuição das Taxas de Subida / Enchimento por Período (cm/h)",
            labels={"rate_cmh": "Taxa Média do Período (cm/h)", "count": "Frequência (Períodos)"},
            color_discrete_sequence=["#2e7d32"],
        )
        fig_ench.update_layout(
            height=360,
            margin=dict(l=20, r=20, t=40, b=40),
            showlegend=False,
            bargap=0.05,
        )

    fig_esv = None
    if not df_esv.empty:
        fig_esv = px.histogram(
            df_esv,
            x="rate_cmh",
            nbins=100,
            title="🔵 Distribuição das Taxas de Descida / Bombeamento por Período (cm/h)",
            labels={"rate_cmh": "Taxa Média do Bombeamento (cm/h)", "count": "Frequência (Períodos)"},
            color_discrete_sequence=["#1565c0"],
        )
        fig_esv.update_layout(
            height=360,
            margin=dict(l=20, r=20, t=40, b=40),
            showlegend=False,
            bargap=0.05,
        )

    return fig_ench, fig_esv


def compute_recent_rising_trend(
    df: pd.DataFrame,
    dist_borda_cm: float,
    d_on: float = 71.5,
    tem_bomba2: bool = False,
    d_on2: float = 41.5,
    weather_df: pd.DataFrame = None,
    factor_mm_cm: float = 2.83,
    window_size: int = 20,
):
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
    valid = valid.dropna(subset=["val_d"]).sort_values("dt_round").reset_index(drop=True)

    if len(valid) < 2:
        return None

    # Usar as últimas N leituras (padrão 20 leituras = ~1h40min)
    n_points = min(window_size, len(valid))
    segment = valid.tail(n_points).copy()

    t0 = segment["dt_round"].iloc[0]
    t_latest = segment["dt_round"].iloc[-1]
    times_h = (segment["dt_round"] - t0).dt.total_seconds() / 3600.0

    if times_h.iloc[-1] <= 0:
        return None

    slope, intercept = np.polyfit(times_h, segment["val_d"], 1)
    rate_cmh = -slope  # taxa positiva de subida recente em cm/h

    curr_d = segment["val_d"].iloc[-1]

    # Tempos de projeção linear base (sem chuva)
    time_bomba1_h = max(0.0, (curr_d - d_on) / rate_cmh) if (curr_d > d_on and rate_cmh > 0) else (0.0 if curr_d <= d_on else float("inf"))
    time_bomba2_h = max(0.0, (curr_d - d_on2) / rate_cmh) if (tem_bomba2 and curr_d > d_on2 and rate_cmh > 0) else (0.0 if (tem_bomba2 and curr_d <= d_on2) else float("inf"))
    time_sensor_h = max(0.0, curr_d / rate_cmh) if (curr_d > 0 and rate_cmh > 0) else (0.0 if curr_d <= 0 else float("inf"))
    time_overflow_h = max(0.0, (curr_d + dist_borda_cm) / rate_cmh) if (curr_d > -dist_borda_cm and rate_cmh > 0) else (0.0 if curr_d <= -dist_borda_cm else float("inf"))

    # ── Simulação com Previsão Meteorológica ERA5 ──
    time_bomba1_era5_h = time_bomba1_h
    time_bomba2_era5_h = time_bomba2_h
    time_sensor_era5_h = time_sensor_h
    time_overflow_era5_h = time_overflow_h
    era5_sim_df = None
    has_future_rain = False
    total_rain_era5 = 0.0

    if weather_df is not None and not weather_df.empty and rate_cmh > 0:
        sim_times = []
        sim_d = []
        c_d = curr_d
        c_t = t_latest

        t_bomba1_era5_dt = c_t if curr_d <= d_on else None
        t_bomba2_era5_dt = c_t if (tem_bomba2 and curr_d <= d_on2) else None
        t_sensor_era5_dt = c_t if curr_d <= 0 else None
        t_overflow_era5_dt = c_t if curr_d <= -dist_borda_cm else None

        w_df_clean = weather_df.copy()
        if w_df_clean.index.tz is None:
            w_df_clean.index = w_df_clean.index.tz_localize("America/Sao_Paulo")
        elif str(w_df_clean.index.tz) != "America/Sao_Paulo":
            w_df_clean.index = w_df_clean.index.tz_convert("America/Sao_Paulo")

        max_sim_steps = 12 * 168  # até 7 dias em passos de 5min
        for step in range(max_sim_steps):
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


processed_df = preprocess_time_series(df)
filled_df = fill_gaps(processed_df)

# Buscar dados meteorológicos alinhados ao período do sensor
_sensor_min = filled_df["dt_round"].min()
_sensor_max = filled_df["dt_round"].max()
_weather_start = (_sensor_min - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
_weather_end = (pd.Timestamp.now(tz="America/Sao_Paulo") + pd.Timedelta(days=7)).strftime("%Y-%m-%d")

weather_df = fetch_weather_data(latitude, longitude, _weather_start, _weather_end)

# Aplicar fator de correção ERA5 à precipitação (cobre tabs de Chuvas e Previsão do Nível)
if not weather_df.empty and era5_correction != 1.0:
    weather_df = weather_df.copy()
    weather_df["precipitation"] = weather_df["precipitation"] * era5_correction

# Precipitação em 5min alinhada ao grid do sensor
precip_5min_series = build_precip_5min(weather_df, filled_df["dt_round"])
filled_df["precipitation_5min"] = precip_5min_series.values

latest_valid = filled_df[filled_df["nivel_cm"].notna()]
if not latest_valid.empty:
    latest_row = latest_valid.iloc[-1]
else:
    latest_row = filled_df.iloc[-1]

latest_distance = (
    float(latest_row["distancia_sensor_cm"])
    if not pd.isna(latest_row["distancia_sensor_cm"])
    else None
)

col1, col2 = st.columns(2)
with col1:
    if latest_distance is not None:
        st.metric("Distância atual do sensor à linha d'água", f"{latest_distance:.0f} cm")
    else:
        st.metric("Distância atual do sensor à linha d'água", "Sem leitura válida")
with col2:
    if "status_bomba" in latest_row and not pd.isna(latest_row["status_bomba"]):
        st.metric(label="Status da Bomba", value=str(latest_row["status_bomba"]))
    else:
        st.info("Coluna de status da bomba não encontrada na resposta.")

st.subheader("Diagnóstico do poço")
if latest_distance is None:
    st.warning("Sem leitura válida da distância do sensor à linha d'água.")
elif latest_distance >= FUNDODOPOCO:
    st.warning(f"⚠️ Poço seco: a distância do sensor à linha d'água é de {latest_distance/100:.1f} m ou mais.")
else:
    altura_agua_cm = max(FUNDODOPOCO - latest_distance, 0.0)
    dist_ao_transbordo = latest_distance + dist_borda_cm
    # Alertas progressivos baseados na distância real à borda
    if dist_ao_transbordo <= 20:
        st.error(
            f"🚨 **ALERTA:** poço a apenas **{dist_ao_transbordo:.0f} cm** da borda! "
            f"(sensor: {latest_distance:.0f} cm | borda: +{dist_borda_cm:.0f} cm)"
        )
    elif dist_ao_transbordo <= 50:
        st.warning(
            f"⚠️ Atenção: poço a **{dist_ao_transbordo:.0f} cm** da borda. "
            f"(sensor: {latest_distance:.0f} cm | borda: +{dist_borda_cm:.0f} cm)"
        )
    else:
        st.success(
            f"Nível estimado da água: **{altura_agua_cm:.0f} cm** acima do fundo | "
            f"📦 Folga até a borda: **{dist_ao_transbordo:.0f} cm** "
            f"({latest_distance:.0f} cm sensor + {dist_borda_cm:.0f} cm borda)"
        )

col1, col2, col3, col4 = st.columns(4)
total_obs = len(filled_df)
missing_obs = filled_df["nivel_cm"].isna().sum()
missing_pct = (missing_obs / total_obs) * 100
col1.metric("Total de Leituras Esperadas", f"{total_obs:,}")
col2.metric("Leituras Validadas", f"{total_obs - missing_obs:,}")
col3.metric("Dados Preenchidos (Gaps)", f"{missing_obs:,} ({missing_pct:.1f}%)")
col4.metric("Faixa de Operação", f"{d_on:.1f} cm - {d_off:.1f} cm")


@st.cache_data(ttl=3600)  # cache de 1h
def fetch_defesa_civil_rankings() -> dict:
    """Retorna rankings de maiores chuvas da Defesa Civil de Blumenau.

    Estratégia híbrida:
    - LOCAL com bs4 instalado: faz scraping em tempo real, atualiza dc_rankings_cache.json.
    - STREAMLIT CLOUD (sem bs4): lê dc_rankings_cache.json do repositório.
    Retorna dict {duração: DataFrame} com colunas [Estação, Região, Data/Hora, Acumulado_mm].
    """
    # ── Localizar o arquivo de cache junto ao próprio script ──
    _cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dc_rankings_cache.json")

    def _json_to_dict(payload: dict) -> dict:
        """Converte o JSON salvo de volta para dict {dur: DataFrame}."""
        out = {}
        for dur, rows in payload.get("dados", {}).items():
            df = pd.DataFrame(rows)
            if "Acumulado_mm" in df.columns:
                df["Acumulado_mm"] = pd.to_numeric(df["Acumulado_mm"], errors="coerce")
            out[dur] = df
        return out

    def _load_json_cache() -> dict:
        if os.path.exists(_cache_path):
            try:
                with open(_cache_path, encoding="utf-8") as f:
                    return _json_to_dict(json.load(f))
            except Exception:
                pass
        return {}

    # ── Tentar scraping (só funciona se bs4 estiver instalado) ──
    try:
        # pyrefly: ignore [missing-import]
        from bs4 import BeautifulSoup  # import local: não quebra cloud se ausente
    except ImportError:
        # Estamos no cloud sem bs4: usar cache JSON
        return _load_json_cache()

    url = "https://defesacivil.blumenau.sc.gov.br/d/maiores-chuvas"
    headers_req = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    resultado = {}
    try:
        warnings.filterwarnings("ignore", category=InsecureRequestWarning)
        resp = requests.get(url, headers=headers_req, timeout=20, verify=False)
        if resp.status_code != 200:
            return _load_json_cache()
        soup = BeautifulSoup(resp.text, "html.parser")

        page_headers = soup.find_all("div", class_="page-header")
        for ph in page_headers:
            h3 = ph.find("h3")
            if not h3:
                continue
            titulo_raw = h3.get_text(separator=" ", strip=True)
            match = re.search(r"em\s+([\d]+h|[\d]+min)", titulo_raw, re.IGNORECASE)
            duracao = match.group(1) if match else titulo_raw

            small = h3.find("small")
            ultima_atualizacao = (
                small.get_text(strip=True).replace("\u00daltima atualiza\u00e7\u00e3o:", "").strip()
                if small else ""
            )

            table = ph.find_next_sibling("table")
            if not table:
                continue
            rows_data = []
            for tr in table.find("tbody").find_all("tr"):
                cells = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(cells) == 4:
                    try:
                        acumulado_float = float(cells[3].replace(",", "."))
                    except ValueError:
                        acumulado_float = None
                    rows_data.append({
                        "Esta\u00e7\u00e3o": cells[0],
                        "Regi\u00e3o": cells[1],
                        "Data/Hora": cells[2],
                        "Acumulado_mm": acumulado_float,
                        "ultima_atualizacao": ultima_atualizacao,
                    })
            if rows_data:
                df_dur = pd.DataFrame(rows_data)
                resultado[duracao] = df_dur

        # ── Atualizar o cache JSON (para distribuição no cloud) ──
        if resultado:
            import datetime
            cache_payload = {
                "gerado_em": datetime.datetime.now().isoformat(),
                "fonte": url,
                "dados": {
                    dur: df.to_dict(orient="records")
                    for dur, df in resultado.items()
                },
            }
            try:
                with open(_cache_path, "w", encoding="utf-8") as f:
                    json.dump(cache_payload, f, ensure_ascii=False, indent=2)
            except Exception:
                pass  # cloud pode ser read-only; ignora silenciosamente

    except Exception:
        return _load_json_cache()  # rede falhou: usa cache

    return resultado

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs([
    "📊 Visualização e Imputação",
    "📐 Modelo Matemático",
    "🌧️ Simulador Pluviométrico",
    "🌦️ Chuvas",
    "🔮 Previsão do Nível",
    "📊 Histórico & IDF",
    "🛑 Defesa Civil Blumenau",
    "🗂️ Diagrama do Poço",
    "📋 Relatório de Adequação",
])

with tab1:
    st.subheader("Série Temporal do Nível do Poço")
    st.caption(
        "O eixo Y esquerdo (invertido) mostra a distância do sensor à linha d'água. "
        "O eixo Y direito mostra a precipitação acumulada em cada intervalo de 5 minutos."
    )

    trend = compute_recent_rising_trend(
        filled_df,
        dist_borda_cm,
        d_on=d_on,
        tem_bomba2=tem_bomba2,
        d_on2=d_on2,
        weather_df=weather_df,
        factor_mm_cm=factor_mm_cm,
        window_size=20,
    )

    has_rain_data = "precipitation_5min" in filled_df.columns and filled_df["precipitation_5min"].sum() > 0

    fig = go.Figure()

    # Barras de precipitação no eixo secundário (renderizadas primeiro para ficarem atrás)
    if has_rain_data:
        fig.add_trace(
            go.Bar(
                x=filled_df["dt_round"],
                y=filled_df["precipitation_5min"],
                name="Chuva (mm / 5min)",
                marker_color="rgba(30, 144, 255, 0.35)",
                yaxis="y2",
            )
        )

    fig.add_trace(
        go.Scatter(
            x=filled_df["dt_round"],
            y=filled_df["nivel_cm"],
            mode="markers",
            name="Dados Reais (Sensor)",
            marker=dict(size=4, color="#1f77b4"),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=filled_df["dt_round"],
            y=filled_df["nivel_imputed"],
            mode="lines",
            name="Modelo Estimado (Sem Gaps)",
            line=dict(color="#ff7f0e", width=1.5),
        )
    )

    if trend and trend["rate_cmh"] > 0:
        t_latest = trend["latest_time"]
        t_sensor_dt = t_latest + pd.Timedelta(hours=trend["time_sensor_h"])
        t_overflow_dt = t_latest + pd.Timedelta(hours=trend["time_overflow_h"])

        # Projeção Linear Base (Sem Chuva)
        fig.add_trace(
            go.Scatter(
                x=[t_latest, t_sensor_dt, t_overflow_dt],
                y=[trend["curr_d"], 0, d_overflow],
                mode="lines+markers",
                name=f"Projeção Base ({trend['rate_cmh']:.2f} cm/h)",
                line=dict(color="#777777", width=2, dash="dash"),
                marker=dict(size=5, symbol="circle"),
            )
        )

        # Projeção Corrigida com Previsão ERA5 (Com Chuva Futura)
        if trend["era5_sim_df"] is not None and not trend["era5_sim_df"].empty:
            fig.add_trace(
                go.Scatter(
                    x=trend["era5_sim_df"]["dt_round"],
                    y=trend["era5_sim_df"]["nivel_imputed"],
                    mode="lines",
                    name="Projeção Corrigida (ERA5 + Chuva)",
                    line=dict(color="#d9534f", width=2.5, dash="dot"),
                )
            )
    b1_on_label = "Bomba 1 Liga (Água Alta)" if tem_bomba2 else "Bomba Liga (Água Alta)"
    b1_off_label = "Bomba 1 Desliga (Água Baixa)" if tem_bomba2 else "Bomba Desliga (Água Baixa)"
    fig.add_hline(y=d_on, line_dash="dash", line_color="red", annotation_text=b1_on_label)
    fig.add_hline(y=d_off, line_dash="dash", line_color="green", annotation_text=b1_off_label)
    if tem_bomba2:
        fig.add_hline(y=d_on2, line_dash="dash", line_color="#f0ad4e", annotation_text="Bomba 2 Liga (Emergência)")
        fig.add_hline(y=d_off2, line_dash="dash", line_color="#5cb85c", annotation_text="Bomba 2 Desliga")
    fig.add_hline(y=d_overflow, line_dash="dot", line_color="black", annotation_text="Nível Crítico / Transbordo")
    fig.update_layout(
        yaxis=dict(
            autorange="reversed",
            title="Distância até o Sensor (cm)",
        ),
        yaxis2=dict(
            title="Precipitação (mm / 5min)",
            overlaying="y",
            side="right",
            showgrid=False,
            rangemode="tozero",
            tickfont=dict(color="rgba(30, 144, 255, 0.8)"),
            title_font=dict(color="rgba(30, 144, 255, 0.8)"),
        ),
        xaxis=dict(title="Data / Hora"),
        legend=dict(orientation="h", y=-0.2, x=0.5, xanchor="center"),
        height=520,
        margin=dict(l=20, r=60, t=30, b=40),
        bargap=0,
    )
    st.plotly_chart(fig, use_container_width=True)

    if not has_rain_data:
        st.info("ℹ️ Dados de precipitação não disponíveis para o período exibido (API Open-Meteo).")

    st.subheader("Últimos Registros")
    df_export = filled_df.copy()
    num_cols = df_export.select_dtypes(include=[np.number]).columns
    df_export[num_cols] = df_export[num_cols].round(2)
    st.dataframe(df_export)
    st.download_button(
        label="📥 Baixar Registros em CSV",
        data=df_export.to_csv(index=False, float_format="%.2f").encode("utf-8"),
        file_name="registros_poco.csv",
        mime="text/csv",
    )

    if trend and trend["rate_cmh"] > 0:
        t_b1_str = format_hours_status(trend["time_bomba1_h"], trend["curr_d"], d_on)
        t_b1_era5_str = format_hours_status(trend["time_bomba1_era5_h"], trend["curr_d"], d_on)

        t_b2_str = format_hours_status(trend["time_bomba2_h"], trend["curr_d"], d_on2) if tem_bomba2 else "N/A"
        t_b2_era5_str = format_hours_status(trend["time_bomba2_era5_h"], trend["curr_d"], d_on2) if tem_bomba2 else "N/A"

        t_sens_str = format_hours_status(trend["time_sensor_h"], trend["curr_d"], 0.0)
        t_sens_era5_str = format_hours_status(trend["time_sensor_era5_h"], trend["curr_d"], 0.0)

        t_ovf_str = format_hours_status(trend["time_overflow_h"], trend["curr_d"], d_overflow)
        t_ovf_era5_str = format_hours_status(trend["time_overflow_era5_h"], trend["curr_d"], d_overflow)

        t_start_fmt = trend["start_time"].strftime("%d/%m/%Y às %H:%M")
        t_dur_fmt = format_hours(trend["duration_h"])

        st.markdown("---")
        with st.container(border=True):
            st.subheader("🧪 Estimativa de Subida Real (Tendência Recente de 20 Leituras + ERA5)")

            rain_msg = (
                f" 🌦️ **Chuva Futura ERA5:** +{trend['total_rain_era5']:.1f} mm previstos no período."
                if trend["has_future_rain"]
                else " ☀️ **Sem chuva futura prevista.**"
            )
            st.markdown(
                f"Análise da velocidade atual baseada nas **últimas {trend['num_points']} leituras** ({t_dur_fmt} | desde **{t_start_fmt}**). "
                f"Variação na janela: **{trend['start_d']:.1f} cm** → **{trend['curr_d']:.1f} cm** |{rain_msg}"
            )

            num_cols = 5 if tem_bomba2 else 4
            cols = st.columns(num_cols)

            cols[0].metric(
                label="📈 Taxa Recente (Últimas 20)",
                value=f"{trend['rate_cmh']:.2f} cm/h",
            )

            cols[1].metric(
                label=f"🚨 Ligar Bomba 1 ({d_on:.1f} cm)",
                value=t_b1_era5_str,
                delta=f"Base: {t_b1_str}" if t_b1_era5_str != t_b1_str else None,
                delta_color="off",
            )

            curr_idx = 2
            if tem_bomba2:
                cols[curr_idx].metric(
                    label=f"⚡ Ligar Bomba 2 ({d_on2:.1f} cm)",
                    value=t_b2_era5_str,
                    delta=f"Base: {t_b2_str}" if t_b2_era5_str != t_b2_str else None,
                    delta_color="off",
                )
                curr_idx += 1

            cols[curr_idx].metric(
                label="🎯 Atingir Sensor (0 cm)",
                value=t_sens_era5_str,
                delta=f"Base: {t_sens_str}" if t_sens_era5_str != t_sens_str else None,
                delta_color="off",
            )
            curr_idx += 1

            cols[curr_idx].metric(
                label=f"🌊 Transbordar ({d_overflow:.0f} cm)",
                value=t_ovf_era5_str,
                delta=f"Base: {t_ovf_str}" if t_ovf_era5_str != t_ovf_str else None,
                delta_color="inverse",
            )

    rate_stats = compute_rate_statistics(filled_df)
    if rate_stats:
        with st.container(border=True):
            st.subheader("📊 Análise Histórica das Taxas Operacionais (Enchimento vs. Esvaziamento)")
            st.caption(
                "Estatísticas calculadas a partir de todas as taxas válidas registradas na série temporal. "
                "Separado em **Enchimento** (subida do nível com bomba desligada) e **Esvaziamento** (descida do nível com bombeamento ativo)."
            )

            c_ench, c_esv = st.columns(2)

            with c_ench:
                st.markdown("#### 🟢 Regime de Enchimento (Subida de Nível)")
                st.caption(f"Amostras: **{rate_stats['ench_count']}** períodos de subida")
                m1, m2, m3 = st.columns(3)
                m1.metric("Taxa Mediana", f"{rate_stats['ench_media']:.2f} cm/h")
                m2.metric("Pico (Máximo)", f"{rate_stats['ench_pico']:.2f} cm/h")
                m3.metric("Taxa Mínima", f"{rate_stats['ench_min']:.2f} cm/h")

            with c_esv:
                st.markdown("#### 🔵 Regime de Esvaziamento (Descida por Bombeamento)")
                st.caption(f"Amostras: **{rate_stats['esv_count']}** períodos de descida")
                m4, m5, m6 = st.columns(3)
                m4.metric("Taxa Mediana", f"{rate_stats['esv_media']:.2f} cm/h")
                m5.metric("Pico (Máximo)", f"{rate_stats['esv_pico']:.2f} cm/h")
                m6.metric("Taxa Mínima", f"{rate_stats['esv_min']:.2f} cm/h")

    fig_ext, r_max_w, r_min_w, r_pk_rise, r_pk_drop = plot_historical_extremes_chart(filled_df)
    if fig_ext:
        with st.container(border=True):
            st.subheader("📅 Ocorrência Temporal das Máximas e Mínimas Históricas")
            st.caption(
                "Gráfico interativo destacando os exatos momentos da série temporal em que o poço atingiu "
                "o seu nível máximo de água, nível mínimo, maior taxa de subida (enchimento) e maior taxa de descida (bombeamento)."
            )
            st.plotly_chart(fig_ext, use_container_width=True)

            ext_cols = st.columns(4)
            if r_max_w is not None:
                d1 = r_max_w["nivel_imputed"] if "nivel_imputed" in r_max_w else r_max_w["nivel_cm"]
                t1 = r_max_w["dt_round"].strftime("%d/%m/%Y às %H:%M")
                ext_cols[0].metric(
                    label="🔴 Água Mais Alta (Nível Máx)",
                    value=f"{d1:.1f} cm",
                    delta=f"Ocorrido em: {t1}",
                    delta_color="off",
                )

            if r_min_w is not None:
                d2 = r_min_w["nivel_imputed"] if "nivel_imputed" in r_min_w else r_min_w["nivel_cm"]
                t2 = r_min_w["dt_round"].strftime("%d/%m/%Y às %H:%M")
                ext_cols[1].metric(
                    label="🔵 Água Mais Baixa (Nível Mín)",
                    value=f"{d2:.1f} cm",
                    delta=f"Ocorrido em: {t2}",
                    delta_color="off",
                )

            if r_pk_rise is not None:
                r3 = r_pk_rise["rate_cmh"]
                t3 = r_pk_rise["t_start"].strftime("%d/%m/%Y às %H:%M")
                ext_cols[2].metric(
                    label="⚡ Pico de Subida (Enchimento)",
                    value=f"+{r3:.2f} cm/h",
                    delta=f"Ocorrido em: {t3}",
                    delta_color="off",
                )

            if r_pk_drop is not None:
                r4 = r_pk_drop["rate_cmh"]
                t4 = r_pk_drop["t_start"].strftime("%d/%m/%Y às %H:%M")
                ext_cols[3].metric(
                    label="🌊 Pico de Descida (Esvaziamento)",
                    value=f"-{r4:.2f} cm/h",
                    delta=f"Ocorrido em: {t4}",
                    delta_color="off",
                )

    fig_h_ench, fig_h_esv = plot_rate_histograms(filled_df)
    if fig_h_ench or fig_h_esv:
        with st.container(border=True):
            st.subheader("📊 Frequência e Distribuição das Taxas (Histogramas Operacionais)")
            st.caption(
                "Histogramas de frequência das velocidades operacionais (cm/h). "
                "Permite identificar a concentração das taxas típicas de subida da água e a capacidade de rebaixamento das bombas."
            )
            h_col1, h_col2 = st.columns(2)
            with h_col1:
                if fig_h_ench:
                    st.plotly_chart(fig_h_ench, use_container_width=True)
                else:
                    st.info("Sem dados suficientes de subida para gerar o histograma.")

            with h_col2:
                if fig_h_esv:
                    st.plotly_chart(fig_h_esv, use_container_width=True)
                else:
                    st.info("Sem dados suficientes de esvaziamento para gerar o histograma.")

with tab2:
    st.header("Formulação do Modelo Matemático")
    st.markdown("O comportamento hidrodinâmico do poço de drenagem é regido pela **Equação de Balanço de Massa / Continuidade**:")
    st.latex(r"\frac{dV}{dt} = A \cdot \frac{dh}{dt} = Q_{\text{entrada}}(t) - Q_{\text{saída}}(t)")
    st.markdown(r"Como o sensor mede a distância $d(t)$ no topo em relação à lâmina d'água ($h(t) = H_{\text{sensor}} - d(t)$), temos $\frac{dh}{dt} = -\frac{dd}{dt}$." )
    st.markdown("Portanto, a taxa instantânea de variação da distância $d(t)$ é dada por:")
    st.latex(r"\frac{dd(t)}{dt} = \frac{Q_{\text{saída}}(t) - Q_{\text{entrada}}(t)}{A}")
    st.subheader("Regimes Operacionais Recalibrados")
    st.markdown("**1️⃣ Entrada Lenta (Lençol Freático – Estado $S_0$)**")
    st.markdown("- $Q_{\text{saída}} = 0$ (Bomba desligada)")
    st.latex(r"\frac{dd}{dt} = -r_{\text{freático}} \approx -1.44\text{ cm/h}")
    st.markdown("**2️⃣ Entrada por Chuva ($S_1$)**")
    st.markdown("- Relacionada com a precipitação em mm pela taxa de amplificação do pátio:")
    st.latex(r"\text{Taxa de Entrada (cm/h)} = \frac{\text{Chuva (mm)}}{\text{Horas}} \times 2.83")
    st.markdown("**3️⃣ Esvaziamento Forçado (Bomba Ativa – Estado $S_2$)**")
    st.latex(r"Q_{\text{saída}} = 14.000\text{ L/h} (14\text{ m}^3/h) \Rightarrow r_{\text{bomba}} \approx 76.6\text{ cm/h}")

    st.markdown("---")
    st.subheader("📐 Estimativa e Calibração do Fator de Conversão (m³/h para cm/h)")
    st.markdown(
        """
        ### 1. Relação Teórica da Geometria do Poço
        Para um poço cilíndrico ideal de diâmetro interno $D = 1,20\text{ m}$, a área da seção transversal ($A$) é dada por:
        """
    )
    st.latex(r"A = \pi \cdot \left(\frac{D}{2}\right)^2 = \pi \cdot (0,60)^2 \approx 1,131\text{ m}^2")
    st.markdown(
        """
        Teoricamente, a conversão entre uma vazão volumétrica $Q\text{ (m}^3/\text{h)}$ e a variação da coluna d'água no poço $r\text{ (cm/h)}$ seria:
        """
    )
    st.latex(r"F_{\text{teórico}} = \frac{100\text{ cm/m}}{A\text{ m}^2} = \frac{100}{1,131} \approx 88,42\text{ (cm/h por m}^3/\text{h)}")

    st.markdown(
        """
        ### 2. O Efeito Real das Galerias Conectadas (*Pipe Storage* & Remanso)
        Na instalação real, o poço não opera de forma isolada. Ele está conectado a tubulações e galerias de drenagem dispostas com inclinação suave.
        Quando a bomba desliga ou a chuva entra, ocorre o **efeito de remanso**: a água preenche também o volume contido na rede de tubulações acopladas a montante.
        
        Isso expande a **área equivalente de armazenamento** ($A_{\text{efetiva}}$):
        """
    )
    st.latex(r"A_{\text{efetiva}} = \frac{100}{F_{\text{calibrado}}} = \frac{100}{5,471} \approx 18,28\text{ m}^2")
    st.markdown(
        """
        Ou seja, o reservatório físico se comporta hidrostaticamente como se tivesse uma área de superfície de **18,28 m²** (amortecimento muito maior que o poço isolado de 1,20 m).
        """
    )

    st.markdown(
        """
        ### 3. Estimativa Prática pelos Dados do Sensor Ultrassônico
        O modelo estima e valida empiricamente o fator de conversão $F$ analisando os ciclos de acionamento da bomba capturados pelo sensor ultrassônico durante períodos sem chuva (quando apenas o lençol freático está ativo):

        1. **Fase de Esvaziamento (Bomba LIGADA):** O sensor mede a velocidade líquida de rebaixamento da água $(\\frac{dd}{dt})_{\\text{desce}} \\approx -75,16\\text{ cm/h}$.
        2. **Fase de Enchimento (Bomba DESLIGADA):** O sensor mede a velocidade de subida por afluxo do lençol freático $(\\frac{dd}{dt})_{\\text{subida}} \\approx +1,44\\text{ cm/h}$.
        3. **Vazão Bruta da Bomba ($r_{\\text{bomba}}$):** A soma em módulo das duas velocidades elimina a interferência do lençol freático e fornece a velocidade bruta da bomba registrada no sensor:
        """
    )
    st.latex(r"r_{\text{bomba}} = \left|\left(\frac{dd}{dt}\right)_{\text{desce}}\right| + \left(\frac{dd}{dt}\right)_{\text{subida}} = 75,16 + 1,44 = 76,6\text{ cm/h}")
    st.markdown(
        """
        4. **Cálculo do Fator Calibrado ($F$):** Dividindo essa taxa de variação observada pelo sensor em $\\text{cm/h}$ pela vazão nominal hidráulica da bomba $Q_{\\text{bomba}} = 14,0\\text{ m}^3/\\text{h}$:
        """
    )
    st.latex(r"F_{\text{calibrado}} = \frac{r_{\text{bomba}}\text{ (cm/h)}}{Q_{\text{bomba}}\text{ (m}^3/\text{h)}} = \frac{76,6\text{ cm/h}}{14,0\text{ m}^3/\text{h}} = 5,471\text{ (cm/h por m}^3/\text{h)}")

    st.markdown("---")
    st.subheader("📈 Comparativo no Momento LIGA → DESLIGA: Esvaziamento Teórico vs. 5+ Séries Reais (Sensor)")
    st.caption("Alinhamento do instante exato de atracamento da bomba (quando a água atinge d_on, t = 0 min) para comparar a rampa de esvaziamento teórica com múltiplos eventos reais gravados pelo sensor ultrassônico.")

    # 1. Rampa Teórica de Esvaziamento (Bomba Ligada)
    r_liq_teorico = 76.6 - r_gnd_param
    t_teorico_min = (d_off - d_on) / r_liq_teorico * 60.0
    t_teor_arr = np.linspace(0, max(t_teorico_min * 1.4, 35.0), 100)
    d_teor_arr = [min(d_on + (r_liq_teorico / 60.0) * t, d_off) for t in t_teor_arr]

    fig_comp = go.Figure()
    fig_comp.add_trace(go.Scatter(
        x=t_teor_arr, y=d_teor_arr, mode="lines",
        name=f"Esvaziamento Teórico (-{r_liq_teorico:.1f} cm/h)",
        line=dict(color="#1f77b4", width=3.5)
    ))

    # 2. Localizar e alinhar o início EXATO das rampas de esvaziamento (t = 0 onde d atinge d_on e começa a subir)
    real_cycles = []
    palette_cycles = ["#e67e22", "#2ecc71", "#9b59b6", "#e74c3c", "#f1c40f", "#1abc9c", "#e84393", "#00cec9"]

    if "filled_df" in locals() or "filled_df" in globals():
        try:
            if not filled_df.empty:
                s_lev = filled_df["nivel_cm"].fillna(filled_df["nivel_imputed"])
                df_w = filled_df.copy()
                df_w["val"] = s_lev

                # Identificar pontos onde o nível está alto (val <= d_on + 6.0) E a distância aumenta no curto prazo
                diff_15m = df_w["val"].shift(-3) - df_w["val"]
                triggers = df_w[(df_w["val"] <= (d_on + 6.0)) & (diff_15m >= 2.0)].index

                if len(triggers) == 0:
                    diff_5m = df_w["val"].shift(-1) - df_w["val"]
                    triggers = df_w[(df_w["val"] <= (d_on + 10.0)) & (diff_5m >= 1.0)].index

                for idx in reversed(triggers):
                    loc = df_w.index.get_loc(idx)
                    t_curr = df_w["dt_round"].iloc[loc]

                    # Evitar duplicatas no mesmo ciclo (separação mínima de 20 min)
                    if any(abs((t_curr - c["start_dt"]).total_seconds()) < 1200 for c in real_cycles):
                        continue

                    # Extrair até 24 pontos (2 horas) a partir do início da rampa de desbarrancamento
                    sub = df_w.iloc[loc:min(loc+24, len(df_w))]
                    if len(sub) < 2:
                        continue

                    # Truncar no pico de esvaziamento (quando atinge d_off / distância máxima)
                    max_dist_loc = sub["val"].idxmax()
                    end_loc = sub.index.get_loc(max_dist_loc)
                    sub_cut = sub.iloc[0:end_loc+1]

                    if len(sub_cut) >= 2 and (sub_cut["val"].iloc[-1] - sub_cut["val"].iloc[0]) >= 3.0:
                        sub_copy = sub_cut.copy()
                        t0_ev = sub_copy["dt_round"].iloc[0]
                        sub_copy["t_min"] = (sub_copy["dt_round"] - t0_ev).dt.total_seconds() / 60.0
                        dt_h_ev = (sub_copy["dt_round"].iloc[-1] - t0_ev).total_seconds() / 3600.0
                        real_rate_cmh = (sub_copy["val"].iloc[-1] - sub_copy["val"].iloc[0]) / dt_h_ev if dt_h_ev > 0 else 0.0

                        real_cycles.append({
                            "start_dt": t0_ev,
                            "sub_df": sub_copy,
                            "rate_cmh": real_rate_cmh,
                            "duration_min": sub_copy["t_min"].iloc[-1],
                        })
                        if len(real_cycles) >= 6:
                            break
        except Exception:
            pass

    if real_cycles:
        for idx_c, c in enumerate(real_cycles, 1):
            clr = palette_cycles[(idx_c - 1) % len(palette_cycles)]
            t_label = c["start_dt"].strftime("%d/%m %H:%M")
            fig_comp.add_trace(go.Scatter(
                x=c["sub_df"]["t_min"], y=c["sub_df"]["val"],
                mode="markers+lines",
                name=f"Ciclo Real {idx_c} ({t_label}) — {c['rate_cmh']:.1f} cm/h",
                line=dict(color=clr, width=2, dash="dot"),
                marker=dict(size=6, color=clr)
            ))

        avg_real_rate = np.mean([c["rate_cmh"] for c in real_cycles])
        avg_real_dur = np.mean([c["duration_min"] for c in real_cycles])

        mc1, mc2, mc3 = st.columns(3)
        mc1.metric("Taxa Esvaziamento Teórica", f"{r_liq_teorico:.1f} cm/h", help="14 m³/h menos lençol freático")
        mc2.metric(f"Média Real ({len(real_cycles)} Ciclos Medidos)", f"{avg_real_rate:.1f} cm/h", delta=f"{avg_real_rate - r_liq_teorico:+.1f} cm/h")
        mc3.metric("Tempo Médio de Rebaixamento", f"{avg_real_dur:.0f} min (Real)", delta=f"Teórico: {t_teorico_min:.1f} min")

        with st.expander(f"📋 Ver Detalhes dos {len(real_cycles)} Ciclos Reais Medidos pelo Sensor"):
            det_rows = []
            for i, c in enumerate(real_cycles, 1):
                det_rows.append({
                    "Ciclo #": i,
                    "Data / Hora Ativação (t=0)": c["start_dt"].strftime("%d/%m/%Y %H:%M"),
                    "Taxa Rebaixamento (cm/h)": f"{c['rate_cmh']:.1f} cm/h",
                    "Tempo até Desligar (min)": f"{c['duration_min']:.0f} min",
                    "Diferença vs. Teórico": f"{c['rate_cmh'] - r_liq_teorico:+.1f} cm/h",
                })
            st.dataframe(pd.DataFrame(det_rows).set_index("Ciclo #"), use_container_width=True)

    fig_comp.add_hline(y=d_on, line_dash="dash", line_color="red", annotation_text=f"Bóia LIGA ({d_on:.1f} cm)", annotation_position="top right")
    fig_comp.add_hline(y=d_off, line_dash="dash", line_color="green", annotation_text=f"Bóia DESLIGA ({d_off:.1f} cm)", annotation_position="top right")

    fig_comp.update_layout(
        title="Rampa de Esvaziamento do Poço: Instante da Ativação da Bomba (t = 0 min, Múltiplos Eventos)",
        xaxis_title="Tempo Decorrido desde a Ativação da Bomba (Minutos)",
        yaxis=dict(autorange="reversed", title="Distância do Sensor à Água (cm)"),
        height=480,
        margin=dict(l=20, r=20, t=40, b=40)
    )
    st.plotly_chart(fig_comp, use_container_width=True)

    if not real_cycles:
        st.info("ℹ️ Exibindo rampa teórica de esvaziamento. Conecte ao banco Turso DB para sobrepor dados em tempo real dos eventos de acionamento.")

with tab3:
    st.subheader("🔮 Simulador de Eventos Pluviométricos e Saturação da Bomba")
    st.write("Insira a estimativa de volume de chuva (mm) e a duração prevista (horas) para avaliar se a bomba suportará o volume ou se haverá transbordamento.")

    col_input1, col_input2, col_input3 = st.columns(3)
    with col_input1:
        chuva_mm = st.number_input("Precipitação Prevista (mm)", min_value=1.0, max_value=300.0, value=60.0, step=5.0)
    with col_input2:
        chuva_horas = st.number_input("Duração da Chuva (Horas)", min_value=0.5, max_value=48.0, value=2.0, step=0.5)
    with col_input3:
        bomba_status = st.selectbox("Status da Automação da Bomba", ["Operacional (Ligada)", "Falha / Sem Energia (Desligada)"])

    intensidade_mm_h = chuva_mm / chuva_horas
    taxa_entrada_cm_h = intensidade_mm_h * factor_mm_cm
    intensidade_saturacao_mm_h = r_pump_param / factor_mm_cm

    intensidade_saturacao_mm_h = r_pump_total_param / factor_mm_cm

    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("Intensidade da Chuva", f"{intensidade_mm_h:.1f} mm/h")
    col_m2.metric("Entrada Equivalente no Poço", f"{taxa_entrada_cm_h:.1f} cm/h")
    col_m3.metric(
        "Capacidade Instalada",
        f"{r_pump_total_param:.1f} cm/h",
        help=f"1ª Bomba: {r_pump_param:.1f} cm/h" + (f" | 2ª Bomba: {r_pump_param:.1f} cm/h (Total {vazao_total_m3h:.1f} m³/h)" if tem_bomba2 else "")
    )
    col_m4.metric("Limite de Saturação da Bomba", f"{intensidade_saturacao_mm_h:.1f} mm/h")

    st.markdown("---")

    sim_time_steps = np.linspace(0, max(chuva_horas * 1.5, 6.0), 360)
    dt_h = sim_time_steps[1] - sim_time_steps[0]

    levels = []
    curr_d = d_off
    p1_state = False
    p2_state = False
    overflow_occurred = False
    overflow_time = None

    for t in sim_time_steps:
        if t <= chuva_horas:
            r_in = taxa_entrada_cm_h
        else:
            r_in = 0.0

        if bomba_status == "Falha / Sem Energia (Desligada)":
            curr_d -= r_in * dt_h
        else:
            if not p1_state and curr_d <= d_on:
                p1_state = True
            elif p1_state and curr_d >= d_off:
                p1_state = False

            if tem_bomba2:
                if not p2_state and curr_d <= d_on2:
                    p2_state = True
                elif p2_state and curr_d >= d_off2:
                    p2_state = False
            else:
                p2_state = False

            r_active = (int(p1_state) + int(p2_state)) * r_pump_param
            curr_d += (r_active - r_in) * dt_h

        levels.append(curr_d)
        if not overflow_occurred and curr_d <= d_overflow:
            overflow_occurred = True
            overflow_time = t

    fig_sim = go.Figure()
    fig_sim.add_trace(go.Scatter(x=sim_time_steps, y=levels, mode="lines", name="Nível no poço (cm)", line=dict(color="#1f77b4", width=2.5)))
    fig_sim.add_hline(y=d_on, line_dash="dash", line_color="red", annotation_text="Bóia 1 LIGA", annotation_position="top right")
    fig_sim.add_hline(y=d_off, line_dash="dash", line_color="green", annotation_text="Bóia 1 DESLIGA", annotation_position="top right")
    if tem_bomba2:
        fig_sim.add_hline(y=d_on2, line_dash="dash", line_color="orange", annotation_text="Bóia 2 LIGA (Emergência)", annotation_position="top right")
        fig_sim.add_hline(y=d_off2, line_dash="dash", line_color="teal", annotation_text="Bóia 2 DESLIGA", annotation_position="top right")
    fig_sim.update_layout(
        title=f"Simulação Dinâmica para Chuva de {chuva_mm}mm em {chuva_horas}h ({intensidade_mm_h:.1f} mm/h)",
        xaxis_title="Tempo Decorrido (Horas)",
        yaxis=dict(autorange="reversed", title="Distância até o Sensor (cm)"),
        height=450,
    )
    st.plotly_chart(fig_sim, use_container_width=True)

    st.subheader("📢 Análise de Capacidade e Risco")
    if intensidade_mm_h > intensidade_saturacao_mm_h and bomba_status == "Operacional (Ligada)":
        st.warning(
            f"⚠️ **SATURAÇÃO DA BOMBA DETECTADA:** A intensidade da chuva ({intensidade_mm_h:.1f} mm/h) supera a capacidade máxima de esvaziamento da bomba ({intensidade_saturacao_mm_h:.1f} mm/h). Mesmo com a bomba ligada 100% do tempo, o poço acumulará água."
        )
    elif bomba_status == "Operacional (Ligada)":
        st.success(
            f"✅ **BOMBA DÁ CONTA DA CHUVA:** A intensidade ({intensidade_mm_h:.1f} mm/h) é inferior ao limite de saturação ({intensidade_saturacao_mm_h:.1f} mm/h). A bomba conseguirá ciclar e esvaziar o poço."
        )

    if overflow_occurred:
        st.error(
            f"🚨 **ALERTA DE TRANSBORDAMENTO:** O poço atingirá a borda superior "
            f"(sensor = −{dist_borda_cm:.0f} cm, água {dist_borda_cm:.0f} cm acima do sensor) "
            f"em **{overflow_time*60:.0f} minutos** ({overflow_time:.2f} horas) após o início da chuva!"
        )
    else:
        st.info("ℹ️ Nenhum transbordamento previsto para este cenário específico.")

    st.markdown("---")
    st.subheader("⏮️ Simulação e Emulação de Eventos Históricos")
    st.caption(
        "Selecione qualquer data do passado para carregar os dados pluviométricos e "
        "simular a reação exata do poço e do sistema de bombeamento durante o evento. "
        "Você pode escolher entre medições reais da estação Ponte Adolfo Konder ou modelos ERA5."
    )

    fonte_dados = st.radio(
        "Selecione a Fonte de Dados para Simulação:",
        options=[
            "🌧️ Estação Real: Ponte Adolfo Konder (Medições Pluviométricas Reais - Blumenau)",
            "📡 Modelo ERA5 Corregido (Open-Meteo Archive × Fator de Correção)",
            "📡 Modelo ERA5 Bruto (Open-Meteo Archive sem Fator)",
        ],
        index=0,
        horizontal=True,
    )

    col_h1, col_h2, col_h3, col_h4 = st.columns(4)
    with col_h1:
        data_evento = st.date_input(
            "Data de Início do Evento",
            value=_dt.date(2025, 2, 12),
            min_value=_dt.date(2015, 1, 1),
            max_value=_dt.date.today(),
            help="Selecione a data inicial para reconstrução do evento histórico."
        )
    with col_h2:
        duracao_evento_h = st.selectbox(
            "Janela de Simulação (Horas)",
            options=[24, 48, 72, 96],
            index=1,
            help="Período contínuo a ser simulado a partir da data de início."
        )
    with col_h3:
        nivel_inicial_sim = st.number_input(
            "Nível Inicial (cm do sensor)",
            value=float(d_off),
            step=1.0,
            help="Distância do sensor à água no início do evento (ex: d_off = poço esvaziado)."
        )
    with col_h4:
        status_bomba_hist = st.selectbox(
            "Automação do Sistema",
            ["Operacional (Ligada)", "Falha / Sem Energia (Desligada)"],
            key="status_bomba_hist"
        )

    start_hist_str = data_evento.strftime("%Y-%m-%d")
    end_hist_dt = data_evento + _dt.timedelta(hours=int(duracao_evento_h))
    end_hist_str = end_hist_dt.strftime("%Y-%m-%d")

    df_ev = pd.DataFrame()
    lbl_fonte = ""
    usando_konder_real = False

    start_ts_ev = pd.Timestamp(data_evento, tz="America/Sao_Paulo")
    end_ts_ev = start_ts_ev + pd.Timedelta(hours=int(duracao_evento_h))

    if "Ponte Adolfo Konder" in fonte_dados:
        df_konder = load_adolfo_konder_dataset()
        if not df_konder.empty:
            sub_k = df_konder[(df_konder.index >= start_ts_ev) & (df_konder.index <= end_ts_ev)].copy()
            if not sub_k.empty:
                df_ev = sub_k
                lbl_fonte = "Estação Real Ponte Adolfo Konder (Blumenau)"
                usando_konder_real = True
                st.info(f"✅ **Usando dados reais de pluviômetro da Estação Ponte Adolfo Konder:** {len(sub_k)} leituras de 15 minutos registradas no período.")
            else:
                st.warning(
                    f"⚠️ A data **{start_hist_str}** está fora da cobertura da Estação Adolfo Konder "
                    f"({df_konder.index.min().strftime('%d/%m/%Y')} a {df_konder.index.max().strftime('%d/%m/%Y')}). "
                    f"Alternando automaticamente para o Modelo ERA5 Corrigido."
                )

    if df_ev.empty:
        with st.spinner(f"Buscando chuva histórica ERA5 para {start_hist_str} a {end_hist_str}…"):
            hist_ev_df, hist_err = fetch_historical_5years_precip(
                latitude, longitude, start_date_str=start_hist_str, end_date_str=end_hist_str
            )

        if hist_ev_df.empty:
            st.warning(f"Não foi possível obter dados para este período. Erro: {hist_err}")
        else:
            df_ev = hist_ev_df[(hist_ev_df.index >= start_ts_ev) & (hist_ev_df.index <= end_ts_ev)].copy()
            if "Corregido" in fonte_dados or "Ponte Adolfo Konder" in fonte_dados:
                if era5_correction != 1.0:
                    df_ev["precipitation"] = df_ev["precipitation"] * era5_correction
                lbl_fonte = f"Modelo ERA5 (Open-Meteo ×{era5_correction:.2f})"
            else:
                lbl_fonte = "Modelo ERA5 Bruto (Open-Meteo sem correção)"

    if not df_ev.empty:
        sim_dt_min = 5.0
        n_steps_ev = int((duracao_evento_h * 60) / sim_dt_min)
        sim_times_ev = [start_ts_ev + pd.Timedelta(minutes=i * sim_dt_min) for i in range(n_steps_ev + 1)]

        sim_levels_ev = []
        sim_pump_active_ev = []
        sim_rain_rate_ev = []

        curr_d_ev = float(nivel_inicial_sim)
        p1_ev = curr_d_ev <= d_on
        p2_ev = tem_bomba2 and (curr_d_ev <= d_on2)

        ovf_ev = False
        ovf_ev_time = None

        dt_h_ev = sim_dt_min / 60.0

        for i, t_step in enumerate(sim_times_ev):
            candidates = df_ev.index[df_ev.index <= t_step]
            if len(candidates) > 0:
                if usando_konder_real and "precip_mmh" in df_ev.columns:
                    precip_ev_mmh = float(df_ev.loc[candidates[-1], "precip_mmh"])
                else:
                    precip_ev_mmh = float(df_ev.loc[candidates[-1], "precipitation"])
            else:
                precip_ev_mmh = 0.0

            r_chuva_cm_h = precip_ev_mmh * factor_mm_cm
            r_total_in = r_gnd_param + r_chuva_cm_h

            sim_levels_ev.append(curr_d_ev)
            sim_pump_active_ev.append(int(p1_ev) + int(p2_ev))
            sim_rain_rate_ev.append(r_chuva_cm_h)

            if not ovf_ev and curr_d_ev <= d_overflow:
                ovf_ev = True
                ovf_ev_time = t_step

            if status_bomba_hist == "Falha / Sem Energia (Desligada)":
                curr_d_ev -= r_total_in * dt_h_ev
            else:
                if not p1_ev and curr_d_ev <= d_on:
                    p1_ev = True
                elif p1_ev and curr_d_ev >= d_off:
                    p1_ev = False

                if tem_bomba2:
                    if not p2_ev and curr_d_ev <= d_on2:
                        p2_ev = True
                    elif p2_ev and curr_d_ev >= d_off2:
                        p2_ev = False
                else:
                    p2_ev = False

                r_active_ev = (int(p1_ev) + int(p2_ev)) * r_pump_param
                curr_d_ev += (r_active_ev - r_total_in) * dt_h_ev

        if usando_konder_real and "precip_15min" in df_ev.columns:
            precip_total_ev = df_ev["precip_15min"].sum()
            pico_mmh_ev = df_ev["precip_mmh"].max()
            pico_15m_val = df_ev["precip_15min"].max()
        else:
            precip_total_ev = df_ev["precipitation"].sum()
            pico_mmh_ev = df_ev["precipitation"].max()
            pico_15m_val = None

        min_dist_ev = min(sim_levels_ev)
        folga_min_ev = min_dist_ev + dist_borda_cm

        st.markdown("---")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Precipitação Acumulada", f"{precip_total_ev:.1f} mm", help=f"Fonte: {lbl_fonte}")
        m2.metric(
            "Pico de Chuva (Taxa Horária)",
            f"{pico_mmh_ev:.1f} mm/h",
            help=f"Maior impulso de chuva. " + (f"Pico de 15min: {pico_15m_val:.1f} mm." if pico_15m_val else "") + f" Entrada no poço: {pico_mmh_ev * factor_mm_cm:.1f} cm/h"
        )
        m3.metric("Nível Mais Alto Atingido", f"{min_dist_ev:.1f} cm do sensor", help="0 cm = face do sensor")
        if ovf_ev:
            m4.metric("Status do Evento", "❌ TRANSBORDO", delta="Borda Superada!", delta_color="inverse")
        else:
            m4.metric("Status do Evento", "✅ SEM TRANSBORDO", delta=f"Folga mín: {folga_min_ev:.0f} cm")

        fig_hist = go.Figure()

        if usando_konder_real and "precip_15min" in df_ev.columns:
            fig_hist.add_trace(
                go.Bar(
                    x=df_ev.index,
                    y=df_ev["precip_15min"] * 4.0,
                    name="Chuva Inst. (Ponte Adolfo Konder - mm/h eq.)",
                    marker_color="rgba(46, 204, 113, 0.4)",
                    yaxis="y2",
                )
            )
        else:
            fig_hist.add_trace(
                go.Bar(
                    x=df_ev.index,
                    y=df_ev["precipitation"],
                    name=f"Chuva ({lbl_fonte})",
                    marker_color="rgba(30, 144, 255, 0.35)",
                    yaxis="y2",
                )
            )

        fig_hist.add_trace(
            go.Scatter(
                x=sim_times_ev,
                y=sim_levels_ev,
                mode="lines",
                name="Nível Simulado do Poço",
                line=dict(color="#1f77b4", width=2.5),
            )
        )

        fig_hist.add_hline(y=d_on, line_dash="dash", line_color="red", annotation_text="Bóia 1 LIGA", annotation_position="top right")
        fig_hist.add_hline(y=d_off, line_dash="dash", line_color="green", annotation_text="Bóia 1 DESLIGA", annotation_position="top right")
        if tem_bomba2:
            fig_hist.add_hline(y=d_on2, line_dash="dash", line_color="orange", annotation_text="Bóia 2 LIGA (Emergência)", annotation_position="top right")
            fig_hist.add_hline(y=d_off2, line_dash="dash", line_color="teal", annotation_text="Bóia 2 DESLIGA", annotation_position="top right")

        fig_hist.add_hline(y=d_overflow, line_dash="dot", line_color="darkred", annotation_text="Borda (Transbordo)", annotation_position="top right")
        fig_hist.add_hrect(y0=0, y1=d_overflow, fillcolor="rgba(231,76,60,0.10)", line_width=0)

        fig_hist.update_layout(
            title=f"Reconstrução do Evento ({lbl_fonte}): {start_hist_str} ({duracao_evento_h}h) | Chuva Total: {precip_total_ev:.1f} mm | Pico: {pico_mmh_ev:.1f} mm/h",
            xaxis=dict(title="Data / Hora"),
            yaxis=dict(autorange="reversed", title="Distância Sensor → Água (cm)"),
            yaxis2=dict(
                title="Intensidade de Chuva (mm/h)",
                overlaying="y", side="right", showgrid=False, rangemode="tozero",
                tickfont=dict(color="rgba(30, 144, 255, 0.7)"),
                title_font=dict(color="rgba(30, 144, 255, 0.7)"),
            ),
            legend=dict(orientation="h", y=-0.22, x=0.5, xanchor="center"),
            height=520,
            margin=dict(l=20, r=70, t=50, b=50),
        )
        st.plotly_chart(fig_hist, use_container_width=True)

        if ovf_ev:
            t_ovf_str = ovf_ev_time.strftime("%d/%m/%Y às %H:%M")
            st.error(
                f"🚨 **ALERTA DE TRANSBORDO REGISTRADO NA SIMULAÇÃO:**\n\n"
                f"Na data de **{t_ovf_str}**, o nível da água ultrapassou a borda superior do poço "
                f"(superou a folga de {dist_borda_cm:.0f} cm acima do sensor).\n"
                f"O pico de chuva de **{pico_mmh_ev:.1f} mm/h** ({pico_mmh_ev * factor_mm_cm:.1f} cm/h no poço) "
                f"superou a capacidade instalada de remoção das bombas ({r_pump_total_param:.1f} cm/h)."
            )
        else:
            st.success(
                f"✅ **SISTEMA SUPORTOU O EVENTO HISTÓRICO ({lbl_fonte}):**\n\n"
                f"Durante todo o período de {duracao_evento_h}h a partir de {start_hist_str}, "
                f"o nível máximo atingido ficou a **{min_dist_ev:.1f} cm** do sensor "
                f"(margem restante de **{folga_min_ev:.0f} cm** até a borda superior)."
            )

with tab4:
    st.subheader("🌦️ Dados de Precipitação – Open-Meteo")
    st.caption(f"Localização: lat {latitude:.4f}, lon {longitude:.4f} | Fonte: Open-Meteo (histórico + previsão 7 dias)")

    if weather_df.empty:
        st.error("Não foi possível obter dados de precipitação da API Open-Meteo. Verifique a conexão ou as coordenadas.")
    else:
        now_tz = pd.Timestamp.now(tz="America/Sao_Paulo")

        # Separar histórico e previsão
        hist_df = weather_df[weather_df.index <= now_tz].copy()
        fcast_df = weather_df[weather_df.index > now_tz].copy()

        # Métricas principais
        last_24h = weather_df[weather_df.index >= (now_tz - pd.Timedelta(hours=24))]
        next_24h = weather_df[
            (weather_df.index > now_tz) & (weather_df.index <= (now_tz + pd.Timedelta(hours=24)))
        ]
        next_48h = weather_df[
            (weather_df.index > now_tz) & (weather_df.index <= (now_tz + pd.Timedelta(hours=48)))
        ]
        max_intensity = weather_df["precipitation"].max()

        col_w1, col_w2, col_w3, col_w4 = st.columns(4)
        col_w1.metric("Acumulado últimas 24h", f"{last_24h['precipitation'].sum():.1f} mm")
        col_w2.metric("Previsão próximas 24h", f"{next_24h['precipitation'].sum():.1f} mm")
        col_w3.metric("Previsão próximas 48h", f"{next_48h['precipitation'].sum():.1f} mm")
        col_w4.metric("Intensidade máxima (período)", f"{max_intensity:.1f} mm/h")

        st.markdown("---")

        # Gráfico de precipitação horária completo
        st.subheader("Precipitação Horária – Histórico e Previsão")
        fig_rain = go.Figure()

        if not hist_df.empty:
            fig_rain.add_trace(
                go.Bar(
                    x=hist_df.index,
                    y=hist_df["precipitation"],
                    name="Histórico (mm/h)",
                    marker_color="rgba(30, 100, 220, 0.7)",
                )
            )

        if not fcast_df.empty:
            fig_rain.add_trace(
                go.Bar(
                    x=fcast_df.index,
                    y=fcast_df["precipitation"],
                    name="Previsão (mm/h)",
                    marker_color="rgba(100, 200, 100, 0.7)",
                )
            )

        _now_str = now_tz.strftime("%Y-%m-%d %H:%M:%S")
        fig_rain.add_shape(
            type="line",
            x0=_now_str,
            x1=_now_str,
            y0=0,
            y1=1,
            yref="paper",
            line=dict(dash="dash", color="orange", width=1.5),
        )
        fig_rain.add_annotation(
            x=_now_str,
            y=1,
            yref="paper",
            text="Agora",
            showarrow=False,
            xanchor="left",
            font=dict(color="orange"),
        )
        fig_rain.update_layout(
            xaxis_title="Data / Hora",
            yaxis_title="Precipitação (mm/h)",
            legend=dict(orientation="h", y=-0.2, x=0.5, xanchor="center"),
            height=420,
            bargap=0,
            margin=dict(l=20, r=20, t=20, b=40),
        )
        st.plotly_chart(fig_rain, use_container_width=True)

        # Previsão detalhada próximas 48h
        st.subheader("📋 Previsão Detalhada – Próximas 48 Horas")
        if not next_48h.empty:
            fcast_table = next_48h.copy()
            fcast_table.index = fcast_table.index.strftime("%d/%m/%Y %H:%M")
            fcast_table.columns = ["Precipitação (mm/h)"]
            fcast_table = fcast_table[fcast_table["Precipitação (mm/h)"] > 0]
            if fcast_table.empty:
                st.success("✅ Nenhuma precipitação prevista nas próximas 48 horas.")
            else:
                st.dataframe(fcast_table, use_container_width=True)
                total_prev = next_48h["precipitation"].sum()
                nivel_equiv_cm = total_prev * factor_mm_cm
                st.info(
                    f"📈 Volume previsto de **{total_prev:.1f} mm** equivale a uma variação estimada de "
                    f"**{nivel_equiv_cm:.1f} cm** no nível do poço (fator {factor_mm_cm:.2f} cm/mm)."
                )
        else:
            st.info("Dados de previsão não disponíveis.")

        # Histórico completo dos últimos 7 dias
        with st.expander("📂 Ver histórico completo de precipitação (últimos 7 dias)"):
            if not hist_df.empty:
                show_hist = hist_df.copy()
                show_hist.index = show_hist.index.strftime("%d/%m/%Y %H:%M")
                show_hist.columns = ["Precipitação (mm/h)"]
                st.dataframe(show_hist, use_container_width=True)
            else:
                st.write("Sem dados históricos disponíveis.")

with tab5:
    st.subheader("🔮 Previsão do Nível do Poço – Próximas 48 Horas")
    st.caption(
        "Simulação a partir do nível atual real do sensor, aplicando o modelo de balanço de massa "
        "com a previsão horária de precipitação da Open-Meteo."
    )

    now_tz5 = pd.Timestamp.now(tz="America/Sao_Paulo")

    # Previsão de precipitação nas próximas 48h (dados horários)
    fcast_48h = pd.DataFrame()
    if not weather_df.empty:
        fcast_48h = weather_df[
            (weather_df.index > now_tz5) &
            (weather_df.index <= now_tz5 + pd.Timedelta(hours=48))
        ].copy()

    # Controles da simulação
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        bomba_status5 = st.selectbox(
            "Status da Bomba para Previsão",
            ["Operacional (Ligada)", "Falha / Sem Energia (Desligada)"],
            key="bomba_status_tab5",
        )
    with col_s2:
        horizonte_h = st.slider(
            "Horizonte da Previsão (horas)", min_value=6, max_value=48, value=24, step=6,
            key="horizonte_tab5",
        )

    # Nível inicial = último valor real do sensor
    nivel_inicial_cm = latest_distance if latest_distance is not None else d_off
    st.info(
        f"📍 Nível de partida: **{nivel_inicial_cm:.1f} cm** (distância sensor→água) "
        f"| Leitura de: **{latest_row['dt_round'].strftime('%d/%m/%Y %H:%M') if 'dt_round' in latest_row and not pd.isna(latest_row.get('dt_round')) else 'última leitura'}**"
    )

    # Simular em passos de 5 minutos usando a previsão horária
    dt_min = 5.0  # minutos por passo
    dt_h_step = dt_min / 60.0
    n_steps = int(horizonte_h * 60 / dt_min)

    sim_times = [now_tz5 + pd.Timedelta(minutes=dt_min * i) for i in range(n_steps + 1)]
    sim_levels = []
    sim_pump_state = []
    sim_rain_rate = []  # cm/h de entrada por chuva em cada passo

    curr_d5 = nivel_inicial_cm
    p1_on = curr_d5 <= d_on
    p2_on = tem_bomba2 and (curr_d5 <= d_on2)
    overflow5 = False
    overflow5_time = None

    for i, t in enumerate(sim_times):
        # Precipitação horária correspondente ao instante t
        if not fcast_48h.empty:
            # pegar a hora exata ou anterior mais próxima
            idx_candidates = fcast_48h.index[fcast_48h.index <= t]
            if len(idx_candidates) > 0:
                precip_mmh = float(fcast_48h.loc[idx_candidates[-1], "precipitation"])
            else:
                precip_mmh = 0.0
        else:
            precip_mmh = 0.0

        # Taxa de entrada no poço (lençol + chuva)
        r_chuva_cm_h = precip_mmh * factor_mm_cm
        r_total_entrada = r_gnd_param + r_chuva_cm_h  # cm/h
        sim_rain_rate.append(r_chuva_cm_h)

        # Gravar o nível antes de avançar
        sim_levels.append(curr_d5)
        sim_pump_state.append(p1_on or p2_on)

        # Checar transbordo
        if not overflow5 and curr_d5 <= d_overflow:
            overflow5 = True
            overflow5_time = t

        if i == n_steps:
            break

        # Avançar o nível
        if bomba_status5 == "Falha / Sem Energia (Desligada)":
            curr_d5 -= r_total_entrada * dt_h_step
        else:
            if not p1_on and curr_d5 <= d_on:
                p1_on = True
            elif p1_on and curr_d5 >= d_off:
                p1_on = False

            if tem_bomba2:
                if not p2_on and curr_d5 <= d_on2:
                    p2_on = True
                elif p2_on and curr_d5 >= d_off2:
                    p2_on = False
            else:
                p2_on = False

            r_active5 = (int(p1_on) + int(p2_on)) * r_pump_param
            curr_d5 += (r_active5 - r_total_entrada) * dt_h_step
        curr_d5 = max(curr_d5, d_overflow - 10)  # limitar para não sair do gráfico

    sim_df = pd.DataFrame({
        "datetime": sim_times[:len(sim_levels)],
        "nivel_cm": sim_levels,
        "bomba_ligada": sim_pump_state,
        "r_chuva_cm_h": sim_rain_rate,
    })

    # ── Métricas resumo ──
    nivel_final = sim_levels[-1]
    nivel_min = min(sim_levels)
    chuva_total_prev = fcast_48h["precipitation"].sum() if not fcast_48h.empty else 0.0
    chuva_periodo = (
        weather_df[
            (weather_df.index > now_tz5) &
            (weather_df.index <= now_tz5 + pd.Timedelta(hours=horizonte_h))
        ]["precipitation"].sum()
        if not weather_df.empty else 0.0
    )

    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("Nível Atual (Partida)", f"{nivel_inicial_cm:.1f} cm")
    col_m2.metric(f"Nível Previsto em {horizonte_h}h", f"{nivel_final:.1f} cm",
                  delta=f"{nivel_final - nivel_inicial_cm:+.1f} cm",
                  delta_color="inverse")
    col_m3.metric("Nível Mais Crítico Previsto", f"{nivel_min:.1f} cm")
    col_m4.metric(f"Chuva prevista ({horizonte_h}h)", f"{chuva_periodo:.1f} mm")

    st.markdown("---")

    # ── Gráfico principal ──
    fig5 = go.Figure()

    # Barras de chuva prevista (eixo secundário)
    if not fcast_48h.empty:
        fcast_plot = fcast_48h[fcast_48h.index <= now_tz5 + pd.Timedelta(hours=horizonte_h)]
        if not fcast_plot.empty:
            fig5.add_trace(
                go.Bar(
                    x=fcast_plot.index,
                    y=fcast_plot["precipitation"],
                    name="Precipitação Prevista (mm/h)",
                    marker_color="rgba(30, 144, 255, 0.3)",
                    yaxis="y2",
                )
            )

    # Linha do nível simulado colorida por estado da bomba
    # Segmentos: bomba ligada = vermelho, desligada = azul
    colors_pump = ["#e74c3c" if b else "#2980b9" for b in sim_df["bomba_ligada"]]
    # Plotar como scatter com cores contínuas
    fig5.add_trace(
        go.Scatter(
            x=sim_df["datetime"],
            y=sim_df["nivel_cm"],
            mode="lines",
            name="Nível Simulado",
            line=dict(color="#2980b9", width=2.5),
            hovertemplate="<b>%{x|%d/%m %H:%M}</b><br>Dist. sensor: %{y:.1f} cm<extra></extra>",
        )
    )

    # Ponto de partida
    fig5.add_trace(
        go.Scatter(
            x=[now_tz5],
            y=[nivel_inicial_cm],
            mode="markers",
            name="Nível Atual (Real)",
            marker=dict(size=12, color="#f39c12", symbol="star"),
        )
    )

    # Linhas de referência
    fig5.add_hline(y=d_on, line_dash="dash", line_color="red",
                   annotation_text="Bóia 1 LIGA", annotation_position="top right")
    fig5.add_hline(y=d_off, line_dash="dash", line_color="green",
                   annotation_text="Bóia 1 DESLIGA", annotation_position="top right")
    if tem_bomba2:
        fig5.add_hline(y=d_on2, line_dash="dash", line_color="orange",
                       annotation_text="Bóia 2 LIGA (Emergência)", annotation_position="top right")
        fig5.add_hline(y=d_off2, line_dash="dash", line_color="teal",
                       annotation_text="Bóia 2 DESLIGA", annotation_position="top right")
    fig5.add_hline(y=d_overflow, line_dash="dot", line_color="darkred",
                   annotation_text=f"Borda do Poço (−{dist_borda_cm:.0f} cm)",
                   annotation_position="top right")

    # Linha vertical "Agora"
    _now5_str = now_tz5.strftime("%Y-%m-%d %H:%M:%S")
    fig5.add_shape(
        type="line", x0=_now5_str, x1=_now5_str, y0=0, y1=1, yref="paper",
        line=dict(dash="dash", color="orange", width=1.5),
    )
    fig5.add_annotation(
        x=_now5_str, y=0.98, yref="paper", text="Agora",
        showarrow=False, xanchor="left", font=dict(color="orange", size=11),
    )

    # Zona crítica de transbordo (faixa cinza)
    fig5.add_hrect(
        y0=0, y1=d_overflow,
        fillcolor="rgba(231,76,60,0.08)",
        line_width=0,
        annotation_text="Zona de Transbordo",
        annotation_position="top left",
        annotation_font_color="#e74c3c",
    )

    fig5.update_layout(
        yaxis=dict(
            autorange="reversed",
            title="Distância Sensor → Água (cm)",
        ),
        yaxis2=dict(
            title="Precipitação (mm/h)",
            overlaying="y",
            side="right",
            showgrid=False,
            rangemode="tozero",
            tickfont=dict(color="rgba(30, 144, 255, 0.7)"),
            title_font=dict(color="rgba(30, 144, 255, 0.7)"),
        ),
        xaxis=dict(title="Data / Hora"),
        legend=dict(orientation="h", y=-0.22, x=0.5, xanchor="center"),
        height=540,
        margin=dict(l=20, r=70, t=30, b=50),
        bargap=0,
    )
    st.plotly_chart(fig5, use_container_width=True)

    # ── Alertas ──
    if overflow5:
        delta_t = overflow5_time - now_tz5
        delta_h = int(delta_t.total_seconds() // 3600)
        delta_m = int((delta_t.total_seconds() % 3600) // 60)
        st.error(
            f"🚨 **ALERTA DE TRANSBORDO PREVISTO:** o poço deve atingir a borda superior "
            f"(água {dist_borda_cm:.0f} cm acima do sensor) "
            f"em **{overflow5_time.strftime('%d/%m %H:%M')}** "
            f"({delta_h}h {delta_m}min a partir de agora)."
        )
    else:
        intensidade_max_prev = fcast_48h["precipitation"].max() if not fcast_48h.empty else 0.0
        intensidade_sat = r_pump_param / factor_mm_cm
        if intensidade_max_prev > intensidade_sat and bomba_status5 == "Operacional (Ligada)":
            st.warning(
                f"⚠️ **Atenção:** a previsão inclui intensidades de até **{intensidade_max_prev:.1f} mm/h**, "
                f"acima do limite de saturação da bomba ({intensidade_sat:.1f} mm/h). "
                "Monitorar de perto."
            )
        else:
            st.success("✅ Nenhum transbordo previsto no horizonte selecionado.")

    # ── Tabela de detalhes horáros ──
    with st.expander("📋 Ver tabela de evolução horária simulada"):
        hourly_sim = sim_df.set_index("datetime").resample("1h").first()
        hourly_sim["Bomba"] = hourly_sim["bomba_ligada"].map({True: "🔴 Ligada", False: "🟢 Desligada"})
        hourly_sim["Entrada por Chuva (cm/h)"] = hourly_sim["r_chuva_cm_h"].round(2)
        hourly_sim["Nível (dist. sensor, cm)"] = hourly_sim["nivel_cm"].round(1)
        hourly_sim["Altura Água (cm)"] = (FUNDODOPOCO - hourly_sim["nivel_cm"]).clip(lower=0).round(1)

        # Merge com precipitação horária prevista
        if not fcast_48h.empty:
            fcast_merge = fcast_48h.resample("1h").sum().rename(columns={"precipitation": "Chuva Prevista (mm/h)"})
            hourly_sim = hourly_sim.join(fcast_merge, how="left")
            hourly_sim["Chuva Prevista (mm/h)"] = hourly_sim["Chuva Prevista (mm/h)"].fillna(0.0).round(2)

        display_cols = ["Nível (dist. sensor, cm)", "Altura Água (cm)", "Bomba", "Entrada por Chuva (cm/h)"]
        if "Chuva Prevista (mm/h)" in hourly_sim.columns:
            display_cols.append("Chuva Prevista (mm/h)")

        hourly_sim.index = hourly_sim.index.strftime("%d/%m/%Y %H:%M")
        st.dataframe(hourly_sim[display_cols], use_container_width=True)

# ──────────────────────────────────────────────────────────────────────────────────
with tab6:
    st.subheader("📊 Análise Histórica de Chuvas & Curva IDF")

    # Limiar de saturação da bomba em mm/h
    intensidade_sat_mmh = r_pump_param / factor_mm_cm

    # ── Controle do período de análise ──
    col_anos, col_btn = st.columns([3, 1])
    with col_anos:
        anos_analise = st.slider(
            "Período de análise (anos)",
            min_value=1, max_value=15, value=5, step=1,
            key="hist_anos_slider",
            help="Número de anos de dados do Open-Meteo Archive API a buscar. "
                 "Mais anos = curva IDF mais robusta, mas requisição maior (pode levar até 30s na 1ª vez).",
        )
    with col_btn:
        st.write("")
        if st.button("🔄 Limpar cache", key="clear_hist_cache", help="Força novo download dos dados históricos"):
            fetch_historical_5years_precip.clear()
            st.rerun()

    end_ref = pd.Timestamp.now(tz="America/Sao_Paulo").normalize() - pd.Timedelta(days=1)
    hist_start_str = (end_ref - pd.DateOffset(years=anos_analise)).strftime("%Y-%m-%d")
    hist_start_label = f"{anos_analise} ano{'s' if anos_analise != 1 else ''} (desde {hist_start_str})"

    # Informação de referência da Defesa Civil
    dc_early = fetch_defesa_civil_rankings()
    if dc_early:
        all_dc_dates = []
        for _df_dur in dc_early.values():
            for _raw in _df_dur["Data/Hora"]:
                try:
                    all_dc_dates.append(pd.to_datetime(_raw, dayfirst=True))
                except Exception:
                    pass
        if all_dc_dates:
            oldest_dc = min(all_dc_dates)
            n_anos_dc = (pd.Timestamp.now() - oldest_dc).days / 365.25
            st.caption(
                f"💡 O evento mais antigo registrado pela Defesa Civil é de "
                f"**{oldest_dc.strftime('%d/%m/%Y')}** (~{n_anos_dc:.0f} anos). "
                f"Selecionar **{n_anos_dc:.0f}+ anos** permite comparar os eventos históricos oficiais com o ERA5."
            )

    with st.spinner(f"⏳ Buscando dados históricos de precipitação ({hist_start_label})..."):
        hist5_df, hist_err = fetch_historical_5years_precip(latitude, longitude, start_date_str=hist_start_str)

    # Fallback para 5 anos se o fetch estendido falhou
    if hist5_df.empty and hist_start_str:
        st.warning(
            f"⚠️ Falha ao buscar dados desde {hist_start_str}: `{hist_err}`. "
            "Tentando janela padrão de 5 anos..."
        )
        with st.spinner("⏳ Buscando 5 anos de dados (fallback)..."):
            hist5_df, hist_err = fetch_historical_5years_precip(latitude, longitude, start_date_str="")

    if hist5_df.empty:
        st.error(
            f"Não foi possível obter dados históricos da Open-Meteo Archive API. "
            f"Erro: `{hist_err}`. Verifique a conexão com a internet e as coordenadas."
        )

    fonte_tab6 = st.radio(
        "Selecione a Base de Dados para Análise Histórica & IDF:",
        options=[
            "📡 Modelo ERA5 Corregido (Série Histórica com Fator de Correção)",
            "🌧️ Estação Real: Ponte Adolfo Konder (Dados Pluviométricos Reais 2021-2025)",
        ],
        index=0,
        horizontal=True,
    )

    hist5_raw_df = hist5_df.copy()

    if "Ponte Adolfo Konder" in fonte_tab6:
        konder_df = load_adolfo_konder_dataset()
        if not konder_df.empty:
            hist5_df = pd.DataFrame({"precipitation": konder_df["precip_15min"].resample("1h").sum()}).dropna()
            st.info("✅ **Análise baseada nas medições reais da Estação Ponte Adolfo Konder (2021–2025).** Fator ERA5 desativado para dados reais de pluviômetro.")
        else:
            st.warning("⚠️ Dados da Estação Ponte Adolfo Konder não encontrados. Utilizando ERA5 Corrigido.")
            if era5_correction != 1.0:
                hist5_df = hist5_df.copy()
                hist5_df["precipitation"] = hist5_df["precipitation"] * era5_correction
    else:
        if era5_correction != 1.0:
            hist5_df = hist5_df.copy()
            hist5_df["precipitation"] = hist5_df["precipitation"] * era5_correction

    anos_disponiveis = sorted(hist5_df.index.year.unique())
    n_anos = len(anos_disponiveis)
    total_horas = len(hist5_df)

    # Caption dinâmico com o período real obtido
    periodo_real = f"{anos_disponiveis[0]}–{anos_disponiveis[-1]} ({n_anos} anos)"
    correcao_txt = f" | Fator ERA5: ×{era5_correction:.2f}" if era5_correction != 1.0 else ""
    st.caption(
        f"Dados horários de {periodo_real} | lat {latitude:.4f}, lon {longitude:.4f} "
        f"| Fonte: Open-Meteo Archive API (ERA5){correcao_txt} | Distribuição: Gumbel (método dos momentos)"
    )

    # ── Métricas gerais ──
    chuva_anual_media = hist5_df["precipitation"].sum() / n_anos
    max_horario = hist5_df["precipitation"].max()
    n_eventos_sat = (hist5_df["precipitation"] >= intensidade_sat_mmh).sum()
    freq_sat_ano = n_eventos_sat / n_anos

    col_h1, col_h2, col_h3, col_h4 = st.columns(4)
    col_h1.metric("Período analisado", f"{n_anos} anos ({anos_disponiveis[0]}–{anos_disponiveis[-1]})",
                   help="Período obtido alinhado ao evento mais antigo registrado pela Defesa Civil de Blumenau")
    col_h2.metric("Chuva média anual", f"{chuva_anual_media:.0f} mm/ano")
    col_h3.metric("Máx. intensidade horária", f"{max_horario:.1f} mm/h")
    col_h4.metric(
        "Eventos saturantes (1h)",
        f"{n_eventos_sat} total | {freq_sat_ano:.1f}/ano",
        help=f"Horas com precipitação ≥ {intensidade_sat_mmh:.1f} mm/h (limite de saturação da bomba)",
    )

    st.markdown("---")

    # ── Configuração das durações para análise IDF ──
    duracoes_h = [1, 2, 3, 6, 12, 24]  # horas
    trs = [2, 5, 10, 25, 50, 100]       # períodos de retorno

    # Calcular máximas anuais para cada duração via janela deslizante
    idf_results = {}  # {dur: {tr: intensidade_mm_h}}
    gumbel_params = {}  # {dur: (alpha, u, annual_max_array)}

    for dur in duracoes_h:
        # Máximo acumulado em janela de `dur` horas
        rolled = hist5_df["precipitation"].rolling(window=dur, min_periods=dur).sum()
        rolled_intensity = rolled / dur  # converter para mm/h
        # Máxima anual
        annual_max = rolled_intensity.groupby(hist5_df.index.year).max().dropna().values
        if len(annual_max) < 2:
            continue
        alpha, u = fit_gumbel(annual_max)
        gumbel_params[dur] = (alpha, u, annual_max)
        idf_results[dur] = {}
        for tr in trs:
            idf_results[dur][tr] = max(gumbel_quantile(alpha, u, tr), 0.0)

    # ── Gráfico 1 – Precipitação anual acumulada ──
    st.subheader("🌧️ Precipitação Anual Acumulada")
    anual_acc = hist5_df["precipitation"].groupby(hist5_df.index.year).sum().reset_index()
    anual_acc.columns = ["Ano", "Precipitação (mm)"]
    fig_anual = go.Figure()
    fig_anual.add_trace(go.Bar(
        x=anual_acc["Ano"].astype(str),
        y=anual_acc["Precipitação (mm)"],
        marker_color="rgba(30, 100, 220, 0.75)",
        name="Acumulado anual",
    ))
    fig_anual.add_hline(
        y=chuva_anual_media,
        line_dash="dash", line_color="orange",
        annotation_text=f"Média: {chuva_anual_media:.0f} mm",
        annotation_position="top right",
    )
    fig_anual.update_layout(
        xaxis_title="Ano", yaxis_title="Precipitação acumulada (mm)",
        height=320, margin=dict(l=20, r=20, t=20, b=30),
    )
    st.plotly_chart(fig_anual, use_container_width=True)

    # ── Gráfico 2 – Histograma de intensidades horárias + limiar de saturação ──
    st.subheader("📊 Distribuição de Intensidades Horárias")
    precip_positivo = hist5_df["precipitation"][hist5_df["precipitation"] > 0]
    fig_hist = go.Figure()
    fig_hist.add_trace(go.Histogram(
        x=precip_positivo,
        nbinsx=60,
        name="Horas com chuva",
        marker_color="rgba(30, 100, 220, 0.6)",
        xbins=dict(start=0, end=max(precip_positivo.max(), intensidade_sat_mmh * 1.5), size=2),
    ))
    fig_hist.add_vline(
        x=intensidade_sat_mmh,
        line_dash="dash", line_color="red",
    )
    fig_hist.add_annotation(
        x=intensidade_sat_mmh, y=1, yref="paper",
        text=f"Saturação bomba<br>{intensidade_sat_mmh:.1f} mm/h",
        showarrow=True, arrowhead=2, arrowcolor="red",
        font=dict(color="red", size=11), xanchor="left",
    )
    fig_hist.update_layout(
        xaxis_title="Intensidade (mm/h)",
        yaxis_title="Número de horas",
        height=320, margin=dict(l=20, r=20, t=20, b=30),
    )
    st.plotly_chart(fig_hist, use_container_width=True)

    n_eventos_sat_5anos = int((hist5_df["precipitation"] >= intensidade_sat_mmh).sum())
    if n_eventos_sat_5anos > 0:
        st.warning(
            f"⚠️ Em **{n_anos} anos** ocorreram **{n_eventos_sat_5anos} horas** com intensidade ≥ {intensidade_sat_mmh:.1f} mm/h "
            f"(limite de saturação da bomba) – média de **{freq_sat_ano:.1f} evento(s)/ano**. "
            "Nesses momentos o poço enche mais rápido do que a bomba consegue esvaziar."
        )
    else:
        st.success(
            f"✅ Nenhuma hora com intensidade ≥ {intensidade_sat_mmh:.1f} mm/h nos últimos {n_anos} anos. "
            "O sistema não foi saturado no período analisado."
        )

    st.markdown("---")

    # ── Gráfico 3 – Curva IDF ──
    st.subheader("📈 Curva IDF – Intensidade–Duração–Frequência")
    st.caption(
        "Distribuição de Gumbel ajustada às máximas anuais horárias. "
        "Cada curva representa um período de retorno (Tr)."
    )

    if idf_results:
        palette = [
            "#1a73e8", "#34a853", "#fbbc04", "#ea4335", "#9c27b0", "#00bcd4"
        ]
        fig_idf = go.Figure()
        for i, tr in enumerate(trs):
            y_idf = [idf_results.get(d, {}).get(tr, None) for d in duracoes_h]
            fig_idf.add_trace(go.Scatter(
                x=duracoes_h,
                y=y_idf,
                mode="lines+markers",
                name=f"Tr = {tr} anos",
                line=dict(color=palette[i % len(palette)], width=2.5 if tr == 25 else 1.5,
                          dash="solid" if tr == 25 else "dot" if tr >= 50 else "solid"),
                marker=dict(size=7),
            ))
        # Linha de saturação da bomba (capacidade 1h)
        fig_idf.add_hline(
            y=intensidade_sat_mmh,
            line_dash="dash", line_color="red",
            annotation_text=f"Saturação bomba ({intensidade_sat_mmh:.1f} mm/h)",
            annotation_position="top right",
        )
        fig_idf.update_layout(
            xaxis=dict(title="Duração (horas)", tickvals=duracoes_h,
                       ticktext=[f"{d}h" for d in duracoes_h]),
            yaxis_title="Intensidade média (mm/h)",
            legend=dict(orientation="h", y=-0.25, x=0.5, xanchor="center"),
            height=420, margin=dict(l=20, r=20, t=20, b=60),
        )
        st.plotly_chart(fig_idf, use_container_width=True)

    # ── Tabela IDF completa ──
    st.subheader("📋 Tabela IDF – Intensidades por Duração e Período de Retorno (mm/h)")
    if idf_results:
        idf_table = pd.DataFrame(
            {f"Tr={tr}a": [idf_results.get(d, {}).get(tr, None) for d in duracoes_h] for tr in trs},
            index=[f"{d}h" for d in duracoes_h],
        ).round(1)
        idf_table.index.name = "Duração"

        def highlight_tr25(col):
            return ["background-color: rgba(234,67,53,0.15); font-weight:bold" if col.name == "Tr=25a" else "" for _ in col]

        st.dataframe(idf_table.style.apply(highlight_tr25), use_container_width=True)

    st.markdown("---")

    # ── Destaque: Chuva de projeto Tr=25 anos ──
    st.subheader("🚨 Chuva de Projeto – Tr = 25 anos")
    tr25_1h = idf_results.get(1, {}).get(25, None)
    tr25_24h = idf_results.get(24, {}).get(25, None)

    col_idf1, col_idf2, col_idf3 = st.columns(3)
    if tr25_1h:
        col_idf1.metric("Intensidade Tr=25 (1h)", f"{tr25_1h:.1f} mm/h")
        vol_poco_tr25_1h = tr25_1h * factor_mm_cm
        col_idf2.metric("Entrada equivalente no poço (1h)", f"{vol_poco_tr25_1h:.1f} cm/h")
        col_idf3.metric(
            "Excede saturação da bomba?",
            "SIM ⚠️" if tr25_1h > intensidade_sat_mmh else "NÃO ✅",
            delta=f"{tr25_1h - intensidade_sat_mmh:+.1f} mm/h",
            delta_color="inverse",
        )

    # ── Simulação da pior chuva (Tr=25, 1h) no poço ──
    if tr25_1h:
        st.markdown("#### Simulação do Impacto no Poço – Chuva Tr=25 anos (1 hora)")
        sim_dur_h_idf = st.selectbox(
            "Duração da chuva de projeto",
            options=[f"{d}h" for d in duracoes_h],
            index=0,
            key="idf_dur_select",
        )
        dur_sel = int(sim_dur_h_idf.replace("h", ""))
        intens_sel = idf_results.get(dur_sel, {}).get(25, tr25_1h)
        taxa_entrada_idf = intens_sel * factor_mm_cm  # cm/h de entrada no poço

        # Simulação dinâmica (passos de 5min)
        dt_h_idf = 5.0 / 60.0
        total_h_idf = max(dur_sel * 1.5, 6.0)
        n_steps_idf = int(total_h_idf / dt_h_idf)
        t_idf = np.linspace(0, total_h_idf, n_steps_idf)

        lvls_idf = []
        curr_idf = d_off
        p1_idf = False
        p2_idf = False
        ovf_idf = False
        ovf_idf_t = None

        for ti in t_idf:
            r_in_idf = (r_gnd_param + taxa_entrada_idf) if ti <= dur_sel else r_gnd_param

            if not p1_idf and curr_idf <= d_on:
                p1_idf = True
            elif p1_idf and curr_idf >= d_off:
                p1_idf = False

            if tem_bomba2:
                if not p2_idf and curr_idf <= d_on2:
                    p2_idf = True
                elif p2_idf and curr_idf >= d_off2:
                    p2_idf = False
            else:
                p2_idf = False

            r_act_idf = (int(p1_idf) + int(p2_idf)) * r_pump_param
            curr_idf += (r_act_idf - r_in_idf) * dt_h_idf

            lvls_idf.append(curr_idf)
            if not ovf_idf and curr_idf <= d_overflow:
                ovf_idf = True
                ovf_idf_t = ti

        fig_idf_sim = go.Figure()
        # Fundo: duração da chuva
        fig_idf_sim.add_vrect(
            x0=0, x1=dur_sel,
            fillcolor="rgba(30, 144, 255, 0.08)", line_width=0,
            annotation_text="Duração da chuva", annotation_position="top left",
        )
        fig_idf_sim.add_trace(go.Scatter(
            x=t_idf, y=lvls_idf,
            mode="lines",
            name=f"Nível poço (Tr=25a, {dur_sel}h)",
            line=dict(color="#e74c3c", width=2.5),
        ))
        fig_idf_sim.add_hline(y=d_on, line_dash="dash", line_color="red",
                              annotation_text="Bóia 1 LIGA", annotation_position="top right")
        fig_idf_sim.add_hline(y=d_off, line_dash="dash", line_color="green",
                              annotation_text="Bóia 1 DESLIGA", annotation_position="top right")
        if tem_bomba2:
            fig_idf_sim.add_hline(y=d_on2, line_dash="dash", line_color="orange",
                                  annotation_text="Bóia 2 LIGA (Emergência)", annotation_position="top right")
            fig_idf_sim.add_hline(y=d_off2, line_dash="dash", line_color="teal",
                                  annotation_text="Bóia 2 DESLIGA", annotation_position="top right")
        fig_idf_sim.add_hline(y=d_overflow, line_dash="dot", line_color="black",
                              annotation_text="Nível Crítico", annotation_position="top right")
        fig_idf_sim.add_hrect(
            y0=0, y1=d_overflow, fillcolor="rgba(231,76,60,0.08)", line_width=0,
        )
        fig_idf_sim.update_layout(
            title=f"Chuva Tr=25 anos | {dur_sel}h | {intens_sel:.1f} mm/h | Partida: d_off={d_off:.0f} cm",
            xaxis_title="Tempo (horas)",
            yaxis=dict(autorange="reversed", title="Distância Sensor → Água (cm)"),
            height=420, margin=dict(l=20, r=20, t=50, b=30),
        )
        st.plotly_chart(fig_idf_sim, use_container_width=True)

        if ovf_idf:
            st.error(
                f"🚨 **TRANSBORDO ESPERADO** para chuva Tr=25 anos ({dur_sel}h, {intens_sel:.1f} mm/h): "
                f"o poço atingiria o nível crítico em **{ovf_idf_t*60:.0f} minutos** "
                f"({ovf_idf_t:.2f} horas) após o início da chuva!"
            )
        else:
            st.success(
                f"✅ O sistema suporta a chuva de projeto Tr=25 anos ({dur_sel}h, {intens_sel:.1f} mm/h) "
                "sem transbordo nas condições simuladas."
            )

    # ── Máximas anuais (expander) ──
    with st.expander("📊 Ver máximas anuais por duração (entrada do ajuste Gumbel)"):
        max_table_data = {}
        for dur in duracoes_h:
            if dur in gumbel_params:
                alpha, u, annual_max = gumbel_params[dur]
                max_table_data[f"{dur}h (mm/h)"] = np.round(annual_max, 2)
        if max_table_data:
            n_rows = max(len(v) for v in max_table_data.values())
            idx = [str(y) for y in anos_disponiveis[:n_rows]]
            max_df = pd.DataFrame(max_table_data, index=idx[:n_rows])
            max_df.index.name = "Ano"
            st.dataframe(max_df, use_container_width=True)
            st.caption(
                "⚠️ Gumbel ajustado com apenas 5 pontos (1 por ano). "
                "Para aplicações de engenharia crítica, recomenda-se série histórica ≥ 30 anos."
            )

    # ── Seção de Calibração: Ponte Adolfo Konder vs ERA5 ──
    st.markdown("---")
    st.subheader("🎯 Calibração Pluviométrica: Estação Ponte Adolfo Konder vs. ERA5 Bruto (2021–2025)")
    st.caption(
        "Comparação ano a ano dos picos máximos anuais medidos pelo pluviômetro local da Ponte Adolfo Konder (Blumenau) "
        "com os picos brutos fornecidos pelo modelo de satélite ERA5 (Open-Meteo):"
    )
    konder_calib_ds = load_adolfo_konder_dataset()
    if not konder_calib_ds.empty and 'hist5_raw_df' in locals() and not hist5_raw_df.empty:
        konder_h_series = konder_calib_ds["precip_15min"].resample("h").sum()
        era_raw_series = hist5_raw_df["precipitation"]

        calib_rows = []
        ratios_list = []
        for y_cal in [2021, 2022, 2023, 2024, 2025]:
            k_val = konder_h_series[str(y_cal)].max() if str(y_cal) in konder_h_series.index.year.astype(str) else np.nan
            e_val = era_raw_series[str(y_cal)].max() if str(y_cal) in era_raw_series.index.year.astype(str) else np.nan
            if pd.notna(k_val) and pd.notna(e_val) and e_val > 0:
                r_cal = k_val / e_val
                ratios_list.append(r_cal)
                calib_rows.append({
                    "Ano": str(y_cal),
                    "Ponte Adolfo Konder (Medição Real mm/h)": round(k_val, 1),
                    "ERA5 Bruto (Satélite mm/h)": round(e_val, 1),
                    "Fator de Correção Requerido": f"×{r_cal:.2f}",
                })
        if calib_rows:
            st.dataframe(pd.DataFrame(calib_rows).set_index("Ano"), use_container_width=True)
            med_ratio = np.mean(ratios_list) if ratios_list else 2.07
            max_ratio = np.max(ratios_list) if ratios_list else 2.90
            st.info(
                f"💡 **Conclusão da Calibração Local (Ponte Adolfo Konder):**\n"
                f"- A média dos fatores anuais é de **×{med_ratio:.2f}**.\n"
                f"- Para tempestades e picos de eventos graves (ex: 2022 e 2025 com 58.0 mm/h), a subestimação do ERA5 atinge **×{max_ratio:.2f}**.\n"
                f"- Recomenda-se utilizar o **Fator de Correção em ×2.60** no menu lateral para garantir margem de segurança no dimensionamento hidráulico NBR 10844."
            )

    # ── Seção de Validação: Open-Meteo vs. Defesa Civil ──
    st.markdown("---")
    st.subheader("🔬 Validação: Open-Meteo Archive vs. Defesa Civil de Blumenau")
    st.caption(
        "O Open-Meteo usa reanálise ERA5 (~9 km de resolução espacial e 1 hora temporal). "
        "A Defesa Civil usa pluviômetros locais que capturam picos pontuais. "
        "Esta seção compara os dois e estima o fator de subestimação do ERA5."
    )

    with st.spinner("Buscando dados da Defesa Civil para validação…"):
        dc_val = fetch_defesa_civil_rankings()

    if not dc_val or hist5_df.empty:
        st.warning("Não foi possível realizar a validação (dados indisponíveis).")
    else:
        dur_val_map = {"15min": 1, "30min": 1, "01h": 1, "24h": 24, "96h": 96}
        dur_val_h   = {"15min": 0.25, "30min": 0.5, "01h": 1.0, "24h": 24.0, "96h": 96.0}

        # ── 1. Tabela de máximos globais por duração ──
        st.markdown("#### 1️⃣ Máximos Globais por Duração — DC (histórico completo) vs. Open-Meteo (5 anos)")
        rows_cmp = []
        for dur, df_dc in dc_val.items():
            dh = dur_val_h.get(dur, 1.0)
            win = dur_val_map.get(dur, 1)           # janela em horas para rolling
            dc_max_mm  = df_dc["Acumulado_mm"].max()
            dc_max_mmh = dc_max_mm / dh

            # Open-Meteo: rolling sum de `win` horas → máximo da série
            om_rolled = hist5_df["precipitation"].rolling(window=win, min_periods=win).sum()
            om_max_mm  = om_rolled.max()
            om_max_mmh = om_max_mm / dh

            ratio = om_max_mmh / dc_max_mmh if dc_max_mmh > 0 else None
            rows_cmp.append({
                "Duração": dur,
                "DC – Máx. (mm)": round(dc_max_mm, 1),
                "OM – Máx. 5a (mm)": round(om_max_mm, 1),
                "DC – Intensidade (mm/h)": round(dc_max_mmh, 1),
                "OM – Intensidade (mm/h)": round(om_max_mmh, 1),
                "OM / DC (%)": round(ratio * 100, 1) if ratio else None,
            })

        df_cmp = pd.DataFrame(rows_cmp).set_index("Duração")

        def color_ratio(val):
            if val is None:
                return ""
            if val >= 80:
                return "background-color: rgba(52,168,83,0.2)"
            if val >= 60:
                return "background-color: rgba(251,188,4,0.25)"
            return "background-color: rgba(234,67,53,0.2)"

        st.dataframe(
            df_cmp.style.map(color_ratio, subset=["OM / DC (%)"]),
            use_container_width=True,
        )
        st.caption(
            "🟢 ≥ 80%: boa concordância | 🟡 60–80%: subestimação moderada | 🔴 < 60%: subestimação significativa"
        )

        # ── 2. Lookup de eventos recentes (DC na janela de 5 anos) ──
        st.markdown("#### 2️⃣ Eventos Recentes da DC dentro da Janela Open-Meteo (5 anos)")
        st.caption(
            "Para eventos registrados pela DC dentro dos últimos 5 anos, buscamos o valor "
            "que o Open-Meteo registrou na mesma janela temporal e comparamos diretamente."
        )

        om_start = hist5_df.index.min()
        om_end   = hist5_df.index.max()
        event_rows = []

        for dur, df_dc in dc_val.items():
            dh  = dur_val_h.get(dur, 1.0)
            win = dur_val_map.get(dur, 1)
            for _, row in df_dc.iterrows():
                raw_dt = row["Data/Hora"]
                try:
                    dt = pd.to_datetime(raw_dt, dayfirst=True).tz_localize("America/Sao_Paulo")
                except Exception:
                    continue
                if dt < om_start or dt > om_end:
                    continue

                # Janela de busca: [dt - win horas, dt + 1h]
                t0 = dt - pd.Timedelta(hours=win + 1)
                t1 = dt + pd.Timedelta(hours=2)
                slice_om = hist5_df.loc[t0:t1, "precipitation"]
                if slice_om.empty or len(slice_om) < win:
                    continue
                om_event_mm = slice_om.rolling(window=win, min_periods=win).sum().max()
                if pd.isna(om_event_mm):
                    continue

                dc_mm = row["Acumulado_mm"]
                ratio_ev = (om_event_mm / dc_mm * 100) if dc_mm else None
                event_rows.append({
                    "Duração": dur,
                    "Estação DC": row["Estação"],
                    "Região": row["Região"],
                    "Data/Hora DC": raw_dt,
                    "DC (mm)": round(dc_mm, 1),
                    "Open-Meteo (mm)": round(om_event_mm, 1),
                    "OM / DC (%)": round(ratio_ev, 1) if ratio_ev else None,
                })

        if event_rows:
            df_ev = pd.DataFrame(event_rows).sort_values("OM / DC (%)")
            df_ev.index = range(1, len(df_ev) + 1)
            df_ev.index.name = "#"

            def color_ev(val):
                if val is None:
                    return ""
                if val >= 80:
                    return "background-color: rgba(52,168,83,0.2)"
                if val >= 50:
                    return "background-color: rgba(251,188,4,0.25)"
                return "background-color: rgba(234,67,53,0.2)"

            st.dataframe(
                df_ev.style.map(color_ev, subset=["OM / DC (%)"]),
                use_container_width=True,
            )

            # ── 3. Scatter OM vs DC ──
            st.markdown("#### 3️⃣ Dispersão: Open-Meteo vs. Defesa Civil (eventos recentes)")
            fig_scatter = go.Figure()
            # Linha de referência 1:1
            max_val = max(df_ev["DC (mm)"].max(), df_ev["Open-Meteo (mm)"].max()) * 1.1
            fig_scatter.add_trace(go.Scatter(
                x=[0, max_val], y=[0, max_val],
                mode="lines", name="Referência 1:1",
                line=dict(dash="dash", color="gray", width=1.5),
            ))
            for dur_label in df_ev["Duração"].unique():
                sub = df_ev[df_ev["Duração"] == dur_label]
                fig_scatter.add_trace(go.Scatter(
                    x=sub["DC (mm)"], y=sub["Open-Meteo (mm)"],
                    mode="markers+text",
                    name=dur_label,
                    text=sub["Estação DC"],
                    textposition="top center",
                    textfont=dict(size=8),
                    marker=dict(size=10),
                    hovertemplate=(
                        "<b>%{text}</b><br>"
                        "DC: %{x:.1f} mm<br>"
                        "OM: %{y:.1f} mm<br>"
                        "<extra>" + dur_label + "</extra>"
                    ),
                ))
            fig_scatter.update_layout(
                xaxis_title="Acumulado Defesa Civil (mm)",
                yaxis_title="Acumulado Open-Meteo ERA5 (mm)",
                height=420,
                margin=dict(l=20, r=20, t=20, b=30),
                legend=dict(orientation="h", y=-0.25, x=0.5, xanchor="center"),
            )
            st.plotly_chart(fig_scatter, use_container_width=True)
            st.caption(
                "Pontos **abaixo** da linha 1:1 → Open-Meteo subestima o dado local. "
                "Pontos **acima** → Open-Meteo superestima (raro para chuvas intensas pontuais)."
            )

            # ── 4. Conclusão automática ──
            ratios_validos = df_ev["OM / DC (%)"].dropna()
            if len(ratios_validos) > 0:
                media_ratio = ratios_validos.mean()
                fator_correcao = 100 / media_ratio if media_ratio > 0 else None
                st.markdown("#### 4️⃣ Conclusão — Fator de Correção Estimado")
                col_c1, col_c2, col_c3 = st.columns(3)
                col_c1.metric("Média OM/DC", f"{media_ratio:.1f}%",
                              help="Percentual médio que o Open-Meteo captura dos recordes DC para eventos recentes")
                col_c2.metric("Fator de correção estimado", f"×{fator_correcao:.2f}" if fator_correcao else "N/D",
                              help="Multiplique os valores Open-Meteo por este fator para estimar os picos locais equivalentes")
                col_c3.metric("Eventos comparados", f"{len(ratios_validos)}",
                              help="Número de registros DC com data dentro da janela Open-Meteo (5 anos)")

                if media_ratio < 50:
                    st.error(
                        f"🔴 **Subestimação significativa**: o Open-Meteo captura em média apenas **{media_ratio:.0f}%** "
                        "dos valores registrados pela Defesa Civil. "
                        "Isso é esperado para dados de reanálise de grade grossa em regiões com topografia complexa. "
                        f"Para fins de projeto, considere multiplicar os valores IDF por **{fator_correcao:.1f}×**."
                    )
                elif media_ratio < 75:
                    st.warning(
                        f"🟡 **Subestimação moderada**: Open-Meteo captura ~**{media_ratio:.0f}%** dos picos DC. "
                        f"Fator de correção sugerido: **{fator_correcao:.2f}×**."
                    )
                else:
                    st.success(
                        f"🟢 **Boa concordância**: Open-Meteo captura ~**{media_ratio:.0f}%** dos picos DC. "
                        "Os dados de reanálise são razoavelmente representativos para este local."
                    )
        else:
            st.info(
                "ℹ️ Nenhum evento recente da Defesa Civil (últimos 5 anos) foi encontrado na janela "
                "do Open-Meteo Archive para comparação direta. "
                "A comparação de máximos globais na tabela acima ainda é válida."
            )

        st.markdown("---")
        st.caption(
            "**Por que os dados diferem?** O Open-Meteo Archive usa ERA5 (ECMWF), "
            "uma reanálise global com células de grade de ~9 km. "
            "Eventos de chuva intensa muito localizados (convectivos) são suavizados espacialmente, "
            "resultando em intensidades menores do que as medidas por pluviômetros pontuais. "
            "Já a Defesa Civil usa estações físicas que capturam o pico local real. "
            "Para dimensionamento de sistemas como este poço, os dados da Defesa Civil são mais conservadores e confiáveis."
        )

# ──────────────────────────────────────────────────────────────────────────────────
with tab7:
    st.subheader("🛑 Maiores Chuvas Históricas – Defesa Civil de Blumenau")
    st.caption(
        "Dados oficiais do AlertaBlu – Últimos Rankings por duração (15min, 30min, 1h, 24h, 96h). "
        "Fonte: [defesacivil.blumenau.sc.gov.br](https://defesacivil.blumenau.sc.gov.br/d/maiores-chuvas)"
    )

    intensidade_sat_dc = r_pump_param / factor_mm_cm  # mm/h de saturação da bomba

    with st.spinner("⏳ Buscando dados do site da Defesa Civil de Blumenau..."):
        dc_rankings = fetch_defesa_civil_rankings()

    if not dc_rankings:
        st.error(
            "Não foi possível buscar os dados da Defesa Civil de Blumenau. "
            "Verifique a conexão com a internet ou se o site está acessível."
        )
    else:
        # ── Mapa de conversão de duração para horas (para calcular intensidade em mm/h)
        dur_para_horas = {
            "15min": 0.25,
            "30min": 0.5,
            "01h": 1.0,
            "1h": 1.0,
            "24h": 24.0,
            "96h": 96.0,
        }

        # ── Filtro de Região / Bairro ──
        # Coletar todas as regiões presentes nos dados
        todas_regioes = sorted(set(
            regiao
            for df_dur in dc_rankings.values()
            for regiao in df_dur["Região"].dropna().unique()
        ))
        OPCAO_TODAS = "★ Todas as regiões (máximo global)"
        opcoes_regiao = [OPCAO_TODAS] + todas_regioes

        col_dur, col_reg = st.columns(2)
        with col_dur:
            dur_selecionada = st.selectbox(
                "Duração:",
                options=list(dc_rankings.keys()),
                key="dc_dur_select",
            )
        with col_reg:
            regiao_selecionada = st.selectbox(
                "Região / Bairro:",
                options=opcoes_regiao,
                key="dc_regiao_select",
                help="Filtra o ranking pelas estações de uma região específica, ou exibe o máximo global.",
            )

        # ── Visão geral: maiores registros de cada duração (rank 1), respeitando o filtro de região ──
        st.subheader("🏆 Recordes Absolutos por Duração")
        resumo_cols = st.columns(len(dc_rankings))
        for i, (dur, df_dur) in enumerate(dc_rankings.items()):
            if regiao_selecionada == OPCAO_TODAS:
                df_top = df_dur
            else:
                df_top = df_dur[df_dur["Região"] == regiao_selecionada]
            if df_top.empty:
                resumo_cols[i].metric(label=f"Máx. em {dur}", value="—", help=f"Nenhum registro para {regiao_selecionada}")
                continue
            top = df_top.sort_values("Acumulado_mm", ascending=False).iloc[0]
            dur_h = dur_para_horas.get(dur, 1.0)
            intensidade_top = top["Acumulado_mm"] / dur_h if dur_h > 0 else 0
            resumo_cols[i].metric(
                label=f"Máx. em {dur}",
                value=f"{top['Acumulado_mm']:.1f} mm",
                help=f"{top['Estação']} ({top['Região']}) | {top['Data/Hora']} | {intensidade_top:.1f} mm/h",
            )

        st.markdown("---")

        # Aplicar filtro de região
        df_sel = dc_rankings[dur_selecionada].copy()
        regiao_sem_dados = False
        if regiao_selecionada != OPCAO_TODAS:
            df_sel_filtrado = df_sel[df_sel["Região"] == regiao_selecionada].copy()
            if df_sel_filtrado.empty:
                # Descobrir quais durações têm dados para esta região
                durs_com_dados = [
                    dur for dur, df_dur in dc_rankings.items()
                    if not df_dur[df_dur["Região"] == regiao_selecionada].empty
                ]
                msg_durs = ", ".join(f"**{d}**" for d in durs_com_dados) if durs_com_dados else "nenhuma"
                st.info(
                    f"ℹ️ A região **{regiao_selecionada}** não aparece no ranking de **{dur_selecionada}** "
                    f"(o top-10 dessa duração é dominado por outras regiões). "
                    f"Durações com registros para {regiao_selecionada}: {msg_durs}. "
                    "Exibindo ranking global para referência."
                )
                regiao_sem_dados = True
                # Mantém df_sel com o ranking global para exibição de fallback
            else:
                df_sel = df_sel_filtrado
        dur_h_sel = dur_para_horas.get(dur_selecionada, 1.0)
        df_sel["Intensidade_mmh"] = df_sel["Acumulado_mm"] / dur_h_sel
        ultima_atualiz = df_sel["ultima_atualizacao"].iloc[0] if not df_sel.empty else ""

        st.caption(f"Tabela: Maiores Chuvas em **{dur_selecionada}** | Última atualização: {ultima_atualiz}")

        # Gráfico de barras horizontal com destaque das que saturam a bomba
        cores = [
            "rgba(231,76,60,0.85)" if row["Intensidade_mmh"] >= intensidade_sat_dc else "rgba(30,100,220,0.75)"
            for _, row in df_sel.iterrows()
        ]
        fig_dc = go.Figure()
        fig_dc.add_trace(go.Bar(
            x=df_sel["Acumulado_mm"],
            y=[f"{row['Estação']} ({row['Data/Hora']})" for _, row in df_sel.iterrows()],
            orientation="h",
            marker_color=cores,
            text=[f"{v:.1f} mm" for v in df_sel["Acumulado_mm"]],
            textposition="outside",
            customdata=df_sel[["Estação", "Região", "Intensidade_mmh"]].values,
            hovertemplate=(
                "<b>%{customdata[0]}</b> (%{customdata[1]})<br>"
                "Acumulado: %{x:.1f} mm<br>"
                f"Intensidade média: %{{customdata[2]:.1f}} mm/h<br>"
                "<extra></extra>"
            ),
        ))
        # Linha de saturação convertida para acumulado na duração selecionada
        acum_sat = intensidade_sat_dc * dur_h_sel
        fig_dc.add_vline(
            x=acum_sat,
            line_dash="dash", line_color="red",
        )
        fig_dc.add_annotation(
            x=acum_sat, y=1, yref="paper",
            text=f"Saturação bomba<br>({acum_sat:.1f} mm em {dur_selecionada})",
            showarrow=True, arrowhead=2, arrowcolor="red",
            font=dict(color="red", size=10), xanchor="left",
        )
        fig_dc.update_layout(
            xaxis_title=f"Acumulado em {dur_selecionada} (mm)",
            yaxis=dict(autorange="reversed"),
            height=max(300, 50 + 40 * len(df_sel)),
            margin=dict(l=10, r=120, t=20, b=30),
        )
        st.plotly_chart(fig_dc, use_container_width=True)

        # Quantos registros no ranking superam a saturação da bomba?
        n_acima_sat = (df_sel["Intensidade_mmh"] >= intensidade_sat_dc).sum()
        if n_acima_sat > 0:
            st.warning(
                f"⚠️ **{n_acima_sat} de {len(df_sel)} registros** neste ranking têm intensidade ≥ **{intensidade_sat_dc:.1f} mm/h** "
                f"(limite de saturação da bomba). Em eventos como esses, o poço encheria mais rápido do que a bomba conseguiria esvaziar."
            )
        else:
            st.success(
                f"✅ Nenhum registro deste ranking supera o limite de saturação da bomba ({intensidade_sat_dc:.1f} mm/h)."
            )

        # ── Tabela detalhada ──
        st.subheader("📋 Tabela Completa")
        df_display = df_sel[["Estação", "Região", "Data/Hora", "Acumulado_mm", "Intensidade_mmh"]].copy()
        df_display.columns = ["Estação", "Região", "Data/Hora", f"Acumulado em {dur_selecionada} (mm)", "Intensidade média (mm/h)"]
        df_display[f"Acumulado em {dur_selecionada} (mm)"] = df_display[f"Acumulado em {dur_selecionada} (mm)"].round(1)
        df_display["Intensidade média (mm/h)"] = df_display["Intensidade média (mm/h)"].round(1)
        df_display.index = range(1, len(df_display) + 1)
        df_display.index.name = "#"
        st.dataframe(df_display, use_container_width=True)

        st.markdown("---")

        # ── Contexto IDF: comparar maiores chuvas do DC com a IDF Gumbel (se disponível) ──
        st.subheader("📈 Contexto IDF: Maiores Chuvas Históricas vs. Curva Gumbel (Open-Meteo)")
        st.caption(
            "Pontos vermelhos = recordes da Defesa Civil de Blumenau | "
            "Linhas = curvas IDF estimadas da tab Histórico & IDF (5 anos)"
        )

        # Checar se idf_results já foi calculado (está no escopo de tab6, mas Python é single-scope)
        idf_available = "idf_results" in dir() or "idf_results" in globals() or "idf_results" in locals()
        # Como o código de tab6 só roda quando a aba está ativa no Streamlit,
        # vamos recalcular de forma simples se necessário
        try:
            _ = idf_results  # checa se já existe no escopo global
            idf_ok = True
        except NameError:
            idf_ok = False

        # Construir pontos dos recordes da Defesa Civil (respeitando o filtro de região)
        dc_points_x = []  # duração em horas
        dc_points_y = []  # intensidade mm/h
        dc_points_label = []
        for dur, df_dur in dc_rankings.items():
            dh = dur_para_horas.get(dur, None)
            if dh is None:
                continue
            df_pts = df_dur if regiao_selecionada == OPCAO_TODAS else df_dur[df_dur["Região"] == regiao_selecionada]
            if df_pts.empty:
                continue
            top = df_pts.sort_values("Acumulado_mm", ascending=False).iloc[0]
            dc_points_x.append(dh)
            dc_points_y.append(top["Acumulado_mm"] / dh)
            dc_points_label.append(f"{top['Estação']} ({dur}: {top['Acumulado_mm']:.0f}mm)")

        fig_ctx = go.Figure()

        if idf_ok:
            duracoes_h_ctx = [1, 2, 3, 6, 12, 24]
            palette_ctx = ["#1a73e8", "#34a853", "#fbbc04", "#ea4335", "#9c27b0", "#00bcd4"]
            trs_ctx = [2, 5, 10, 25, 50, 100]
            for i_tr, tr in enumerate(trs_ctx):
                y_ctx = [idf_results.get(d, {}).get(tr, None) for d in duracoes_h_ctx]
                fig_ctx.add_trace(go.Scatter(
                    x=duracoes_h_ctx, y=y_ctx,
                    mode="lines",
                    name=f"Tr={tr}a (Gumbel)",
                    line=dict(color=palette_ctx[i_tr % len(palette_ctx)],
                              width=2.5 if tr == 25 else 1.2,
                              dash="solid" if tr in [25, 100] else "dot"),
                    opacity=0.7,
                ))

        # Pontos dos recordes da Defesa Civil
        if dc_points_x:
            fig_ctx.add_trace(go.Scatter(
                x=dc_points_x, y=dc_points_y,
                mode="markers+text",
                name="Recorde Defesa Civil BNU",
                marker=dict(size=12, color="red", symbol="star"),
                text=dc_points_label,
                textposition="top center",
                textfont=dict(size=9),
            ))

        # Linha de saturação horizontal
        fig_ctx.add_hline(
            y=intensidade_sat_dc,
            line_dash="dash", line_color="darkred",
            annotation_text=f"Saturação bomba ({intensidade_sat_dc:.1f} mm/h)",
            annotation_position="top right",
        )
        fig_ctx.update_layout(
            xaxis=dict(
                title="Duração (horas)",
                tickvals=[0.25, 0.5, 1, 2, 3, 6, 12, 24, 96],
                ticktext=["15min", "30min", "1h", "2h", "3h", "6h", "12h", "24h", "96h"],
            ),
            yaxis_title="Intensidade média (mm/h)",
            legend=dict(orientation="h", y=-0.3, x=0.5, xanchor="center"),
            height=480,
            margin=dict(l=20, r=20, t=20, b=80),
        )
        st.plotly_chart(fig_ctx, use_container_width=True)

        if not idf_ok:
            st.info(
                "ℹ️ Para ver as curvas IDF junto com os recordes, acesse primeiro a aba "
                "**📊 Histórico & IDF (5 anos)** para que os dados sejam calculados."
            )

        st.markdown("---")
        st.info(
            "🔗 Dados originais: [Defesa Civil de Blumenau – Maiores Chuvas]("
            "https://defesacivil.blumenau.sc.gov.br/d/maiores-chuvas). "
            "Os dados são atualizados pelo AlertaBlu conforme novos eventos ocorrem."
        )



    # ── SEÇÃO EXCLUSIVA: ESTAÇÃO PONTE ADOLFO KONDER (GUMBEL & EXTREMOS LOCALIZADOS) ──
    st.markdown("---")
    st.subheader("🌊 Estação Pluviométrica Ponte Adolfo Konder — Análise de Gumbel & Recordes (2021–2025)")
    st.caption(
        "Seção exclusiva dedicada à Estação Pluviométrica física instalada na Ponte Adolfo Konder (Centro de Blumenau). "
        "Contém a análise de frequência de extremos por Ajuste da Distribuição de Gumbel e os maiores picos gravados no poço de monitoramento."
    )

    konder_sec_df = load_adolfo_konder_dataset()
    if not konder_sec_df.empty:
        pico_15m_rec = konder_sec_df["precip_15min"].max()
        pico_15m_dt = konder_sec_df["precip_15min"].idxmax()
        pico_1h_rec = konder_sec_df["precipitation"].max()
        pico_1h_dt = konder_sec_df["precipitation"].idxmax()
        tot_leituras_k = len(konder_sec_df)

        mk1, mk2, mk3, mk4 = st.columns(4)
        mk1.metric("Recorde 15min (Pulso)", f"{pico_15m_rec:.1f} mm", help=f"Data: {pico_15m_dt.strftime('%d/%m/%Y %H:%M')} | Taxa inst.: {pico_15m_rec*4:.1f} mm/h")
        mk2.metric("Recorde Horário (1h)", f"{pico_1h_rec:.1f} mm/h", help=f"Data: {pico_1h_dt.strftime('%d/%m/%Y %H:%M')}")
        mk3.metric("Período Coberto", "2021–2025", help=f"{konder_sec_df.index.min().strftime('%d/%m/%Y')} a {konder_sec_df.index.max().strftime('%d/%m/%Y')}")
        mk4.metric("Total de Leituras", f"{tot_leituras_k:,}", help="Intervalos contínuos de 15 minutos")

        st.markdown("#### 📈 Curvas IDF de Gumbel — Estação Ponte Adolfo Konder")
        st.caption("Intensidades máximas calculadas por Gumbel (método dos momentos) sobre as séries reais de 15min a 24h.")

        dur_map_k = {
            "15min": (0.25, konder_sec_df["precip_mmh"]),
            "30min": (0.5, konder_sec_df["precip_15min"].rolling(2, min_periods=1).sum() * 2.0),
            "1h": (1.0, konder_sec_df["precip_15min"].rolling(4, min_periods=1).sum()),
            "2h": (2.0, konder_sec_df["precip_15min"].rolling(8, min_periods=1).sum() / 2.0),
            "6h": (6.0, konder_sec_df["precip_15min"].rolling(24, min_periods=1).sum() / 6.0),
            "24h": (24.0, konder_sec_df["precip_15min"].rolling(96, min_periods=1).sum() / 24.0),
        }
        trs_k = [2, 5, 10, 25, 50, 100]
        idf_k_dict = {}

        for d_lbl, (dh_k, s_k) in dur_map_k.items():
            ann_max_k = s_k.groupby(s_k.index.year).max().dropna().values
            if len(ann_max_k) >= 2:
                alpha_k, u_k = fit_gumbel(ann_max_k)
                idf_k_dict[d_lbl] = {tr: round(max(gumbel_quantile(alpha_k, u_k, tr), 0.0), 1) for tr in trs_k}

        if idf_k_dict:
            idf_k_df = pd.DataFrame(idf_k_dict).T
            idf_k_df.columns = [f"Tr={tr}a" for tr in trs_k]
            idf_k_df.index.name = "Duração"

            palette_k = ["#1a73e8", "#34a853", "#fbbc04", "#ea4335", "#9c27b0", "#00bcd4"]
            fig_idf_k = go.Figure()
            durs_x = list(idf_k_dict.keys())
            for idx_tr, tr_val in enumerate(trs_k):
                y_vals_k = [idf_k_dict[d_lbl][tr_val] for d_lbl in durs_x]
                fig_idf_k.add_trace(go.Scatter(
                    x=durs_x, y=y_vals_k, mode="lines+markers",
                    name=f"Tr = {tr_val} anos",
                    line=dict(color=palette_k[idx_tr % len(palette_k)], width=2.5 if tr_val == 25 else 1.5),
                    marker=dict(size=7),
                ))
            fig_idf_k.add_hline(
                y=intensidade_sat_dc, line_dash="dash", line_color="red",
                annotation_text=f"Saturação bomba ({intensidade_sat_dc:.1f} mm/h)",
                annotation_position="top right",
            )
            fig_idf_k.update_layout(
                title="Curvas IDF Gumbel — Estação Ponte Adolfo Konder (2021–2025)",
                xaxis_title="Duração", yaxis_title="Intensidade Média (mm/h)",
                legend=dict(orientation="h", y=-0.22, x=0.5, xanchor="center"),
                height=420, margin=dict(l=20, r=20, t=40, b=50),
            )
            st.plotly_chart(fig_idf_k, use_container_width=True)

            st.markdown("##### 📋 Tabela IDF Gumbel (Ponte Adolfo Konder — mm/h)")
            def highlight_tr25_k(col):
                return ["background-color: rgba(234,67,53,0.15); font-weight:bold" if col.name == "Tr=25a" else "" for _ in col]
            st.dataframe(idf_k_df.style.apply(highlight_tr25_k), use_container_width=True)

        st.markdown("#### 🏆 Maiores Picos Registrados na Ponte Adolfo Konder")
        tab_p1, tab_p2 = st.tabs(["⚡ Top 10 Pulsos de 15 Minutos", "🌧️ Top 10 Eventos Horários (1h)"])

        with tab_p1:
            top_15m = konder_sec_df.sort_values("precip_15min", ascending=False).head(10).copy()
            top_15m_display = []
            for dt_k, r_k in top_15m.iterrows():
                val_15 = r_k["precip_15min"]
                rate_h = val_15 * 4.0
                sat_status = "🚨 Excede Bomba 1" if rate_h > intensidade_sat_dc else "✅ Suportado"
                top_15m_display.append({
                    "Data / Hora": dt_k.strftime("%d/%m/%Y %H:%M"),
                    "Precipitação (mm / 15min)": f"{val_15:.1f} mm",
                    "Taxa Horária Eq. (mm/h)": f"{rate_h:.1f} mm/h",
                    "Entrada no Poço (cm/h)": f"{rate_h * factor_mm_cm:.1f} cm/h",
                    "Status": sat_status,
                })
            st.dataframe(pd.DataFrame(top_15m_display), use_container_width=True)

        with tab_p2:
            top_1h = konder_sec_df["precipitation"].resample("1h").sum().sort_values(ascending=False).head(10).copy()
            top_1h_display = []
            for dt_k, val_1h in top_1h.items():
                sat_status = "🚨 Excede Bomba 1" if val_1h > intensidade_sat_dc else "✅ Suportado"
                top_1h_display.append({
                    "Data / Hora": dt_k.strftime("%d/%m/%Y %H:00"),
                    "Chuva Acumulada 1h (mm)": f"{val_1h:.1f} mm/h",
                    "Entrada no Poço (cm/h)": f"{val_1h * factor_mm_cm:.1f} cm/h",
                    "Status": sat_status,
                })
            st.dataframe(pd.DataFrame(top_1h_display), use_container_width=True)

# ──────────────────────────────────────────────────────────────────────────────────
with tab8:
    st.subheader("🗂️ Diagrama do Poço de Drenagem")
    st.caption("Diagrama em escala real baseado nos parâmetros do sidebar. Atualiza automaticamente ao alterar qualquer parâmetro.")

    # ── Geometria do poço (eixo Y: distância a partir do sensor, positivo = abaixo, negativo = acima) ──
    # Eixo Y = distância do sensor à água (o mesmo do simulador)
    # Y = 0  → nível do sensor
    # Y > 0  → abaixo do sensor (fundo = +FUNDODOPOCO)
    # Y < 0  → acima do sensor (borda = -dist_borda_cm)

    y_fundo = float(FUNDODOPOCO)          # +150 cm (fundo do poço)
    y_borda = -float(dist_borda_cm)       # -90 cm (borda superior)
    y_sensor = 0.0                         # sensor
    y_don = float(d_on)                   # nível onde bomba 1 liga
    y_doff = float(d_off)                 # nível onde bomba 1 desliga
    y_don2 = float(d_on2)                 # nível onde bomba 2 liga (emergência)
    y_doff2 = float(d_off2)               # nível onde bomba 2 desliga
    y_overflow = float(d_overflow)        # = -dist_borda_cm

    # Nível atual da água
    if latest_distance is not None:
        y_agua = float(latest_distance)   # leitura do sensor = distância atual
    else:
        y_agua = y_doff  # fallback: nível de desligamento da bomba

    # Total de altura representada
    y_top = y_borda - 10          # margem acima da borda
    y_bottom = y_fundo + 10       # margem abaixo do fundo

    # Largura simbólica do poço
    w = 1.0       # largura total
    wwall = 0.08  # espessura da parede

    fig_diag = go.Figure()

    # ── Fundo do poço ──
    fig_diag.add_shape(type="rect",
        x0=-w/2, x1=w/2, y0=y_fundo, y1=y_fundo + 8,
        fillcolor="#795548", line_color="#4e342e", line_width=2)

    # ── Paredes esquerda e direita (do fundo até a borda) ──
    for xi0, xi1 in [(-w/2 - wwall, -w/2), (w/2, w/2 + wwall)]:
        fig_diag.add_shape(type="rect",
            x0=xi0, x1=xi1, y0=y_borda, y1=y_fundo + 8,
            fillcolor="#9e9e9e", line_color="#616161", line_width=1.5)

    # ── Zona de transbordo (acima do sensor até a borda) ──
    fig_diag.add_shape(type="rect",
        x0=-w/2, x1=w/2, y0=y_borda, y1=0,
        fillcolor="rgba(231,76,60,0.10)", line_width=0)

    # ── Zona da bomba 1 ON ──
    fig_diag.add_shape(type="rect",
        x0=-w/2, x1=w/2, y0=y_don, y1=y_doff,
        fillcolor="rgba(52,152,219,0.12)", line_width=0)

    # ── Zona da bomba 2 ON (emergência) ──
    if tem_bomba2:
        fig_diag.add_shape(type="rect",
            x0=-w/2, x1=w/2, y0=y_don2, y1=y_don,
            fillcolor="rgba(230,126,34,0.15)", line_width=0)

    # ── Água atual ──
    if y_agua <= y_fundo:
        fig_diag.add_shape(type="rect",
            x0=-w/2 + 0.01, x1=w/2 - 0.01,
            y0=y_agua, y1=y_fundo,
            fillcolor="rgba(30,144,255,0.35)",
            line_color="rgba(30,144,255,0.6)", line_width=1)

    # ── Linha do sensor ──
    fig_diag.add_shape(type="line",
        x0=-0.15, x1=0.15, y0=0, y1=0,
        line=dict(color="#ff9800", width=3))
    fig_diag.add_shape(type="line",
        x0=0, x1=0, y0=-15, y1=0,
        line=dict(color="#ff9800", width=2, dash="dot"))

    # ── Linha de borda ──
    fig_diag.add_shape(type="line",
        x0=-w/2 - wwall, x1=w/2 + wwall, y0=y_borda, y1=y_borda,
        line=dict(color="darkred", width=2.5, dash="dot"))

    # ── Linhas de liga/desliga bomba ──
    diag_lines = [(y_don, "Bóia 1 LIGA", "#e74c3c"), (y_doff, "Bóia 1 DESLIGA", "#27ae60")]
    if tem_bomba2:
        diag_lines.extend([(y_don2, "Bóia 2 LIGA (Emergência)", "#d35400"), (y_doff2, "Bóia 2 DESLIGA", "#16a085")])

    for y_lv, lbl, clr in diag_lines:
        fig_diag.add_shape(type="line",
            x0=-w/2, x1=w/2, y0=y_lv, y1=y_lv,
            line=dict(color=clr, width=1.5, dash="dash"))

    # ── Linha do nível atual da água ──
    fig_diag.add_shape(type="line",
        x0=-w/2 + 0.01, x1=w/2 - 0.01, y0=y_agua, y1=y_agua,
        line=dict(color="#1e90ff", width=2.5))

    # ── Anotações ──
    ann_x = w/2 + wwall + 0.08
    annotations = [
        dict(x=ann_x, y=y_borda, text=f"<b>Borda do Poço</b><br>+{dist_borda_cm:.0f} cm acima do sensor",
             xanchor="left", showarrow=True, ax=30, ay=0,
             font=dict(color="darkred", size=11),
             arrowcolor="darkred", arrowwidth=1.5),
        dict(x=ann_x, y=0, text="<b>Sensor</b> (referência = 0 cm)",
             xanchor="left", showarrow=True, ax=30, ay=0,
             font=dict(color="#e67e22", size=11),
             arrowcolor="#e67e22", arrowwidth=1.5),
        dict(x=ann_x, y=y_agua,
             text=f"<b>Nível atual</b><br>{y_agua:.0f} cm do sensor<br>Folga até borda: {y_agua + dist_borda_cm:.0f} cm",
             xanchor="left", showarrow=True, ax=30, ay=0,
             font=dict(color="#1e90ff", size=11),
             arrowcolor="#1e90ff", arrowwidth=1.5),
        dict(x=-ann_x, y=y_don,
             text=f"Bóia 1 LIGA<br>{y_don:.0f} cm",
             xanchor="right", showarrow=True, ax=-30, ay=0,
             font=dict(color="#e74c3c", size=10),
             arrowcolor="#e74c3c", arrowwidth=1.2),
        dict(x=-ann_x, y=y_doff,
             text=f"Bóia 1 DESLIGA<br>{y_doff:.0f} cm",
             xanchor="right", showarrow=True, ax=-30, ay=0,
             font=dict(color="#27ae60", size=10),
             arrowcolor="#27ae60", arrowwidth=1.2),
        dict(x=-ann_x, y=y_fundo,
             text=f"Fundo do poço<br>{y_fundo:.0f} cm",
             xanchor="right", showarrow=True, ax=-30, ay=0,
             font=dict(color="#795548", size=10),
             arrowcolor="#795548", arrowwidth=1.2),
    ]
    if tem_bomba2:
        annotations.append(
            dict(x=-ann_x, y=y_don2,
                 text=f"Bóia 2 LIGA (Emergência)<br>{y_don2:.0f} cm",
                 xanchor="right", showarrow=True, ax=-30, ay=0,
                 font=dict(color="#d35400", size=10),
                 arrowcolor="#d35400", arrowwidth=1.2)
        )
        annotations.append(
            dict(x=-ann_x, y=y_doff2,
                 text=f"Bóia 2 DESLIGA<br>{y_doff2:.0f} cm",
                 xanchor="right", showarrow=True, ax=-30, ay=0,
                 font=dict(color="#16a085", size=10),
                 arrowcolor="#16a085", arrowwidth=1.2)
        )

    diag_tickvals = [y_borda, 0, y_don, y_doff, y_fundo]
    diag_ticktext = [f"Borda ({y_borda:.0f})", "Sensor (0)", f"Bóia 1 Liga ({y_don:.0f})", f"Bóia 1 Desliga ({y_doff:.0f})", f"Fundo ({y_fundo:.0f})"]
    if tem_bomba2:
        diag_tickvals.extend([y_don2, y_doff2])
        diag_ticktext.extend([f"Bóia 2 Liga ({y_don2:.0f})", f"Bóia 2 Desliga ({y_doff2:.0f})"])

    fig_diag.update_layout(
        annotations=annotations,
        xaxis=dict(
            range=[-w/2 - wwall - 0.45, w/2 + wwall + 0.55],
            showticklabels=False, showgrid=False, zeroline=False,
        ),
        yaxis=dict(
            autorange="reversed",
            title="Distância do Sensor (cm)  [↓ positivo = abaixo; negativo = acima]",
            tickmode="array",
            tickvals=diag_tickvals,
            ticktext=diag_ticktext,
            range=[y_bottom, y_top],
            gridcolor="rgba(0,0,0,0.06)",
        ),
        height=680,
        margin=dict(l=30, r=200, t=40, b=20),
        showlegend=False,
        plot_bgcolor="#f8f9fa",
        paper_bgcolor="white",
    )


    # ── Legenda de cores ──
    st.plotly_chart(fig_diag, use_container_width=True)

    col_l1, col_l2, col_l3 = st.columns(3)
    col_l1.metric("Capacidade total do poço", f"{capacidade_total_cm:.0f} cm",
                  help="Fundo do poço até a borda superior")
    if latest_distance is not None:
        dist_ao_transbordo = latest_distance + dist_borda_cm
        altura_agua_cm = max(FUNDODOPOCO - latest_distance, 0.0)
        col_l2.metric("Nível da água (acima do fundo)", f"{altura_agua_cm:.0f} cm")
        col_l3.metric("📦 Folga até a borda", f"{dist_ao_transbordo:.0f} cm",
                      delta=f"{dist_ao_transbordo / capacidade_total_cm * 100:.0f}% da capacidade")
    else:
        col_l2.metric("Nível da água", "Sem leitura")
        col_l3.metric("📦 Folga até a borda", "Sem leitura")

    st.markdown("""
    **Legenda:**
    - 🟥 Zona vermelha: região acima do sensor (borda do poço) \u2014 água aqui = transbordamento
    - 🟦 Zona azul: faixa de operação da bomba (liga a {y_don:.0f} cm, desliga a {y_doff:.0f} cm)
    - 🟦 Água atual: nível medido pelo sensor
    - 🟠 Sensor: ponto de referência zero (distância = 0)
    """.format(y_don=y_don, y_doff=y_doff))

# ────────────────────────────────────────────────────────────────────────────────
# TAB 9 – RELATÓRIO DE ADEQUAÇÃO DA BOMBA
# ────────────────────────────────────────────────────────────────────────────────
with tab9:
    import datetime as _dt
    try:
        from zoneinfo import ZoneInfo
        _tz_br = ZoneInfo("America/Sao_Paulo")
    except Exception:
        _tz_br = _dt.timezone(_dt.timedelta(hours=-3))
    _now_br = _dt.datetime.now(_tz_br)

    st.subheader("📋 Relatório de Adequação da Bomba de Drenagem")
    st.caption(
        "Relatório técnico automático baseado nos parâmetros do sistema, Curvas IDF de Gumbel da "
        "Estação Pluviométrica Ponte Adolfo Konder (Blumenau) e normas ABNT NBR 10844. Atualizado a cada re-execução."
    )

    # ── Parâmetros derivados ────────────────────────────────────────────────────
    area_poco_m2 = round(100.0 / fator_m3h_para_cmh, 2)
    area_drenagem_m2 = round(factor_mm_cm * area_poco_m2 / 0.1, 1)
    volume_buffer_L = round(dist_borda_cm * 0.01 * area_poco_m2 * 1000, 0)

    # ── Buscar dados históricos (cache compartilhado) ───────────────────────────
    _dc_rep = fetch_defesa_civil_rankings()
    _oldest_rep = "2015-01-08"
    if _dc_rep:
        _all_d = []
        for _dfr in _dc_rep.values():
            for _rr in _dfr["Data/Hora"]:
                try:
                    _all_d.append(pd.to_datetime(_rr, dayfirst=True))
                except Exception:
                    pass
        if _all_d:
            _oldest_rep = min(_all_d).strftime("%Y-%m-%d")

    with st.spinner("Carregando dados históricos para o relatório…"):
        rep_hist_df, _rep_err = fetch_historical_5years_precip(
            latitude, longitude, start_date_str=_oldest_rep
        )

    if not rep_hist_df.empty and era5_correction != 1.0:
        rep_hist_df = rep_hist_df.copy()
        rep_hist_df["precipitation"] = rep_hist_df["precipitation"] * era5_correction

    # ── Obter dados IDF da Estação Ponte Adolfo Konder (Medições Locais Reais) ──
    _konder_rep_df = load_adolfo_konder_dataset()
    _fonte_idf_rep = "Estação Pluviométrica Ponte Adolfo Konder (Medições Locais Reais - Blumenau 2021–2025)"
    _usou_konder_rep = False

    _DURACOES = {"1h": 1, "2h": 2, "3h": 3, "6h": 6, "12h": 12, "24h": 24}
    _DURACOES_K = {"1h": 4, "2h": 8, "3h": 12, "6h": 24, "12h": 48, "24h": 96}
    _TRS = [2, 5, 10, 25, 50, 100]
    _TR_NORMA = 25

    idf_rep = {}    # {label: {tr: intensity_mmh}}

    if not _konder_rep_df.empty:
        _usou_konder_rep = True
        for _lbl, _n15 in _DURACOES_K.items():
            _roll = _konder_rep_df["precip_15min"].rolling(_n15, min_periods=1).sum()
            _nh = _n15 / 4.0
            _intens = _roll / _nh
            _ann = _intens.groupby(_intens.index.year).max().dropna()
            if len(_ann) >= 2:
                _a, _u = fit_gumbel(_ann.values)
                idf_rep[_lbl] = {tr: round(gumbel_quantile(_a, _u, tr), 1) for tr in _TRS}

    if not idf_rep:
        _fonte_idf_rep = f"Modelo ERA5 (Open-Meteo ×{era5_correction:.2f})"
        if not rep_hist_df.empty:
            for _lbl, _nh in {"1h": 1, "2h": 2, "3h": 3, "6h": 6, "12h": 12, "24h": 24}.items():
                _roll = rep_hist_df["precipitation"].rolling(_nh).sum()
                _intens = _roll / _nh
                _ann = _intens.groupby(_intens.index.year).max().dropna()
                if len(_ann) >= 3:
                    _a, _u = fit_gumbel(_ann.values)
                    idf_rep[_lbl] = {tr: round(gumbel_quantile(_a, _u, tr), 1) for tr in _TRS}

    # ── Análise de adequação (referência: 1h) ──────────────────────────────────
    _dur_ref = "1h"
    adequacy_rep = {}
    if _dur_ref in idf_rep:
        for tr in _TRS:
            q_mmh = idf_rep[_dur_ref][tr]
            q_cmh = round(q_mmh * factor_mm_cm, 1)
            margem = round((r_pump_total_param - q_cmh) / q_cmh * 100, 1)
            adequacy_rep[tr] = {
                "q_mmh": q_mmh, "q_cmh": q_cmh,
                "pump_cmh": round(r_pump_total_param, 1),
                "adequate": bool(r_pump_total_param >= q_cmh),
                "margem_pct": margem,
            }

    vered = adequacy_rep.get(_TR_NORMA, {})
    _adeq_val = vered.get("adequate", None)
    bomba_ok = bool(_adeq_val) if _adeq_val is not None else None

    # ── CABEÇALHO ───────────────────────────────────────────────────────────────
    st.markdown("---")
    col_tit, col_badge = st.columns([3, 1])
    with col_tit:
        st.markdown("## Relatório de Adequação Hidráulica")
        st.markdown(
            f"**Local:** lat {latitude:.4f}, lon {longitude:.4f} &nbsp;|&nbsp; "
            f"**Emitido:** {_now_br.strftime('%d/%m/%Y %H:%M')} (Horário de Brasília)"
        )
    with col_badge:
        if bomba_ok is True:
            st.success("BOMBA ADEQUADA\n\nTr 25a – NBR 10844")
        elif bomba_ok is False:
            st.error("BOMBA INSUFICIENTE\n\nTr 25a – NBR 10844")
        else:
            st.warning("DADOS INSUFICIENTES")

    # ── Diagnóstico automático quando dados insuficientes ───────────────────────
    if bomba_ok is None:
        with st.expander("🔍 Diagnóstico — por que os dados são insuficientes?", expanded=True):
            st.markdown("**Verificando cada etapa do pipeline de dados:**")

            # 1. Fetch histórico
            if rep_hist_df.empty:
                st.error(f"❌ **Dados históricos ERA5 não carregados.**  \nErro retornado: `{_rep_err}`")
                st.markdown(
                    "**Possíveis causas:**\n"
                    "- Sem conexão com a internet\n"
                    "- API Open-Meteo Archive temporariamente indisponível\n"
                    "- Coordenadas inválidas (lat/lon fora do range aceito pela API)\n\n"
                    f"**Parâmetros enviados:** lat={latitude}, lon={longitude}, start={_oldest_rep}"
                )
            else:
                _anos_diag = sorted(rep_hist_df.index.year.unique())
                _n_rows = len(rep_hist_df)
                st.success(
                    f"✅ **Dados ERA5 carregados:** {_n_rows:,} horas | "
                    f"Período: {_anos_diag[0]}–{_anos_diag[-1]} | "
                    f"Anos disponíveis: {len(_anos_diag)}"
                )

            # 2. Data de início (Defesa Civil)
            if _dc_rep:
                st.success(f"✅ **Defesa Civil:** dados carregados. Data mais antiga encontrada: `{_oldest_rep}`")
            else:
                st.warning(
                    f"⚠️ **Defesa Civil:** sem dados (scraping falhou ou cache ausente). "
                    f"Usando data de início padrão: `{_oldest_rep}`"
                )

            # 3. IDF por duração
            st.markdown("**Status da curva IDF por duração:**")
            _idf_diag_rows = []
            if not rep_hist_df.empty:
                for _lbl2, _nh2 in _DURACOES.items():
                    _roll2 = rep_hist_df["precipitation"].rolling(_nh2).sum()
                    _intens2 = _roll2 / _nh2
                    _ann2 = _intens2.groupby(_intens2.index.year).max().dropna()
                    _ok = len(_ann2) >= 3
                    _idf_diag_rows.append({
                        "Duração": _lbl2,
                        "Anos com máxima anual": len(_ann2),
                        "Mín. necessário": 3,
                        "IDF calculada": "✅ Sim" if _ok else "❌ Não (anos insuficientes)",
                        "Máx. observada (mm/h)": f"{_ann2.max():.1f}" if not _ann2.empty else "—",
                    })
            else:
                for _lbl2 in _DURACOES:
                    _idf_diag_rows.append({
                        "Duração": _lbl2,
                        "Anos com máxima anual": 0,
                        "Mín. necessário": 3,
                        "IDF calculada": "❌ Sem dados ERA5",
                        "Máx. observada (mm/h)": "—",
                    })
            st.dataframe(
                pd.DataFrame(_idf_diag_rows).set_index("Duração"),
                use_container_width=True,
            )

            # 4. Adequação
            if _dur_ref in idf_rep:
                st.success(f"✅ **IDF para duração de referência ({_dur_ref})** calculada. Análise de adequação disponível.")
            else:
                st.error(
                    f"❌ **IDF para duração de referência ({_dur_ref}) não disponível.** "
                    f"A análise de adequação requer pelo menos 3 anos de dados históricos "
                    f"com a janela de {_dur_ref}. Durações calculadas: `{list(idf_rep.keys()) or 'nenhuma'}`"
                )

            # 5. Resumo e sugestões de ação
            st.markdown("**Sugestões para resolver:**")
            if rep_hist_df.empty:
                st.markdown(
                    "1. Verifique a conexão com internet e recarregue a página\n"
                    "2. Confirme que as coordenadas estão corretas no sidebar\n"
                    "3. Tente ajustar a data de início da análise (aba Histórico & IDF)"
                )
            elif len(_anos_diag) < 3:
                st.markdown(
                    f"1. Apenas {len(_anos_diag)} ano(s) de dados disponíveis — "
                    f"são necessários pelo menos 3\n"
                    "2. Aumente o período de análise na aba **Histórico & IDF**\n"
                    "3. Se a API retornar dados muito recentes, aguarde alguns dias para "
                    "o período se completar"
                )
            else:
                st.markdown(
                    "Os dados foram carregados mas a análise de adequação falhou. "
                    "Verifique se `fit_gumbel` recebeu valores válidos (sem NaN)."
                )

    st.markdown("---")

    # -- SEÇÃO 1: PARÂMETROS ----------------------------------------------------------------
    st.markdown("### 1. Parâmetros do Sistema Instalado")
    st.markdown(
        """
        Esta seção descreve o que foi instalado no local e é a base de comparação com
        o que as normas exigem. Antes de qualquer cálculo, precisamos saber exatamente
        com o que estamos trabalhando: qual é a capacidade real da bomba, qual é o tamanho
        físico do poço e quanta área de telhado/terra contribui com chuva para esse poço.
        """
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Capacidade Instalada",
        f"{vazao_total_m3h:.1f} m³/h",
        help=f"1ª Bomba: {vazao_bomba_m3h:.1f} m³/h" + (f" + 2ª Bomba: {vazao_bomba_m3h:.1f} m³/h (2x em paralelo)" if tem_bomba2 else " (1 bomba isolada)")
    )
    c2.metric("Velocidade de Rebaixamento", f"{r_pump_total_param:.1f} cm/h",
              help="Taxa máxima de rebaixamento do nível do poço com todas as bombas operando.")
    c3.metric("Seção Transversal do Poço", f"{area_poco_m2:.2f} m²",
              help="Área da abertura interna do poço. Poços maiores sobem mais devagar sob chuva igual.")
    c4.metric("Área de Drenagem Estimada", f"{area_drenagem_m2:.0f} m²",
              help=(
                  "Estimação da área total (telhados, pisos, jardins) cujas águas pluviais "
                  "são direcionadas para este poço. Calculado pelo fator de amplificação calibrado."
              ))

    _t_esvaziar = round(FUNDODOPOCO / r_pump_total_param, 1)
    _vol_1mm = round(area_drenagem_m2 * 0.001 * 1000, 0)
    _bomba2_rows = (
        f"| Bóia 2 LIGA quando distância | **≤ {d_on2:.0f} cm** | Emergência: aciona 2ª bomba em paralelo (+30 cm) |\n"
        f"| Bóia 2 DESLIGA quando distância | **≥ {d_off2:.0f} cm** | Desliga a 2ª bomba quando o nível baixa |\n"
    ) if tem_bomba2 else ""

    st.markdown(
        f"""
**O que esses números significam na prática?**

- A capacidade total instalada de bombeamento é de **{r_pump_total_param:.1f} cm/h** ({vazao_total_m3h:.1f} m³/h).
  Isso significa: se a chuva parar agora, o sistema leva cerca de **{_t_esvaziar} horas**
  para esvaziar completamente o poço a partir do nível máximo.
- A área de drenagem de **{area_drenagem_m2:.0f} m²** significa que cada 1 mm de chuva coloca
  aproximadamente **{_vol_1mm:.0f} litros** de água dentro do poço.
- O poço tem capacidade total de **{capacidade_total_cm:.0f} cm** de coluna d'água:
  {FUNDODOPOCO} cm do sensor até o fundo + {dist_borda_cm:.0f} cm do sensor até a borda superior.

💡 **Nota Técnica — Por que a área/volume equivalente é maior que a seção do cilindro de concreto?**
> Embora a camisa cilíndrica de concreto do poço tenha um diâmetro físico de **1,20 m** (seção transversal de **1,13 m²**), o poço não opera de forma isolada. As galerias e tubulações de drenagem que deságuam nele estão instaladas em diferentes cotas com inclinação suave (semi-horizontais).
>
> Quando o nível de água sobe no poço, ocorre o **efeito de remanso (backwater effect)**: a água retrocede e invade a rede de tubulações a montante. Para subir cada 1 cm no sensor do poço, a chuva precisa preencher não apenas o tubo vertical do poço, mas também metros de galerias conectadas (*Pipe Storage*). Esse volume em rede funciona como um **reservatório de amortecimento físico**, fazendo com que a seção acumuladora equivalente de água se comporte como uma área significativamente maior do que a camisa cilíndrica de 1,20 m.

| Parâmetro | Valor | Interpretação |
|---|---|---|
| Profundidade sensor → fundo | **{FUNDODOPOCO} cm** | Reservatório principal de água |
| Sensor → borda superior | **{dist_borda_cm:.0f} cm** | Margem de segurança extra antes do transbordamento |
| Capacidade total do poço | **{capacidade_total_cm:.0f} cm** | Volume total que o poço suporta |
| Volume de segurança acima do sensor | **{volume_buffer_L:.0f} L** | Reserva que ganha tempo antes do transbordo |
| Bóia 1 LIGA quando distância | **≤ {d_on:.0f} cm** | Quando a água sobe e aciona a 1ª bomba |
| Bóia 1 DESLIGA quando distância | **≥ {d_off:.0f} cm** | Quando o poço esvazia até desligar a 1ª bomba |
{_bomba2_rows}| Fator correção ERA5 aplicado | **×{era5_correction:.2f}** | Ajuste dos dados climáticos (ver Seção 2) |
        """
    )


    # -- SEÇÃO 2: BASE DE DADOS -----------------------------------------------------------------
    st.markdown("### 2. Base de Dados Pluviométrica Utilizada")
    st.markdown(
        f"""
Para saber se a bomba é adequada, precisamos saber com qual chuva ela precisa lidar.
Usamos dados históricos reais de precipitação ao longo de vários anos. Essas informações
vêm de duas fontes complementares:

- **Estação Pluviométrica Ponte Adolfo Konder (Medições Locais Reais 2021–2025):** pluviômetro físico
  instalado no centro de Blumenau que registra chuvas continuamente em intervalos de 15 minutos. É a nossa
  **fonte primária para o cálculo da Curva IDF e Chuva de Projeto**, por capturar com máxima fidelidade os picos
  reais de tempestades locais.

- **Open-Meteo ERA5 (reanálise do ECMWF):** modelo climático global utilizado como base secundária e para histórico
  estendido de longo prazo.

O **fator de correção ×{era5_correction:.2f}** aplicado ao ERA5 significa que os picos reais observados
pela Defesa Civil são em média {(era5_correction-1)*100:.0f}% maiores do que o ERA5 registra.
Sem essa correção, estaremos projetando para uma chuva menor do que a real — o que
levaria a uma avaliação otimista demais sobre a adequação da bomba.
        """
    )
    if not rep_hist_df.empty:
        _anos = sorted(rep_hist_df.index.year.unique())
        _prec_med = rep_hist_df["precipitation"].sum() / len(_anos)
        _max_h = rep_hist_df["precipitation"].max()
        ca, cb, cc, cd = st.columns(4)
        ca.metric("Período analisado", f"{_anos[0]}–{_anos[-1]}")
        cb.metric("Anos de dados", str(len(_anos)))
        cc.metric("Precipitação média anual", f"{_prec_med:.0f} mm",
                  help="Média de chuva por ano no período analisado. Blumenau é uma das cidades mais chuvosas do Brasil.")
        cd.metric("Máx. horária ERA5 (corrigida)", f"{_max_h:.1f} mm/h",
                  help="A hora mais chuvosa registrada no período completo após correção.")
        st.caption(f"Fonte: Open-Meteo Archive API (ERA5) com fator ×{era5_correction:.2f} | Dados desde: {_oldest_rep}")
        if _dc_rep and "01h" in _dc_rep and "1h" in idf_rep:
            _dc_max = _dc_rep["01h"]["Acumulado_mm"].max()
            _era_tr100 = idf_rep["1h"].get(100, None)
            if _era_tr100:
                _ratio = _dc_max / _era_tr100 * 100
                st.info(
                    f"Validação cruzada: o maior evento de 1h registrado pela Defesa Civil foi de "
                    f"**{_dc_max:.1f} mm/h**. A curva IDF do ERA5 (corrigida) estima que chuvas assim "
                    f"correspondem a Tr ≈ 100 anos ({_era_tr100:.1f} mm/h no ERA5). "
                    f"Relação DC/ERA5-Tr100 = **{_ratio:.0f}%** — "
                    + (
                        "valores próximos de 100% indicam boa consistência entre as fontes."
                        if 80 <= _ratio <= 120
                        else f"diferença relevante — o fator de correção ×{era5_correction:.2f} tenta compensar isso."
                    )
                )
    else:
        st.warning(f"Dados históricos não disponíveis. Erro: {_rep_err}")

    # -- SEÇÃO 3: CURVA IDF -----------------------------------------------------------------------
    st.markdown("### 3. Intensidades de Projeto – Curva IDF")
    st.markdown(
        """
**O que é a curva IDF?**

IDF significa **Intensidade – Duração – Frequência**. É a ferramenta fundamental do projeto
hidrológico: ela responde à pergunta *"com que intensidade pode chover, por quanto tempo,
e com que frequência isso ocorre?"*

**O que é período de retorno (Tr)?**

Tr = 25 anos significa que aquele evento de chuva tem **probabilidade de 4% de ocorrer
em qualquer ano**. Não significa que vai chover exatamente assim a cada 25 anos exatos
— pode ocorrer dois anos seguidos, ou não ocorrer por 40 anos. É uma forma estatística
de descrever a *raridade* do evento. Quanto maior o Tr, mais rara é a chuva.

**Como foram calculados os valores?**

Para cada duração de chuva (1h, 2h, etc.), identificamos o maior evento registrado
em cada ano do histórico. Com esses valores extremos anuais, ajustamos uma
*distribuição de Gumbel* — modelo estatístico especificamente desenvolvido para análise
de máximos. Ele extrapola para períodos de retorno mais longos do que o período de dados
disponível, com incerteza crescente para Tr muito maiores que o histórico.

**Como usar a tabela abaixo:**

Cada célula mostra a *intensidade média* (mm/h) da chuva de projeto para aquela
combinação de duração e Tr. Por exemplo, "Tr 25a / 1h" é a intensidade de uma chuva
que dura 1 hora e tem Tr = 25 anos — o cenário que a norma NBR 10844 exige que o
sistema suporte.

A linha tracejada vermelha no gráfico indica a intensidade a partir da qual a bomba
instalada não consegue mais acompanhar o enchimento do poço (ponto de saturação).
        """
    )
    if idf_rep:
        _idf_rows = []
        for _lbl in _DURACOES:
            if _lbl not in idf_rep:
                continue
            row = {"Duração": _lbl}
            row.update({f"Tr {tr}a": f"{idf_rep[_lbl][tr]:.1f} mm/h" for tr in _TRS})
            _idf_rows.append(row)
        st.dataframe(pd.DataFrame(_idf_rows).set_index("Duração"), use_container_width=True)

        # Gráfico IDF
        fig_idf_r = go.Figure()
        _colors = px.colors.qualitative.Set2
        for _i, tr in enumerate(_TRS):
            _xv = [_DURACOES[l] for l in _DURACOES if l in idf_rep]
            _yv = [idf_rep[l][tr] for l in _DURACOES if l in idf_rep]
            fig_idf_r.add_trace(go.Scatter(
                x=_xv, y=_yv, mode="lines+markers", name=f"Tr {tr}a",
                line=dict(color=_colors[_i % len(_colors)],
                          width=3 if tr == _TR_NORMA else 1.5,
                          dash="solid" if tr == _TR_NORMA else "dot"),
                marker=dict(size=8 if tr == _TR_NORMA else 5),
            ))
        # Linha de saturação da bomba
        _sat_mmh = r_pump_param / factor_mm_cm
        fig_idf_r.add_hline(
            y=_sat_mmh, line_dash="dash", line_color="#e74c3c",
            annotation_text=f"Saturação bomba: {_sat_mmh:.1f} mm/h",
            annotation_position="top right",
            annotation_font_color="#e74c3c",
        )
        fig_idf_r.update_layout(
            title="Curva IDF – linha tracejada vermelha = limite de saturação da bomba instalada",
            xaxis_title="Duração (horas)", yaxis_title="Intensidade média (mm/h)",
            height=400,
            legend=dict(orientation="h", y=-0.28, x=0.5, xanchor="center"),
        )
        st.plotly_chart(fig_idf_r, use_container_width=True)
    else:
        st.warning("IDF não calculada — dados insuficientes.")

    # -- SEÇÃO 4: ADEQUAÇÃO -----------------------------------------------------------------------
    st.markdown("### 4. Comparação: Chuva de Projeto vs Capacidade da Bomba")
    st.markdown(
        f"""
**Como a bomba é comparada com a chuva de projeto?**

A lógica é simples: a chuva entra no poço a uma certa taxa (cm/h), e a bomba precisa
remover água a uma taxa igual ou maior para que o nível não suba continuamente.

Para converter chuva (mm/h) em taxa de enchimento do poço (cm/h), usamos o
**Fator de Amplificação** de {factor_mm_cm:.2f} calibrado em eventos reais:
cada 1 mm/h de chuva sobre a área de drenagem eleva o nível do poço em **{factor_mm_cm:.2f} cm/h**.

A *duração de referência de 1 hora* é a mais crítica para bombas de recalque:
é o tempo suficiente para saturar o poço, e é o padrão que a maioria das normas usa
para dimensionar sistemas de bombeamento de águas pluviais.

**Como ler a tabela e o gráfico:**
- Cada linha mostra: qual chuva (mm/h) ocorre naquele Tr, como isso se traduz em
  taxa de enchimento (cm/h), e se a bomba instalada consegue ou não acompanhar.
- As barras coloridas no gráfico mostram o quanto a bomba precisa trabalhar.
- A linha verde é a capacidade real da bomba instalada.
- Barras **acima** da linha verde = a bomba **não consegue acompanhar** essa chuva.
- Barras **abaixo** da linha = a bomba **dá conta** e o nível não sobe indefinidamente.
        """
    )
    if adequacy_rep:
        _adq_rows = []
        for tr in _TRS:
            a = adequacy_rep[tr]
            _adq_rows.append({
                "Tr (anos)": tr,
                "Intensidade 1h (mm/h)": a["q_mmh"],
                "Taxa de enchimento (cm/h)": a["q_cmh"],
                "Capacidade da Bomba (cm/h)": a["pump_cmh"],
                "Margem (%)": f"{a['margem_pct']:+.1f}%",
                "Resultado": "✅ Bomba dá conta" if a["adequate"] else "❌ Bomba insuficiente",
            })
        st.dataframe(pd.DataFrame(_adq_rows).set_index("Tr (anos)"), use_container_width=True)
        st.caption(
            f"Margem positiva = bomba tem folga. Margem negativa = falta capacidade. "
            f"A norma NBR 10844 exige que a bomba seja adequada para Tr = {_TR_NORMA} anos."
        )

        fig_adq_r = go.Figure()
        fig_adq_r.add_trace(go.Bar(
            x=[str(tr) for tr in _TRS],
            y=[adequacy_rep[tr]["q_cmh"] for tr in _TRS],
            name="Taxa de enchimento do poço (chuva de projeto)",
            marker_color=["#e74c3c" if not adequacy_rep[tr]["adequate"] else "#f39c12" for tr in _TRS],
        ))
        fig_adq_r.add_hline(
            y=r_pump_total_param, line_dash="dash", line_color="#27ae60",
            annotation_text=f"Bomba(s) instalada(s): {r_pump_total_param:.1f} cm/h ({vazao_total_m3h:.1f} m³/h)",
            annotation_position="top left", annotation_font_color="#27ae60",
        )
        fig_adq_r.update_layout(
            title="Barras vermelhas = bomba insuficiente | Amarelas = bomba adequada | Linha verde = capacidade instalada",
            xaxis_title="Período de Retorno (anos)",
            yaxis_title="Taxa de enchimento necessária (cm/h)",
            height=350,
        )
        st.plotly_chart(fig_adq_r, use_container_width=True)

    # -- SEÇÃO 5: BUFFER --------------------------------------------------------------------------
    st.markdown("### 5. Tempo de Amortecimento do Poço (Margem de Segurança Física)")
    st.markdown(
        f"""
**O poço como reservatório de segurança**

Mesmo que a bomba instalada não consiga remover água tão rápido quanto ela entra
durante uma chuva forte, o poço ainda tem uma margem física antes de transbordar:
são **{dist_borda_cm:.0f} cm** entre o sensor e a borda superior. Esses {dist_borda_cm:.0f} cm
funcionam como um reservatório de emergência.

O **tempo de amortecimento** é quanto tempo leva para o poço se encher completamente
com o excesso de água que a bomba não consegue remover. Isso importa porque a maioria
das chuvas intensas não sustenta sua intensidade máxima por mais de 30 a 60 minutos.
Se o poço aguentar esse pico sem transbordar, a bomba consegue esvaziar o restante
depois que a chuva diminuir.

**Como ler a tabela:**
- "Excesso não removido" = taxa de enchimento menos capacidade da bomba.
  Se for zero, a bomba dá conta e o poço nunca enche completamente naquele Tr.
- "Tempo até transbordo" = quanto tempo o poço aguenta com chuva contínua na
  intensidade máxima de projeto. Na prática, o sistema normalmente resiste mais tempo
  porque a intensidade varia ao longo do evento de chuva.
        """
    )
    if adequacy_rep:
        _buf_rows = []
        for tr in _TRS:
            a = adequacy_rep[tr]
            excesso = max(a["q_cmh"] - r_pump_total_param, 0.0)
            if excesso > 0:
                t_min = round(dist_borda_cm / excesso * 60, 0)
                nota = f"{t_min:.0f} min até transbordo"
            else:
                nota = "Sem risco — bomba suficiente para esse Tr"
            _buf_rows.append({
                "Tr (anos)": tr,
                "Excesso não removido (cm/h)": round(excesso, 1),
                "Buffer disponível (cm)": dist_borda_cm,
                "Tempo até transbordo": nota,
            })
        st.dataframe(pd.DataFrame(_buf_rows).set_index("Tr (anos)"), use_container_width=True)
        st.caption(
            "Nota: o tempo até transbordo assume chuva contínua na intensidade máxima de projeto. "
            "Cenário conservador — na prática, a intensidade varia e o sistema geralmente aguenta mais."
        )

    # -- SEÇÃO 6: NORMAS --------------------------------------------------------------------------
    st.markdown("### 6. Normas e Critérios de Projeto Aplicados")
    st.markdown(
        f"""
As normas de engenharia estabelecem o **nível mínimo de proteção** que uma instalação
deve ter, equilibrando custo e risco. Elas foram desenvolvidas por especialistas com base
em dados históricos e experiência acumulada ao longo de décadas:

- **ABNT NBR 10844:1989** — *Instalações prediais de águas pluviais*: a principal norma
  brasileira para dimensionamento de calhas, condutores e bombas de águas pluviais em
  edificações. Para uso coletivo (condomínios, comércio), exige que o sistema suporte
  chuvas com **Tr ≥ 25 anos**. Isso significa que há no máximo 4% de chance
  por ano de a chuva superar o que o sistema foi projetado para suportar.

- **ABNT NBR 5626:2020** — Norma geral para dimensionamento de sistemas prediais hidráulicos.

- **Manual de Drenagem DAEE/SP** — utilizado amplamente no Brasil para drenagem urbana;
  recomenda Tr entre 10 e 100 anos dependendo do risco (área residencial, via pública,
  área crítica, etc.).

**Por que Tr = 25 anos foi escolhido como critério principal?**

Um critério mais conservador (ex: Tr = 100 anos) seria mais seguro, mas exigiria uma bomba
muito maior e mais cara. Um critério muito brando (Tr = 2 anos) significaria transbordamento
frequente. Tr = 25 anos é o balanço estabelecido pela NBR 10844 para edificações coletivas —
um transbordamento esperado *no pior caso* a cada 25 anos de operação.

| Norma | Aplicação | Tr recomendado |
|---|---|---|
| **ABNT NBR 10844:1989** | Instalações prediais de águas pluviais | **Tr ≥ 25 anos** (uso coletivo) |
| **ABNT NBR 5626:2020** | Sistemas de água fria e quente | Dimensionamento geral |
| **Manual DAEE/SP** | Drenagem urbana | Tr 10–100 anos conforme risco |
| **ERA5 + DC Blumenau** | Dados históricos locais | Corrigido por ×{era5_correction:.2f} |
        """
    )

    # -- SEÇÃO 7: PARECER TÉCNICO FINAL -----------------------------------------------------------
    st.markdown("### 7. Parecer Técnico Final")
    if not adequacy_rep:
        st.warning("Dados insuficientes para emitir parecer.")
    else:
        _tr_max_ok = max((tr for tr in _TRS if adequacy_rep[tr]["adequate"]), default=None)
        if bomba_ok is True:
            _mg = vered.get("margem_pct", 0)
            _conf = "alta" if _mg >= 50 else ("moderada" if _mg >= 20 else "baixa — margem estreita")
            _txt_sistema = f"de 2 bombas em paralelo ({vazao_total_m3h:.1f} m³/h = {r_pump_total_param:.1f} cm/h)" if tem_bomba2 else f"da bomba instalada ({vazao_bomba_m3h:.1f} m³/h = {r_pump_param:.1f} cm/h)"
            st.success(
                f"✅ APROVADO — O sistema está dimensionado conforme a norma NBR 10844.\n\n"
                f"A capacidade do sistema {_txt_sistema} "
                f"consegue remover água mais rápido do que ela entra durante uma chuva de "
                f"período de retorno de **{_TR_NORMA} anos** (probabilidade de 4% ao ano). "
                f"A margem de segurança é de **{_mg:+.1f}%** — confiabilidade **{_conf}**."
            )
            if _mg < 20:
                st.warning(
                    f"Atenção: apesar de aprovada, a margem de {_mg:.1f}% é estreita. "
                    f"Qualquer redução de vazão por desgaste da bomba, entupimento das grades "
                    f"ou aumento da área de drenagem pode tornar o sistema insuficiente. "
                    f"Recomenda-se monitoramento periódico e medição da vazão real da bomba."
                )
        else:
            _def = abs(vered.get("margem_pct", 0))
            _q_needed = vered.get("q_cmh", r_pump_total_param * 1.3) / fator_m3h_para_cmh
            _q_norma_cmh = vered.get("q_cmh", r_pump_total_param * 1.3)
            _txt_sistema = f"de 2 bombas em paralelo ({vazao_total_m3h:.1f} m³/h = {r_pump_total_param:.1f} cm/h)" if tem_bomba2 else f"da bomba instalada ({vazao_bomba_m3h:.1f} m³/h = {r_pump_param:.1f} cm/h)"
            st.error(
                f"❌ REPROVADO — O sistema está subdimensionado para a norma NBR 10844.\n\n"
                f"A capacidade do sistema {_txt_sistema} "
                f"NÃO CONSEGUE remover água na mesma velocidade que ela entra durante "
                f"uma chuva de Tr = {_TR_NORMA} anos. O sistema está com **{_def:.1f}%** menos "
                f"capacidade do que o necessário segundo a norma. "
                + (
                    f"O sistema atual só é suficiente para chuvas de até Tr = **{_tr_max_ok} anos** "
                    f"(probabilidade de {100//_tr_max_ok}% ao ano). "
                    if _tr_max_ok else ""
                )
                + f"Para cumprir a norma, seria necessária uma capacidade total de pelo menos **{_q_needed:.1f} m³/h**."
            )
            # Explicar o que acontece na pratica
            _excesso_norma = _q_norma_cmh - r_pump_total_param
            if _excesso_norma > 0 and dist_borda_cm > 0:
                _t_buf = dist_borda_cm / _excesso_norma * 60
                st.info(
                    f"📌 O que acontece durante uma chuva de Tr = {_TR_NORMA} anos:\n\n"
                    f"A água entra no poço a **{_q_norma_cmh:.1f} cm/h** mas as bombas só conseguem "
                    f"remover **{r_pump_total_param:.1f} cm/h**. A diferença de **{_excesso_norma:.1f} cm/h** "
                    f"faz o nível subir mesmo com a bomba ligada. Com os **{dist_borda_cm:.0f} cm** de "
                    f"buffer disponível, o poço levaria cerca de **{_t_buf:.0f} minutos** para transbordar "
                    f"(assumindo chuva contínua na intensidade máxima — o que raramente ocorre na prática, mas é teoricamente possível e por isso a norma exige esse cálculo)."
                )

        # Recomendações
        st.markdown("#### Recomendações")
        _recs = []
        if bomba_ok is False:
            _q_min = vered.get("q_cmh", r_pump_total_param * 1.3) / fator_m3h_para_cmh
            if not tem_bomba2:
                _recs.append(
                    f"**Instalar 2ª bomba em paralelo:** a capacidade necessária para a NBR 10844 (Tr 25a) "
                    f"é de **{_q_min:.1f} m³/h**. Adicionar uma 2ª bomba idêntica de {vazao_bomba_m3h:.1f} m³/h "
                    f"em paralelo eleva a capacidade total para {vazao_bomba_m3h*2:.1f} m³/h."
                )
            else:
                _recs.append(
                    f"**Aumentar capacidade dos conjuntos motobomba:** mesmo com 2 bombas em paralelo "
                    f"({vazao_total_m3h:.1f} m³/h), a NBR 10844 exige **{_q_min:.1f} m³/h**. "
                    f"Recomenda-se substituir os conjuntos por bombas de maior vazão unitária."
                )
        if dist_borda_cm < 50:
            _recs.append(
                f"**Ampliar a margem de segurança física:** a distância atual do sensor à borda é de apenas "
                f"{dist_borda_cm:.0f} cm. Aumentar para pelo menos 80 cm (rebaixando o sensor ou elevando "
                f"a borda do poço) dá mais tempo para o sistema agir antes de transbordar — especialmente "
                f"útil durante picos de chuva quando a bomba não dá conta."
            )
        if era5_correction > 1.5:
            _recs.append(
                f"**Instalar pluviômetro local:** o fator de correção ERA5 de ×{era5_correction:.2f} indica "
                f"que os dados de satélite subestimam significativamente a chuva neste local. "
                f"Um pluviômetro próprio forneceria dados muito mais precisos para calibração "
                f"do sistema e do fator de amplificação."
            )
        _recs.append(
            "**Prever grupo gerador ou nobreak:** eventos de chuva forte frequentemente causam "
            "queda de energia elétrica. Sem energia, a bomba para e o poço pode transbordar muito "
            "mais rápido. Um no-break ou gerador garante operação contínua nos momentos mais críticos."
        )
        _recs.append(
            "**Manutenção semestral obrigatória:** ao longo do tempo, a vazão real da bomba "
            "diminui por desgaste, incrustações e sedimentos. É essencial medir a vazão real "
            "semestralmente, limpar as grades de entrada e verificar as bóias. Uma bomba envelhecida "
            "pode estar entregando apenas 70% da vazão nominal na prática."
        )
        for _r in _recs:
            st.markdown(f"- {_r}")

    # -- DOWNLOAD & IMPRESSÃO ----------------------------------------------------------------------
    st.markdown("---")
    col_dl1, col_dl2 = st.columns(2)
    with col_dl1:
        _rpt_txt = (
            f"RELATORIO DE ADEQUACAO - POCO DE DRENAGEM\n"
            f"Data: {_now_br.strftime('%d/%m/%Y %H:%M')} (Horario de Brasilia)\n"
            f"Local: lat {latitude:.4f}, lon {longitude:.4f}\n\n"
            f"SISTEMA INSTALADO\n"
            f"  Bomba: {vazao_bomba_m3h:.1f} m3/h = {r_pump_param:.1f} cm/h\n"
            f"  Secao do poco: {area_poco_m2:.2f} m2\n"
            f"  Area de drenagem estimada: {area_drenagem_m2:.0f} m2\n"
            f"  Capacidade total: {capacidade_total_cm:.0f} cm\n\n"
            f"CRITERIO DE PROJETO\n"
            f"  Norma: ABNT NBR 10844:1989 | Tr = {_TR_NORMA} anos | Duracao ref.: 1h\n\n"
            f"RESULTADO\n"
            f"  {'APROVADO' if bomba_ok else 'REPROVADO'}\n"
            f"  Margem Tr 25a: {vered.get('margem_pct', 'N/A')}%\n"
        )
        st.download_button(
            "⬇️ Baixar Resumo do Relatório (.txt)",
            data=_rpt_txt.encode("utf-8"),
            file_name=f"relatorio_poco_{_dt.date.today().isoformat()}.txt",
            mime="text/plain",
        )

    with col_dl2:
        with st.expander("🖨️ Visualizar Documento para Impressão / PDF", expanded=False):
            _txt_bom = f"{vazao_total_m3h:.1f} m³/h ({'2 bombas em paralelo' if tem_bomba2 else '1 bomba isolada'})"
            _bg_card = "#e8f5e9" if bomba_ok else "#ffebee"
            _bd_card = "#2e7d32" if bomba_ok else "#c62828"
            _status_txt = "SISTEMA APROVADO (NBR 10844)" if bomba_ok else "SISTEMA REPROVADO (SUBDIMENSIONADO)"
            _expl_txt = (
                "O sistema instalado cumpre integralmente as exigências de remoção de vazão para o período de retorno Tr = 25 anos exigido pela NBR 10844."
                if bomba_ok else
                f"A capacidade instalada ({r_pump_total_param:.1f} cm/h) não atinge a taxa de afluxo requerida pela NBR 10844 (Tr 25a: {vered.get('q_cmh', 0):.1f} cm/h)."
            )
            st.markdown(
                f"""
                <div style="background-color: white; padding: 20px; border: 1px solid #ccc; border-radius: 8px; color: #222;">
                    <div style="text-align: center; border-bottom: 2px solid #0056b3; padding-bottom: 8px; margin-bottom: 15px;">
                        <h3 style="margin: 0; color: #0056b3;">PARECER TÉCNICO DE ADEQUAÇÃO HIDRÁULICA</h3>
                        <p style="margin: 3px 0 0 0; color: #555; font-size: 13px;">CONDOMÍNIO RESIDENCIAL — POÇO DE DRENAGEM PLUVIAL</p>
                        <p style="margin: 2px 0 0 0; color: #777; font-size: 11px;">Emissão: {_now_br.strftime('%d/%m/%Y às %H:%M')} (Horário de Brasília)</p>
                    </div>
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 15px; font-size: 13px;">
                        <tr style="background-color: #f8f9fa;">
                            <td style="padding: 6px; border: 1px solid #ddd;"><b>Localização:</b> Lat {latitude:.4f}, Lon {longitude:.4f}</td>
                            <td style="padding: 6px; border: 1px solid #ddd;"><b>Norma Técnica:</b> ABNT NBR 10844 (Tr = 25 anos)</td>
                        </tr>
                        <tr>
                            <td style="padding: 6px; border: 1px solid #ddd;"><b>Capacidade Instalada:</b> {_txt_bom}</td>
                            <td style="padding: 6px; border: 1px solid #ddd;"><b>Vazão de Esvaziamento:</b> {r_pump_total_param:.1f} cm/h</td>
                        </tr>
                    </table>
                    <div style="background-color: {_bg_card}; border-left: 4px solid {_bd_card}; padding: 12px; margin-bottom: 15px;">
                        <h4 style="margin: 0 0 4px 0; color: {_bd_card};">{_status_txt}</h4>
                        <p style="margin: 0; font-size: 13px;">{_expl_txt}</p>
                    </div>
                    <p style="font-size: 11px; color: #888; text-align: center; margin: 10px 0 0 0;">
                        Documento emitido pelo Sistema de Monitoramento e Análise Hidráulica de Poços de Drenagem.
                    </p>
                </div>
                """,
                unsafe_allow_html=True
            )
