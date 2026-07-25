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
    "Nível Crítico de Transbordo (cm)", value=65.0, step=0.5, help="Distância crítica do sensor onde o poço transborda"
)
r_gnd_param = st.sidebar.number_input(
    "Taxa Lençol Freático (cm/h)", value=1.44, step=0.1
)
r_pump_param = st.sidebar.number_input(
    "Vazão de Esvaziamento Bomba (cm/h)", value=76.6, step=1.0, help="Capacidade nominal da bomba (14 m³/h)"
)
factor_mm_cm = st.sidebar.number_input(
    "Fator de Amplificação (cm de poço / mm de chuva)", value=2.83, step=0.1, help="Relação calibrada na chuva de 22/07 (54mm -> 153cm no poço)"
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


processed_df = preprocess_time_series(df)
filled_df = fill_gaps(processed_df)

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

tab1, tab2, tab3 = st.tabs(["📊 Visualização e Imputação", "📐 Modelo Matemático", "🌧️ Simulador Pluviométrico"])

with tab1:
    st.subheader("Série Temporal do Nível do Poço")
    st.caption("O eixo Y é invertido para refletir a altura real da água no poço (valores menores de distância = nível de água mais alto).")

    fig = go.Figure()
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
        yaxis=dict(autorange="reversed", title="Distância até o Sensor (cm)"),
        xaxis=dict(title="Data / Hora"),
        legend=dict(orientation="h", y=-0.2, x=0.5, xanchor="center"),
        height=500,
        margin=dict(l=20, r=20, t=30, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)

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
