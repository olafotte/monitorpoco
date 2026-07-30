import os
import re
import warnings

from bs4 import BeautifulSoup
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning
import streamlit as st

FUNDODOPOCO = 150  # cm, distância do sensor à linha d'água quando o poço está seco

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

st.sidebar.subheader("Configuração da consulta")
record_limit = st.sidebar.number_input(
    "Número de registros a carregar",
    min_value=1,
    max_value=10000,
    value=300,
    step=50,
)

st.sidebar.header("⚙️ Parâmetros do Modelo (Calibrados)")
d_on = st.sidebar.number_input(
    "Nível Ativação Bomba (cm)", value=73.0, step=0.5, help="Distância quando a bomba liga (água alta)"
)
d_off = st.sidebar.number_input(
    "Nível Desativação Bomba (cm)", value=90.0, step=0.5, help="Distância quando a bomba desliga (água baixa)"
)
d_overflow = st.sidebar.number_input(
    "Nível Crítico de Transbordo (cm)", value=0.0, step=0.5, help="Distância crítica do sensor onde o poço transborda"
)
r_gnd_param = st.sidebar.number_input(
    "Taxa Lençol Freático (cm/h)", value=1.44, step=0.1
)
vazao_bomba_m3h = st.sidebar.number_input(
    "Capacidade da Bomba (m³/h)", value=14.0, step=0.5,
    help="Vazão nominal da bomba em metros cúbicos por hora"
)
fator_m3h_para_cmh = st.sidebar.number_input(
    "Fator de Conversão (cm/h por m³/h)", value=5.471, step=0.01, format="%.3f",
    help="Fator que converte m³/h em cm/h de variação no poço (depende da área da seção transversal). "
         "Calculado como: 100 / (Área do poço em m²). Ex.: bomba de 14 m³/h → 76.6 cm/h ⟹ fator ≈ 5.471"
)
r_pump_param = vazao_bomba_m3h * fator_m3h_para_cmh
st.sidebar.caption(f"↳ Vazão equivalente: **{r_pump_param:.2f} cm/h**")
factor_mm_cm = st.sidebar.number_input(
    "Fator de Amplificação (cm de poço / mm de chuva)", value=2.83, step=0.1, help="Relação calibrada na chuva de 22/07 (54mm -> 153cm no poço)"
)

st.sidebar.header("🌧️ Dados Meteorológicos (Open-Meteo ERA5)")
era5_correction = st.sidebar.number_input(
    "Fator de Correção ERA5",
    min_value=0.5, max_value=5.0, value=1.0, step=0.05, format="%.2f",
    help=(
        "Fator multiplicativo aplicado à precipitação do Open-Meteo (ERA5) antes da análise histórica. "
        "O ERA5 é uma reanalíse de grade grossa (~9 km) e tende a subestimar picos locais. "
        "Consulte a seção de Validação na aba Histórico & IDF para estimar o fator adequado para este local. "
        "Valor 1.0 = sem correção (dados brutos do ERA5)."
    ),
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
):
    vals = np.zeros(steps)
    vals[0] = start_val
    state = 0 if start_val > d_trigger_on else 1
    curr = start_val
    for i in range(1, steps - 1):
        if state == 0:
            curr -= r_in_cm_min * dt_min
            if curr <= d_trigger_on:
                state = 1
        else:
            curr += (r_pump_cm_min - r_in_cm_min) * dt_min
            if curr >= d_trigger_off:
                state = 0
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
def fetch_historical_5years_precip(lat: float, lon: float, start_date_str: str = "") -> tuple:
    """Busca dados de precipitação horária via Open-Meteo Archive API.
    Se start_date_str for fornecido, usa essa data como início; caso contrário usa 5 anos atrás.
    Retorna (DataFrame, erro_str) – erro_str é vazio em caso de sucesso."""
    # Usar datas naïve para a query (evita tz_localize extra)
    today = pd.Timestamp.now().normalize()
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


