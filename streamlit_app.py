import os

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

FUNDODOPOCO = 210  # cm, distância do sensor à linha d'água quando o poço está seco

st.set_page_config(page_title="Monitoramento de Poço - Condomínio", layout="wide")
st.title("📊 Monitoramento do Poço de Drenagem")

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

# Query SQL: busca os últimos N registros do banco para exibir no gráfico.
# Ordena por id em ordem decrescente para trazer o histórico mais recente primeiro.
sql_query = (
    "SELECT timestamp, nivel_cm, status_bomba FROM leituras_poco "
    f"ORDER BY id DESC LIMIT {int(record_limit)}"
)

# Testa vários formatos de payload que a API HTTP do Turso pode aceitar.
payload_variants = [
    # objeto top-level `stmt` com struct { sql, args }
    {"stmt": {"sql": sql_query, "args": []}},
    # lista de statements onde cada item tem um campo `stmt` (objeto)
    {"statements": [{"stmt": {"sql": sql_query, "args": []}}]},
    # lista de statements com objeto contendo `sql` diretamente
    {"statements": [{"sql": sql_query, "args": []}]},
    # lista simples de strings (menos provável, mas tentamos)
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
    st.json([{"variant": i + 1, "payload_preview": {k: (v if k != 'stmt' else {'sql': v.get('sql')}) for k, v in p.items()}} for i, p in enumerate(payload_variants)])
    st.stop()

response_data = response.json()

# Suporta múltiplos formatos de resposta do Turso:
# - formato com `results` -> [{ "columns": [...], "rows": [...] }]
# - formato com `cols` e `rows` no nível root
if "results" in response_data and response_data.get("results"):
    result = response_data["results"][0]
    columns = result.get("columns", [])
    rows = result.get("rows", [])
elif "result" in response_data and isinstance(response_data.get("result"), dict) and "cols" in response_data.get("result") and "rows" in response_data.get("result"):
    # Ex.: {"result": {"cols": [{"name":"timestamp",...}], "rows": [[{"type":"text","value":"..."}, ...]]}}
    columns = [c.get("name") for c in response_data["result"].get("cols", [])]
    rows = response_data["result"].get("rows", [])
elif "cols" in response_data and "rows" in response_data:
    # Ex.: {"cols": [{"name":"timestamp",...}], "rows": [[{"type":"text","value":"..."}, ...]]}
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

if "timestamp" in df.columns:
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    # Ajustar para o horário de Brasília
    df["timestamp"] = df["timestamp"].dt.tz_localize("UTC").dt.tz_convert("America/Sao_Paulo")
    df = df.sort_values("timestamp").reset_index(drop=True)

if "nivel_cm" in df.columns:
    df["distancia_sensor_cm"] = pd.to_numeric(df["nivel_cm"], errors="coerce")
    df["altura_agua_cm"] = (FUNDODOPOCO - df["distancia_sensor_cm"]).clip(lower=0)

    col1, col2 = st.columns(2)
    with col1:
        distancia_atual = float(df["distancia_sensor_cm"].iloc[0])
        st.metric(
            label="Distância atual do sensor à linha d'água",
            value=f"{distancia_atual:.0f} cm",
        )
    with col2:
        if "status_bomba" in df.columns:
            status_atual = df["status_bomba"].iloc[0]
            st.metric(label="Status da Bomba", value=status_atual)
        else:
            st.info("Coluna de status da bomba não encontrada na resposta.")

    st.subheader("Diagnóstico do poço")
    if pd.isna(distancia_atual):
        st.warning("Sem leitura válida da distância do sensor à linha d'água.")
    elif distancia_atual >= FUNDODOPOCO:
        st.warning(f"⚠️ Poço seco: a distância do sensor à linha d'água é de {distancia_atual/100:.1f} m ou mais.")
    elif distancia_atual <= 20:
        st.error("🚨 Alerta: poço em eminência de transbordamento.")
    else:
        altura_agua_cm = float(df["altura_agua_cm"].iloc[0])
        st.success(f"Nível estimado da água: {altura_agua_cm:.0f} cm acima do fundo do poço.")

    st.subheader("Histórico da distância do sensor à linha d'água")
    fig = px.line(
        df,
        x="timestamp",
        y="distancia_sensor_cm",
        title="Distância do sensor à linha d'água ao longo do tempo",
        labels={
            "timestamp": "Data/Hora",
            "distancia_sensor_cm": "Distância sensor-água (cm)",
        },
        markers=True,
    )
    fig.update_yaxes(autorange="reversed")

    fig.add_hline(
        y=FUNDODOPOCO,
        line_dash="dash",
        line_color="red",
        annotation_text=f"Poço seco (>= {FUNDODOPOCO} cm)",
    )
    fig.add_hline(
        y=20,
        line_dash="dash",
        line_color="orange",
        annotation_text=f"Transbordamento (<= {20} cm)",
    )
    st.plotly_chart(fig)

    st.subheader("Últimos Registros")
    st.dataframe(df)
else:
    st.error("Os campos esperados não foram encontrados na resposta do Turso.")
    st.json(df.head(5).to_dict(orient="records"))
