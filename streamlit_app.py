"""streamlit_app.py
Monitoramento Inteligente e Análise Hidráulica de Poço de Drenagem.
Ponto de entrada principal da aplicação Streamlit modularizada (Clean Code).
"""

import datetime as dt
import json
import os
import sys

# Garantir que o diretório raiz esteja no sys.path para resolução dos módulos de 'src'
_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

import pandas as pd
import streamlit as st

from src.config import (
    FUNDODOPOCO,
    RECOMMENDED_ERA5_FACTOR,
    DEFAULT_D_ON,
    DEFAULT_D_OFF,
    DEFAULT_DIST_BORDA,
    DEFAULT_D_ON2,
    DEFAULT_D_OFF2,
    DEFAULT_FATOR_M3H_PARA_CMH,
)
from src.utils import LOCAL_TZ, now_local, normalize_local_ts
from src.data.turso import fetch_turso_data
from src.data.sensor_config import (
    load_sensor_height_config,
    save_sensor_height_config,
    get_active_dist_borda,
)
from src.data.adolfo_konder import calculate_station_era5_factor
from src.data.preprocessing import preprocess_time_series, fill_gaps
from src.data.weather import fetch_weather_data, build_precip_5min, build_temp_grid
from src.components.tabs import (
    render_tab_timeseries,
    render_tab_model,
    render_tab_simulator,
    render_tab_weather,
    render_tab_forecast,
    render_tab_idf,
    render_tab_defesa_civil,
    render_tab_diagram,
    render_tab_report,
)