def fit_gumbel(annual_maxima: np.ndarray):
    """Ajusta distribuição de Gumbel pelo método dos momentos.
    Retorna (alpha, u) – parâmetros de escala e localização."""
    mu = np.mean(annual_maxima)
    sigma = np.std(annual_maxima, ddof=1)
    alpha = sigma * np.sqrt(6) / np.pi
    u = mu - 0.5772 * alpha
    return alpha, u


def gumbel_quantile(alpha: float, u: float, return_period: float) -> float:
    """Calcula quantil de Gumbel para um dado período de retorno Tr (anos)."""
    y_t = -np.log(-np.log(1.0 - 1.0 / return_period))
    return u + alpha * y_t


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
elif latest_distance <= 20:
    st.warning(f"⚠️ Atenção: poço em eminência de transbordamento (<= {20} cm).")
else:
    altura_agua_cm = max(FUNDODOPOCO - latest_distance, 0.0)
    st.success(f"Nível estimado da água: {altura_agua_cm:.0f} cm acima do fundo do poço.")

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
    """Faz scraping das tabelas de maiores chuvas do site da Defesa Civil de Blumenau.
    Retorna dict {duração: DataFrame} com colunas [Estação, Região, Data/Hora, Acumulado_mm]."""
    url = "https://defesacivil.blumenau.sc.gov.br/d/maiores-chuvas"
    headers_req = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    resultado = {}
    try:
        warnings.filterwarnings("ignore", category=InsecureRequestWarning)
        resp = requests.get(url, headers=headers_req, timeout=20, verify=False)
        if resp.status_code != 200:
            return {}
        soup = BeautifulSoup(resp.text, "html.parser")

        # Cada bloco é precedido por um <h3> com o título (ex: "Maiores Chuvas em 15min")
        page_headers = soup.find_all("div", class_="page-header")
        for ph in page_headers:
            h3 = ph.find("h3")
            if not h3:
                continue
            titulo_raw = h3.get_text(separator=" ", strip=True)
            # Extrair a duração do título
            match = re.search(r"em\s+([\d]+h|[\d]+min)", titulo_raw, re.IGNORECASE)
            duracao = match.group(1) if match else titulo_raw

            # Atualização
            small = h3.find("small")
            ultima_atualizacao = small.get_text(strip=True).replace("Última atualização:", "").strip() if small else ""

            # Tabela seguinte
            table = ph.find_next_sibling("table")
            if not table:
                continue
            rows_data = []
            for tr in table.find("tbody").find_all("tr"):
                cells = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(cells) == 4:
                    acumulado_str = cells[3].replace(",", ".")
                    try:
                        acumulado_float = float(acumulado_str)
                    except ValueError:
                        acumulado_float = None
                    rows_data.append({
                        "Estação": cells[0],
                        "Região": cells[1],
                        "Data/Hora": cells[2],
                        "Acumulado_mm": acumulado_float,
                    })
            if rows_data:
                df_dur = pd.DataFrame(rows_data)
                df_dur["ultima_atualizacao"] = ultima_atualizacao
                resultado[duracao] = df_dur
    except Exception:
        pass
    return resultado

tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "📊 Visualização e Imputação",
    "📐 Modelo Matemático",
    "🌧️ Simulador Pluviométrico",
    "🌦️ Chuvas",
    "🔮 Previsão do Nível",
    "📊 Histórico & IDF (5 anos)",
    "🛑 Defesa Civil Blumenau",
])

