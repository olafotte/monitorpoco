"""
Componente da Aba 3: Simulador de Eventos Pluviométricos e Saturação da Bomba.
"""

import datetime as _dt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.data.weather import fetch_historical_5years_precip
from src.data.adolfo_konder import load_adolfo_konder_dataset


def render_tab_simulator(
    factor_mm_cm: float,
    r_pump_param: float,
    r_pump_total_param: float,
    r_gnd_param: float,
    vazao_total_m3h: float,
    tem_bomba2: bool,
    d_on: float,
    d_off: float,
    d_on2: float,
    d_off2: float,
    dist_borda_cm: float,
    latitude: float,
    longitude: float,
    era5_correction: float,
    d_overflow: float = 0.0,
) -> None:
    """Renderiza a Aba 3 com o simulador de cenários pluviométricos e reconstrução histórica."""
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
        yaxis=dict(autorange="reversed", title="Nível da Água (cm abaixo do topo do poço)"),
        height=450,
    )
    st.plotly_chart(fig_sim, width="stretch")

    st.subheader("📢 Análise de Capacidade e Risco")
    if intensidade_mm_h > intensidade_saturacao_mm_h and bomba_status == "Operacional (Ligada)":
        st.warning(
            f"⚠️ **SATURAÇÃO DA BOMBA DETECTADA:** A intensidade da chuva ({intensidade_mm_h:.1f} mm/h) supera a capacidade máxima de esvaziamento da bomba ({intensidade_saturacao_mm_h:.1f} mm/h). Mesmo com a bomba ligada 100% do tempo, o poço acumulará água."
        )
    elif bomba_status == "Operacional (Ligada)":
        st.success(
            f"✅ **BOMBA DÁ CONTA DA CHUVA:** A intensidade ({intensidade_mm_h:.1f} mm/h) é inferior ao limite de saturação ({intensidade_saturacao_mm_h:.1f} mm/h). A bomba conseguirá ciclar e esvaziar o poço."
        )

    if overflow_occurred and overflow_time is not None:
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
            value=d_off,
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
    end_hist_dt = data_evento + _dt.timedelta(hours=duracao_evento_h)
    end_hist_str = end_hist_dt.strftime("%Y-%m-%d")

    df_ev = pd.DataFrame()
    lbl_fonte = ""
    usando_konder_real = False

    start_ts_ev = pd.Timestamp(data_evento, tz="America/Sao_Paulo")
    end_ts_ev = start_ts_ev + pd.Timedelta(hours=duracao_evento_h)

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

        curr_d_ev = nivel_inicial_sim
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
        dist_ao_sensor_ev = abs(min_dist_ev - dist_borda_cm)

        st.markdown("---")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Precipitação Acumulada", f"{precip_total_ev:.1f} mm", help=f"Fonte: {lbl_fonte}")
        m2.metric(
            "Pico de Chuva (Taxa Horária)",
            f"{pico_mmh_ev:.1f} mm/h",
            help=f"Maior impulso de chuva. " + (f"Pico de 15min: {pico_15m_val:.1f} mm." if pico_15m_val else "") + f" Entrada no poço: {pico_mmh_ev * factor_mm_cm:.1f} cm/h"
        )
        m3.metric("Nível Mais Alto Atingido", f"{min_dist_ev:.1f} cm do topo", help="0 cm = Topo do Poço (Borda Superior)")
        if ovf_ev:
            m4.metric("Status do Evento", "❌ TRANSBORDO", delta="Borda Superada!", delta_color="inverse")
        else:
            m4.metric("Status do Evento", "✅ SEM TRANSBORDO", delta=f"Folga ao topo: {min_dist_ev:.1f} cm")

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

        fig_hist.add_hline(y=d_overflow, line_dash="dot", line_color="darkred", annotation_text="Topo do Poço / Borda Superior (0 cm)", annotation_position="top right")
        fig_hist.add_hrect(y0=0, y1=d_overflow, fillcolor="rgba(231,76,60,0.10)", line_width=0)

        fig_hist.update_layout(
            title=f"Reconstrução do Evento ({lbl_fonte}): {start_hist_str} ({duracao_evento_h}h) | Chuva Total: {precip_total_ev:.1f} mm | Pico: {pico_mmh_ev:.1f} mm/h",
            xaxis=dict(title="Data / Hora"),
            yaxis=dict(autorange="reversed", title="Nível da Água (cm abaixo do topo do poço)"),
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
        st.plotly_chart(fig_hist, width="stretch")

        if ovf_ev and ovf_ev_time is not None:
            t_ovf_str = ovf_ev_time.strftime("%d/%m/%Y às %H:%M")
            st.error(
                f"🚨 **ALERTA DE TRANSBORDO REGISTRADO NA SIMULAÇÃO:**\n\n"
                f"Na data de **{t_ovf_str}**, o nível da água ultrapassou a borda superior do poço.\n"
                f"O pico de chuva de **{pico_mmh_ev:.1f} mm/h** ({pico_mmh_ev * factor_mm_cm:.1f} cm/h no poço) "
                f"superou a capacidade instalada de remoção das bombas ({r_pump_total_param:.1f} cm/h)."
            )
        else:
            st.success(
                f"✅ **SISTEMA SUPORTOU O EVENTO HISTÓRICO ({lbl_fonte}):**\n\n"
                f"Durante todo o período de {duracao_evento_h}h a partir de {start_hist_str}, "
                f"o nível máximo atingido ficou a **{min_dist_ev:.1f} cm da borda superior (topo do poço)** "
                f"(distância de **{dist_ao_sensor_ev:.1f} cm** em relação ao sensor físico)."
            )
