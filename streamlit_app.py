import os

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
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


processed_df = preprocess_time_series(df)
filled_df = fill_gaps(processed_df)

# Buscar dados meteorológicos alinhados ao período do sensor
_sensor_min = filled_df["dt_round"].min()
_sensor_max = filled_df["dt_round"].max()
_weather_start = (_sensor_min - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
_weather_end = (pd.Timestamp.now(tz="America/Sao_Paulo") + pd.Timedelta(days=7)).strftime("%Y-%m-%d")

weather_df = fetch_weather_data(latitude, longitude, _weather_start, _weather_end)

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

tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 Visualização e Imputação", "📐 Modelo Matemático", "🌧️ Simulador Pluviométrico", "🌦️ Chuvas", "🔮 Previsão do Nível"])

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