with tab1:
    st.subheader("Série Temporal do Nível do Poço")
    st.caption(
        "O eixo Y esquerdo (invertido) mostra a distância do sensor à linha d'água. "
        "O eixo Y direito mostra a precipitação acumulada em cada intervalo de 5 minutos."
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
    fig.add_hline(y=d_on, line_dash="dash", line_color="red", annotation_text="Bomba Liga (Água Alta)")
    fig.add_hline(y=d_off, line_dash="dash", line_color="green", annotation_text="Bomba Desliga (Água Baixa)")
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
    st.dataframe(filled_df)

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

    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("Intensidade da Chuva", f"{intensidade_mm_h:.1f} mm/h")
    col_m2.metric("Entrada Equivalente no Poço", f"{taxa_entrada_cm_h:.1f} cm/h")
    col_m3.metric("Capacidade da Bomba", f"{r_pump_param:.1f} cm/h")
    col_m4.metric("Limite de Saturação da Bomba", f"{intensidade_saturacao_mm_h:.1f} mm/h")

    st.markdown("---")

    sim_time_steps = np.linspace(0, max(chuva_horas * 1.5, 6.0), 360)
    dt_h = sim_time_steps[1] - sim_time_steps[0]

    levels = []
    curr_d = d_off
    p_state = False
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
            if p_state:
                curr_d -= (r_pump_param - r_in) * dt_h
                if curr_d <= d_on:
                    p_state = False
            else:
                curr_d += r_in * dt_h
                if curr_d >= d_off:
                    p_state = True

        levels.append(curr_d)
        if not overflow_occurred and curr_d <= d_overflow:
            overflow_occurred = True
            overflow_time = t

    fig_sim = go.Figure()
    fig_sim.add_trace(go.Scatter(x=sim_time_steps, y=levels, mode="lines", line=dict(color="#1f77b4")))
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
            f"🚨 **ALERTA DE TRANSBORDAMENTO:** O poço atingirá o nível crítico de transbordo ({d_overflow} cm) em **{overflow_time*60:.0f} minutos** ({overflow_time:.2f} horas) após o início da chuva!"
        )
    else:
        st.info("ℹ️ Nenhum transbordamento previsto para este cenário específico.")

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
    pump_on = curr_d5 <= d_on  # se já está com água alta, bomba começa ligada
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
        sim_pump_state.append(pump_on)

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
            if pump_on:
                curr_d5 += (r_pump_param - r_total_entrada) * dt_h_step
                if curr_d5 >= d_off:
                    pump_on = False
            else:
                curr_d5 -= r_total_entrada * dt_h_step
                if curr_d5 <= d_on:
                    pump_on = True
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
                   annotation_text="Bomba Liga", annotation_position="top right")
    fig5.add_hline(y=d_off, line_dash="dash", line_color="green",
                   annotation_text="Bomba Desliga", annotation_position="top right")
    fig5.add_hline(y=d_overflow, line_dash="dot", line_color="black",
                   annotation_text="Nível Crítico", annotation_position="top right")

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
        st.error(
            f"🚨 **ALERTA DE TRANSBORDO PREVISTO:** o poço deve atingir o nível crítico "
            f"({d_overflow} cm) em **{overflow5_time.strftime('%d/%m %H:%M')}** "
            f"({(overflow5_time - now_tz5).seconds // 3600}h "
            f"{((overflow5_time - now_tz5).seconds % 3600) // 60}min a partir de agora)."
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

    anos_disponiveis = sorted(hist5_df.index.year.unique())
    n_anos = len(anos_disponiveis)
    total_horas = len(hist5_df)

    # Aplicar fator de correção ERA5 (se diferente de 1.0)
    if era5_correction != 1.0:
        hist5_df = hist5_df.copy()
        hist5_df["precipitation"] = hist5_df["precipitation"] * era5_correction

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
        pump_idf = False
        ovf_idf = False
        ovf_idf_t = None

        for ti in t_idf:
            r_in_idf = (r_gnd_param + taxa_entrada_idf) if ti <= dur_sel else r_gnd_param
            if pump_idf:
                curr_idf += (r_pump_param - r_in_idf) * dt_h_idf
                if curr_idf >= d_off:
                    pump_idf = False
            else:
                curr_idf -= r_in_idf * dt_h_idf
                if curr_idf <= d_on:
                    pump_idf = True
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
                              annotation_text="Bomba Liga", annotation_position="top right")
        fig_idf_sim.add_hline(y=d_off, line_dash="dash", line_color="green",
                              annotation_text="Bomba Desliga", annotation_position="top right")
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

