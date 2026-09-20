"""
Módulo de comunicação e ingestão de dados com o banco de dados Turso (LibSQL).
"""

from typing import Optional
import pandas as pd
import requests
import streamlit as st


@st.cache_data(ttl="1m", max_entries=5, show_spinner="⏳ Conectando ao banco Turso...")
def fetch_turso_data(
    turso_url: str,
    turso_token: str,
    start_datetime_str: Optional[str] = None
) -> pd.DataFrame:
    """
    Executa consulta SQL no Turso DB via endpoint HTTP e retorna os registros como DataFrame.
    """
    if turso_url.startswith("libsql://"):
        turso_url = turso_url.replace("libsql://", "https://", 1)

    if start_datetime_str:
        iso_t_str = start_datetime_str.replace(" ", "T")
        sql_query = (
            "SELECT timestamp, nivel_cm, status_bomba FROM leituras_poco "
            f"WHERE timestamp >= '{start_datetime_str}' OR timestamp >= '{iso_t_str}' "
            "ORDER BY timestamp ASC LIMIT 100000"
        )
    else:
        sql_query = (
            "SELECT timestamp, nivel_cm, status_bomba FROM leituras_poco "
            "ORDER BY id DESC LIMIT 5000"
        )

    headers_req = {
        "Authorization": f"Bearer {turso_token}",
        "Content-Type": "application/json",
    }
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
            resp = requests.post(turso_url, json=payload, headers=headers_req, timeout=15)
        except Exception as exc:
            last_error = f"request error (variant {idx}): {exc}"
            continue

        if resp.status_code == 200:
            response = resp
            break
        else:
            last_error = f"status {resp.status_code}: {resp.text} (variant {idx})"

    if response is None:
        raise RuntimeError(f"Sem resposta das variantes testadas. Último erro: {last_error}")

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
        raise ValueError("A resposta do Turso não contém resultados válidos.")

    if not columns or not rows:
        return pd.DataFrame()

    if isinstance(rows[0], list) and rows[0] and isinstance(rows[0][0], dict):
        cleaned_rows = [[val.get("value") for val in row] for row in rows]
    elif isinstance(rows[0], dict):
        cleaned_rows = [[row.get(col) for col in columns] for row in rows]
    else:
        cleaned_rows = rows

    df_res = pd.DataFrame(cleaned_rows, columns=columns)
    if "status_bomba" not in df_res.columns:
        df_res["status_bomba"] = pd.NA

    # Filtrar datas corrompidas do ESP32 (ex: ano de 1970) e aplicar filtro de data/hora inicial
    if "timestamp" in df_res.columns and not df_res.empty:
        parsed_ts = pd.to_datetime(df_res["timestamp"], errors="coerce")
        mask = parsed_ts.dt.year >= 2025
        if start_datetime_str:
            try:
                dt_limit = pd.to_datetime(start_datetime_str)
                mask = mask & (parsed_ts >= dt_limit)
            except Exception:
                pass
        df_res = df_res[mask].copy()

    return df_res
