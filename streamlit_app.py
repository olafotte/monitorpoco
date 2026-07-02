import os

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

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

# Query para buscar os dados
payload = {
    "statements": [
        "SELECT timestamp, nivel_cm, status_bomba FROM leituras_poco ORDER BY id DESC LIMIT 100"
    ]
}

response = requests.post(TURSO_URL, json=payload, headers=headers)

if response.status_code != 200:
    st.error(
        "Erro ao conectar com o banco de dados Turso. "
        f"Status {response.status_code}: {response.text}"
    )
    st.stop()

response_data = response.json()
results = response_data.get("results") or []
if not results:
    st.error("A resposta do Turso não contém resultados válidos.")
    st.json(response_data)
    st.stop()

result = results[0]
columns = result.get("columns", [])
rows = result.get("rows", [])

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

if "nivel_cm" in df.columns and "status_bomba" in df.columns:
    col1, col2 = st.columns(2)
    with col1:
        nivel_atual = df["nivel_cm"].iloc[0]
        st.metric(label="Nível Atual do Poço", value=f"{nivel_atual} cm")
    with col2:
        status_atual = df["status_bomba"].iloc[0]
        st.metric(label="Status da Bomba", value=status_atual)

    st.subheader("Histórico do Nível")
    fig = px.line(
        df,
        x="timestamp",
        y="nivel_cm",
        title="Variação do Nível nas Últimas Leituras",
        labels={"timestamp": "Data/Hora", "nivel_cm": "Nível (cm)"},
    )
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Últimos Registros")
    st.dataframe(df)
else:
    st.error("Os campos esperados não foram encontrados na resposta do Turso.")
    st.json(df.head(5).to_dict(orient="records"))
