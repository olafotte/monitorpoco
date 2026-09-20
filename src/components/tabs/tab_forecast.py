"""
Componente da Aba 5: Previsão do Nível do Poço para as Próximas 120 Horas.
"""

from typing import Optional, Any
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.config import FUNDODOPOCO
from src.utils import now_local


def render_tab_forecast(
    weather_df: pd.DataFrame,
    latest_distance: Optional[float],
    latest_row: Any,
    d_on: float,
    d_off: float,
    d_on2: float,
    d_off2: float,
    tem_bomba2: bool,
    dist_borda_cm: float,
    factor_mm_cm: float,
    r_gnd_param: float,
    r_pump_param: float,
    d_overflow: float = 0.0,
) -> None:
    """Renderiza a Aba 5 com previsão dinâmica do nível em até 120 horas e simulação de atracamento."""
    st.subheader("🔮 Previsão do Nível do Poço – Próximas 120 Horas")
    st.caption(
        "Simulação a partir do nível atual real do sensor, aplicando o modelo de balanço de massa "
        "com a previsão horária de precipitação da Open-Meteo."
    )

    now_tz5 = now_local()

    fcast_120h = pd.DataFrame()
    if not weather_df.empty:
        fcast_120h = weather_df[
            (weather_df.index > now_tz5) &
            (weather_df.index <= now_tz5 + pd.Timedelta(hours=120))
        ].copy()

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        bomba_status5 = st.selectbox(
            "Status da Bomba para Previsão",
            ["Operacional (Ligada)", "Falha / Sem Energia (Desligada)"],
            key="bomba_status_tab5",
        )
    with col_s2:
        horizonte_h = st.slider(
            "Horizonte da Previsão (horas)", min_value=6, max_value=120, value=24, step=6,
            key="horizonte_tab5",
        )

    nivel_inicial_cm = latest_distance if latest_distance is not None else d_off
    last_dt_str = "última leitura"
    if latest_row is not None:
        dt_val = latest_row.get("dt_round") if hasattr(latest_row, "get") else getattr(latest_row, "dt_round", None)
        if pd.notna(dt_val):
            last_dt_str = pd.to_datetime(dt_val).strftime("%d/%m/%Y %H:%M")

    st.info(
        f"📍 Nível de partida: **{nivel_inicial_cm:.1f} cm** (distância sensor→água) "
        f"| Leitura de: **{last_dt_str}**"
    )

    dt_min = 5.0
    dt_h_step = dt_min / 60.0
    n_steps = int(horizonte_h * 60 / dt_min)

    sim_times = [now_tz5 + pd.Timedelta(minutes=dt_min * i) for i in range(n_steps + 1)]
    sim_levels = []
    sim_pump_state = []
    sim_rain_rate = []

    curr_d5 = nivel_inicial_cm
    p1_on = curr_d5 <= d_on
    p2_on = tem_bomba2 and (curr_d5 <= d_on2)
    overflow5 = False
    overflow5_time = None

    for i, t in enumerate(sim_times):
        if not fcast_120h.empty:
            idx_candidates = fcast_120h.index[fcast_120h.index <= t]
            if len(idx_candidates) > 0:
                precip_mmh = float(fcast_120h.loc[idx_candidates[-1], "precipitation"])
            else:
                precip_mmh = 0.0
        else:
            precip_mmh = 0.0

        r_chuva_cm_h = precip_mmh * factor_mm_cm
        r_total_entrada = r_gnd_param + r_chuva_cm_h
        sim_rain_rate.append(r_chuva_cm_h)

        sim_levels.append(curr_d5)
        sim_pump_state.append(p1_on or p2_on)

        if not overflow5 and curr_d5 <= d_overflow:
            overflow5 = True
            overflow5_time = t

        if i == n_steps:
            break

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
        curr_d5 = max(curr_d5, d_overflow - 10)

    sim_df = pd.DataFrame({
        "datetime": sim_times[:len(sim_levels)],
        "nivel_cm": sim_levels,
        "bomba_ligada": sim_pump_state,
        "r_chuva_cm_h": sim_rain_rate,
    })

    nivel_final = sim_levels[-1]
    nivel_min = min(sim_levels)
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

    fig5 = go.Figure()

    if not fcast_120h.empty:
        fcast_plot = fcast_120h[fcast_120h.index <= now_tz5 + pd.Timedelta(hours=horizonte_h)]
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

    fig5.add_trace(
        go.Scatter(
            x=[now_tz5],
            y=[nivel_inicial_cm],
            mode="markers",
            name="Nível Atual (Real)",
            marker=dict(size=12, color="#f39c12", symbol="star"),
        )
    )

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

    _now5_str = now_tz5.strftime("%Y-%m-%d %H:%M:%S")
    fig5.add_shape(
        type="line", x0=_now5_str, x1=_now5_str, y0=0, y1=1, yref="paper",
        line=dict(dash="dash", color="orange", width=1.5),
    )
    fig5.add_annotation(
        x=_now5_str, y=0.98, yref="paper", text="Agora",
        showarrow=False, xanchor="left", font=dict(color="orange", size=11),
    )

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
            title="Nível da Água (cm abaixo do topo do poço)",
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
    st.plotly_chart(fig5, width="stretch")

    if overflow5 and overflow5_time is not None:
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
        intensidade_max_prev = fcast_120h["precipitation"].max() if not fcast_120h.empty else 0.0
        intensidade_sat = r_pump_param / factor_mm_cm
        if intensidade_max_prev > intensidade_sat and bomba_status5 == "Operacional (Ligada)":
            st.warning(
                f"⚠️ **Atenção:** a previsão inclui intensidades de até **{intensidade_max_prev:.1f} mm/h**, "
                f"acima do limite de saturação da bomba ({intensidade_sat:.1f} mm/h). "
                "Monitorar de perto."
            )
        else:
            st.success("✅ Nenhum transbordo previsto no horizonte selecionado.")

    with st.expander("Ver tabela de evolução horária simulada", icon=":material/table_chart:"):
        hourly_sim = sim_df.set_index("datetime").resample("1h").first()
        hourly_sim["Bomba"] = hourly_sim["bomba_ligada"].map({True: "🔴 Ligada", False: "🟢 Desligada"})
        hourly_sim["Entrada por Chuva (cm/h)"] = hourly_sim["r_chuva_cm_h"].round(2)
        hourly_sim["Nível (dist. sensor, cm)"] = hourly_sim["nivel_cm"].round(1)
        hourly_sim["Altura Água (cm)"] = (FUNDODOPOCO - hourly_sim["nivel_cm"]).clip(lower=0).round(1)

        if not fcast_120h.empty:
            fcast_merge = fcast_120h.resample("1h").sum().rename(columns={"precipitation": "Chuva Prevista (mm/h)"})
            hourly_sim = hourly_sim.join(fcast_merge, how="left")
            hourly_sim["Chuva Prevista (mm/h)"] = hourly_sim["Chuva Prevista (mm/h)"].fillna(0.0).round(2)

        display_cols = ["Nível (dist. sensor, cm)", "Altura Água (cm)", "Bomba", "Entrada por Chuva (cm/h)"]
        if "Chuva Prevista (mm/h)" in hourly_sim.columns:
            display_cols.append("Chuva Prevista (mm/h)")

        hourly_sim.index = pd.DatetimeIndex(hourly_sim.index).strftime("%d/%m/%Y %H:%M")
        st.dataframe(hourly_sim[display_cols], width="stretch")