def main() -> None:
    # ── Configuração da Página ───────────────────────────────────────────────────
    st.set_page_config(
        page_title="Monitoramento de Poço - Condomínio",
        page_icon=":material/water_drop:",
        layout="wide",
    )
    st.title("📊 Monitoramento do Poço de Drenagem")
    st.markdown(
        "Este aplicativo consulta o banco Turso diretamente, aplica imputação de gaps "
        "e traz também um modelo matemático de operação e adequação hidráulica."
    )

    # ── Credenciais Turso DB ────────────────────────────────────────────────────
    secrets_turso = st.secrets.get("turso", {})
    turso_url = secrets_turso.get("url") or os.environ.get("TURSO_URL")
    turso_token = secrets_turso.get("token") or os.environ.get("TURSO_TOKEN")

    if not turso_url or not turso_token:
        st.error(
            "Credenciais do Turso não configuradas. Adicione `.streamlit/secrets.toml` "
            "ou defina as variáveis de ambiente `TURSO_URL` e `TURSO_TOKEN`."
        )
        st.stop()

    # ── Sidebar: Configurações do Sistema ───────────────────────────────────────
    sensor_config_data = load_sensor_height_config()
    sensor_history = sensor_config_data.get("history", [])
    active_dist_default = get_active_dist_borda(sensor_config_data, fallback=DEFAULT_DIST_BORDA)

    st.sidebar.header("⚙️ Configurações do Sistema")

    with st.sidebar.expander("📐 Geometria do Poço & Sensor", expanded=True):
        d_on = st.number_input(
            "Nível Ativação Bomba 1 (cm abaixo do topo)",
            value=DEFAULT_D_ON,
            step=0.5,
            help="Distância da água abaixo do topo do poço quando a 1ª bomba LIGA (ex: 160 cm abaixo do topo).",
        )
        d_off = st.number_input(
            "Nível Desativação Bomba 1 (cm abaixo do topo)",
            value=DEFAULT_D_OFF,
            step=0.5,
            help="Distância da água abaixo do topo do poço quando a 1ª bomba DESLIGA (ex: 183 cm abaixo do topo).",
        )
        dist_borda_cm = st.number_input(
            "Profundidade do Sensor no Poço (cm)",
            value=active_dist_default,
            step=1.0,
            help="Distância em que o sensor está pendurado dentro do poço a partir do topo (ex: 70 cm).",
        )
        d_overflow = 0.0
        capacidade_total_cm = FUNDODOPOCO
        st.caption(
            f"↳ Topo: **0 cm** | Liga: **+{d_on:.1f} cm** (sensor: {d_on - dist_borda_cm:.1f} cm) | "
            f"Desliga: **+{d_off:.1f} cm** (sensor: {d_off - dist_borda_cm:.1f} cm)"
        )

        with st.expander("Gerenciar histórico de alturas (JSON)", icon=":material/history_edu:", expanded=False):
            st.caption("Histórico por período em `sensor_heights.json`:")
            if sensor_history:
                h_df = pd.DataFrame(sensor_history)
                disp_cols = [c for c in ["start_date", "end_date", "dist_borda_cm", "description"] if c in h_df.columns]
                st.dataframe(h_df[disp_cols], hide_index=True, width="stretch")

            st.markdown("---")
            st.markdown("**➕ Adicionar / Ajustar Período:**")
            new_start = st.text_input("Data Início (ISO)", value=dt.datetime.now().strftime("%Y-%m-%dT00:00:00"), key="cfg_new_start")
            new_end = st.text_input("Data Fim (vazio = período ativo atual)", value="", key="cfg_new_end")
            new_dist = st.number_input("Dist. Sensor → Borda Superior (cm)", value=dist_borda_cm, step=1.0, key="cfg_new_dist")
            new_desc = st.text_input("Descrição", value="Ajuste de altura do sensor", key="cfg_new_desc")

            if st.button("💾 Salvar Ajuste no JSON"):
                p_end = new_end.strip() if new_end.strip() else None
                p_start = new_start.strip()
                if p_end is None and sensor_history:
                    for item in sensor_history:
                        if item.get("end_date") is None:
                            item["end_date"] = p_start

                new_id = len(sensor_history) + 1
                sensor_history.append({
                    "id": new_id,
                    "start_date": p_start,
                    "end_date": p_end,
                    "dist_borda_cm": abs(new_dist),
                    "description": new_desc.strip(),
                })
                sensor_config_data["history"] = sensor_history
                if save_sensor_height_config(sensor_config_data):
                    st.success("Histórico atualizado em sensor_heights.json!")
                    st.rerun()
                else:
                    st.error("Falha ao salvar no arquivo JSON.")

    with st.sidebar.expander("⚡ Automação & Bombas em Paralelo", expanded=True):
        vazao_bomba_m3h = st.number_input(
            "Capacidade 1ª Bomba (m³/h)", value=37.00, step=0.5, help="Vazão nominal da 1ª bomba individual em m³/h"
        )
        fator_m3h_para_cmh = st.number_input(
            "Fator Conversão (cm/h por m³/h)",
            value=DEFAULT_FATOR_M3H_PARA_CMH,
            step=0.01,
            format="%.3f",
            help="Converte m³/h em cm/h de variação no poço durante bombeamento (100 / Área efetiva em m²).",
        )
        r_pump_param = vazao_bomba_m3h * fator_m3h_para_cmh
        area_poco_m2_calc = 100.0 / fator_m3h_para_cmh if fator_m3h_para_cmh > 0 else 5.50
        vol_ciclo_bomba_L = abs(d_off - d_on) * area_poco_m2_calc * 10.0
        st.caption(f"↳ Vazão 1ª Bomba: **{r_pump_param:.2f} cm/h** (Seção Poço: **{area_poco_m2_calc:.2f} m²** / **{area_poco_m2_calc * 10.0:.1f} L/cm**)")
        st.caption(f"↳ Volume expulso por ciclo ({abs(d_off - d_on):.0f} cm): **{vol_ciclo_bomba_L:.0f} Litros** (**{vol_ciclo_bomba_L / 1000.0:.3f} m³**)")

        tem_bomba2 = st.checkbox(
            "Instalar 2ª bomba em paralelo",
            value=False,
            help="Adiciona 2ª bomba ativada por 2ª bóia de emergência quando a 1ª bomba não der conta.",
        )
        if tem_bomba2:
            vazao_bomba2_m3h = st.number_input(
                "Capacidade 2ª Bomba (m³/h)",
                value=vazao_bomba_m3h,
                step=0.5,
                help="Vazão nominal da 2ª bomba em m³/h",
            )
            d_on2 = st.number_input(
                "Nível Ativação 2ª Bomba (cm)",
                value=DEFAULT_D_ON2,
                step=0.5,
                help="Distância quando a 2ª bomba LIGA (bóia de emergência 30 cm mais alta que a 1ª)",
            )
            d_off2 = st.number_input(
                "Nível Desativação 2ª Bomba (cm)",
                value=DEFAULT_D_OFF2,
                step=0.5,
                help="Distância quando a 2ª bomba DESLIGA",
            )
            r_pump2_param = vazao_bomba2_m3h * fator_m3h_para_cmh
            vazao_total_m3h = vazao_bomba_m3h + vazao_bomba2_m3h
            r_pump_total_param = r_pump_param + r_pump2_param
            st.caption(f"↳ Sistema Paralelo: Total **{vazao_total_m3h:.1f} m³/h** (**{r_pump_total_param:.2f} cm/h**)")
        else:
            d_on2 = d_on - 30.0
            d_off2 = d_off - 30.0
            vazao_total_m3h = vazao_bomba_m3h
            r_pump_total_param = r_pump_param

    with st.sidebar.expander("🌦️ Parâmetros Climáticos & ERA5", expanded=True):
        r_gnd_param = st.number_input("Taxa Lençol Freático (cm/h)", value=1.44, step=0.1)
        factor_mm_cm = st.number_input(
            "Fator Amplificação (cm poço / mm chuva)",
            value=2.83,
            step=0.1,
            help="Relação calibrada entre mm de chuva e variação de cm no poço",
        )
        station_calc_info = calculate_station_era5_factor()
        dyn_station_factor = station_calc_info.get("recommended_factor", RECOMMENDED_ERA5_FACTOR)

        default_era5_val = st.session_state.get("era5_corr_val", dyn_station_factor)
        era5_correction = st.number_input(
            "Fator de Correção ERA5",
            min_value=0.5,
            max_value=5.0,
            value=default_era5_val,
            step=0.05,
            format="%.2f",
            key="era5_corr_input",
        )
        col_btn_e1, col_btn_e2 = st.columns(2)
        with col_btn_e1:
            if st.button(f"🎯 Estação (×{dyn_station_factor:.2f})", help="Aplica o fator calculado automaticamente dos picos da Estação Ponte Adolfo Konder"):
                st.session_state["era5_corr_val"] = dyn_station_factor
                st.rerun()
        with col_btn_e2:
            if st.button("⚖️ Padrão (×2.60)", help="Restaura o fator padrão recomendado ×2.60"):
                st.session_state["era5_corr_val"] = RECOMMENDED_ERA5_FACTOR
                st.rerun()

        st.caption(f"📊 Fator calculado da Estação Ponte Adolfo Konder: **×{dyn_station_factor:.2f}** ({station_calc_info.get('method', 'Calibração Histórica')}).")

    with st.sidebar.expander("🔌 Conexão Banco Turso DB", expanded=False):
        _default_5d_dt = dt.datetime.now() - dt.timedelta(days=5)
        col_d1, col_d2 = st.columns(2)
        with col_d1:
            turso_start_date = st.date_input("Data Inicial", value=_default_5d_dt.date())
        with col_d2:
            turso_start_time = st.time_input("Hora Inicial", value=_default_5d_dt.time())

        start_datetime_str = dt.datetime.combine(turso_start_date, turso_start_time).strftime("%Y-%m-%d %H:%M:%S")

        turso_freq_option = st.selectbox(
            "Frequência de Amostragem do Turso",
            options=[
                "Automático (1min em chuva / 5min em seca)",
                "1 minuto (Alta Resolução)",
                "5 minutos (Padrão)",
            ],
            index=0,
        )
        if st.button("🔄 Recarregar Dados do Turso"):
            fetch_turso_data.clear()
            st.rerun()

        if "1 minuto" in turso_freq_option:
            selected_freq_mode = "1min"
        elif "5 minutos" in turso_freq_option:
            selected_freq_mode = "5min"
        else:
            selected_freq_mode = "auto"

    st.sidebar.header("🌍 Localização Meteorológica")
    latitude = st.sidebar.number_input("Latitude", value=-26.9265068, format="%.4f")
    longitude = st.sidebar.number_input("Longitude", value=-49.0687619, format="%.4f")

    # ── Pipeline de Dados ───────────────────────────────────────────────────────
    try:
        df = fetch_turso_data(turso_url, turso_token, start_datetime_str)
    except Exception as exc:
        st.error(f"Erro ao conectar com o banco de dados Turso: {exc}")
        st.stop()

    if df.empty or "timestamp" not in df.columns or "nivel_cm" not in df.columns:
        st.warning("Não há dados suficientes para exibir. Verifique a tabela ou os registros do Turso.")
        st.stop()

    sensor_history_json_str = json.dumps(sensor_history, sort_keys=True)
    processed_df = preprocess_time_series(
        df,
        sensor_history_json_str=sensor_history_json_str,
        active_dist_cm=dist_borda_cm,
        freq_mode=selected_freq_mode,
    )
    filled_df = fill_gaps(
        processed_df,
        r_gnd_param=r_gnd_param,
        r_pump_param=r_pump_param,
        d_on=d_on,
        d_off=d_off,
        tem_bomba2=tem_bomba2,
        d_on2=d_on2,
        d_off2=d_off2,
    )

    # Dados meteorológicos alinhados ao período do sensor
    _sensor_min = filled_df["dt_round"].min()
    _sensor_max = filled_df["dt_round"].max()
    _weather_start = (_sensor_min - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    _weather_end = (now_local() + pd.Timedelta(days=7)).strftime("%Y-%m-%d")

    weather_df = fetch_weather_data(latitude, longitude, _weather_start, _weather_end)
    if not weather_df.empty and era5_correction != 1.0:
        weather_df = weather_df.copy()
        weather_df["precipitation"] = weather_df["precipitation"] * era5_correction

    # Métricas de temperatura local
    latest_temp = None
    temp_min_24h = None
    temp_max_24h = None
    temp_min_period = None
    temp_max_period = None
    temp_avg_period = None

    if not weather_df.empty and "temperature_2m" in weather_df.columns:
        _now_temp_tz = now_local()
        _hist_temp_series = weather_df[weather_df.index <= _now_temp_tz]["temperature_2m"].dropna()
        if not _hist_temp_series.empty:
            latest_temp = float(_hist_temp_series.iloc[-1])
            _last_24h_temp = weather_df[
                (weather_df.index >= (_now_temp_tz - pd.Timedelta(hours=24))) &
                (weather_df.index <= _now_temp_tz)
            ]["temperature_2m"].dropna()
            if not _last_24h_temp.empty:
                temp_min_24h = float(_last_24h_temp.min())
                temp_max_24h = float(_last_24h_temp.max())

        _sensor_temp_series = weather_df[
            (weather_df.index >= _sensor_min) & (weather_df.index <= _sensor_max)
        ]["temperature_2m"].dropna()
        if not _sensor_temp_series.empty:
            temp_min_period = float(_sensor_temp_series.min())
            temp_max_period = float(_sensor_temp_series.max())
            temp_avg_period = float(_sensor_temp_series.mean())

    # Precipitação e temperatura alinhadas ao grid do sensor
    filled_df["precipitation_5min"] = build_precip_5min(weather_df, filled_df["dt_round"]).values
    filled_df["temperatura_c"] = build_temp_grid(weather_df, filled_df["dt_round"]).values

    latest_valid = filled_df[filled_df["nivel_cm"].notna()]
    latest_row = latest_valid.iloc[-1] if not latest_valid.empty else filled_df.iloc[-1]
    latest_distance = (
        float(latest_row["distancia_sensor_cm"])
        if not pd.isna(latest_row["distancia_sensor_cm"])
        else None
    )

    # ── Diagnóstico Rápido e Alertas ───────────────────────────────────────────
    st.subheader("Diagnóstico do poço")

    latest_ts_raw = latest_row.get("dt_round") or latest_row.get("timestamp")
    if pd.notna(latest_ts_raw):
        latest_ts = normalize_local_ts(latest_ts_raw)
        if pd.notna(latest_ts) and isinstance(latest_ts, pd.Timestamp):
            delay_min = (now_local() - latest_ts).total_seconds() / 60.0
            if delay_min > 7.0:
                st.error(
                    f"🚨 **ERRO DE DADOS DA LEITURA (Atraso de {delay_min:.0f} min):**\n\n"
                    f"Última leitura registrada em **{latest_ts.strftime('%d/%m/%Y às %H:%M:%S')}**.\n"
                    f"O sistema está há mais de **{delay_min:.0f} minutos sem dados atualizados** do banco Turso."
                )

    if latest_distance is None:
        st.warning("Sem leitura válida da distância do sensor à linha d'água.")
    else:
        dist_sensor_agua = latest_distance - dist_borda_cm
        if dist_sensor_agua <= 0:
            st.error(f"🚨 **ALERTA EXTREMO:** Sensor SUBMERGIDO! Nível d'água **{abs(dist_sensor_agua):.0f} cm** acima do sensor físico.")
        elif dist_sensor_agua <= 20.0:
            st.error(f"🚨 **ALERTA DE RISCO:** A água está a apenas **{dist_sensor_agua:.0f} cm** do sensor físico.")
        elif latest_distance >= FUNDODOPOCO * 0.9:
            st.warning(f"⚠️ Poço seco: a distância do sensor à linha d'água é de {latest_distance / 100:.1f} m ou mais.")
        else:
            st.success(
                f"Nível estimado da água: **{latest_distance:.1f} cm** abaixo do topo do poço "
                f"(Sensor a {dist_sensor_agua:.0f} cm da água | Bomba Liga a {d_on:.0f} cm | Desliga a {d_off:.0f} cm)"
            )

    col1, col2, col3, col4, col5 = st.columns(5)
    total_obs = len(filled_df)
    missing_obs = int(filled_df["nivel_cm"].isna().sum())
    missing_pct = (missing_obs / total_obs) * 100 if total_obs > 0 else 0.0
    col1.metric("Total de Leituras Esperadas", f"{total_obs:,}")
    col2.metric("Leituras Validadas", f"{total_obs - missing_obs:,}")
    col3.metric("Dados Preenchidos (Gaps)", f"{missing_obs:,} ({missing_pct:.1f}%)")
    col4.metric("Faixa de Operação", f"{d_on:.1f} cm - {d_off:.1f} cm")
    if latest_temp is not None:
        temp_delta_str = f"Mín {temp_min_24h:.1f}°C | Máx {temp_max_24h:.1f}°C (24h)" if temp_min_24h is not None and temp_max_24h is not None else None
        col5.metric("🌡️ Temp. Local Atual", f"{latest_temp:.1f} °C", delta=temp_delta_str, delta_color="normal")
    else:
        col5.metric("🌡️ Temp. Local Atual", "N/A")

    # ── Renderização das Abas (com Lazy-Loading) ────────────────────────────────
    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs([
        ":material/analytics: Visualização e Imputação",
        ":material/architecture: Modelo Matemático",
        ":material/water: Simulador Pluviométrico",
        ":material/thermostat: Chuvas & Clima",
        ":material/auto_awesome: Previsão do Nível",
        ":material/history: Histórico & IDF",
        ":material/shield: Defesa Civil Blumenau",
        ":material/schema: Diagrama do Poço",
        ":material/description: Relatório de Adequação",
    ], on_change="rerun")

    with tab1:
        if tab1.open:
            render_tab_timeseries(
                filled_df=filled_df,
                weather_df=weather_df,
                d_on=d_on,
                d_off=d_off,
                dist_borda_cm=dist_borda_cm,
                tem_bomba2=tem_bomba2,
                d_on2=d_on2,
                d_off2=d_off2,
                vazao_bomba_m3h=vazao_bomba_m3h,
                fator_m3h_para_cmh=fator_m3h_para_cmh,
                factor_mm_cm=factor_mm_cm,
                d_overflow=d_overflow,
            )

    with tab2:
        if tab2.open:
            render_tab_model(
                filled_df=filled_df,
                d_on=d_on,
                d_off=d_off,
                vazao_bomba_m3h=vazao_bomba_m3h,
                r_pump_param=r_pump_param,
                r_gnd_param=r_gnd_param,
                fator_m3h_para_cmh=fator_m3h_para_cmh,
            )

    with tab3:
        if tab3.open:
            render_tab_simulator(
                factor_mm_cm=factor_mm_cm,
                r_pump_param=r_pump_param,
                r_pump_total_param=r_pump_total_param,
                r_gnd_param=r_gnd_param,
                vazao_total_m3h=vazao_total_m3h,
                tem_bomba2=tem_bomba2,
                d_on=d_on,
                d_off=d_off,
                d_on2=d_on2,
                d_off2=d_off2,
                dist_borda_cm=dist_borda_cm,
                latitude=latitude,
                longitude=longitude,
                era5_correction=era5_correction,
                d_overflow=d_overflow,
            )

    with tab4:
        if tab4.open:
            render_tab_weather(
                weather_df=weather_df,
                latitude=latitude,
                longitude=longitude,
                factor_mm_cm=factor_mm_cm,
                filled_df=filled_df,
                fator_m3h_para_cmh=fator_m3h_para_cmh,
                vazao_bomba_m3h=vazao_bomba_m3h,
                latest_temp=latest_temp,
                temp_min_24h=temp_min_24h,
                temp_max_24h=temp_max_24h,
                temp_min_period=temp_min_period,
                temp_max_period=temp_max_period,
                temp_avg_period=temp_avg_period,
            )

    with tab5:
        if tab5.open:
            render_tab_forecast(
                weather_df=weather_df,
                latest_distance=latest_distance,
                latest_row=latest_row,
                d_on=d_on,
                d_off=d_off,
                d_on2=d_on2,
                d_off2=d_off2,
                tem_bomba2=tem_bomba2,
                dist_borda_cm=dist_borda_cm,
                factor_mm_cm=factor_mm_cm,
                r_gnd_param=r_gnd_param,
                r_pump_param=r_pump_param,
                d_overflow=d_overflow,
            )

    with tab6:
        if tab6.open:
            render_tab_idf(
                latitude=latitude,
                longitude=longitude,
                era5_correction=era5_correction,
                factor_mm_cm=factor_mm_cm,
                r_pump_param=r_pump_param,
                r_gnd_param=r_gnd_param,
                tem_bomba2=tem_bomba2,
                d_on=d_on,
                d_off=d_off,
                d_on2=d_on2,
                d_off2=d_off2,
                d_overflow=d_overflow,
            )

    with tab7:
        if tab7.open:
            render_tab_defesa_civil(
                latitude=latitude,
                longitude=longitude,
                era5_correction=era5_correction,
                factor_mm_cm=factor_mm_cm,
                r_pump_param=r_pump_param,
            )

    with tab8:
        if tab8.open:
            render_tab_diagram(
                dist_borda_cm=dist_borda_cm,
                d_on=d_on,
                d_off=d_off,
                tem_bomba2=tem_bomba2,
                d_on2=d_on2,
                d_off2=d_off2,
                latest_distance=latest_distance,
                capacidade_total_cm=capacidade_total_cm,
            )

    with tab9:
        if tab9.open:
            render_tab_report(
                latitude=latitude,
                longitude=longitude,
                fator_m3h_para_cmh=fator_m3h_para_cmh,
                factor_mm_cm=factor_mm_cm,
                era5_correction=era5_correction,
                dist_borda_cm=dist_borda_cm,
                capacidade_total_cm=capacidade_total_cm,
                vazao_bomba_m3h=vazao_bomba_m3h,
                vazao_total_m3h=vazao_total_m3h,
                r_pump_param=r_pump_param,
                r_pump_total_param=r_pump_total_param,
                tem_bomba2=tem_bomba2,
                d_on=d_on,
                d_off=d_off,
                d_on2=d_on2,
                d_off2=d_off2,
            )


if __name__ == "__main__":
    main()
