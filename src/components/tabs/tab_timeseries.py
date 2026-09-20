"""
Componente da Aba 1: Visualização e Imputação da Série Temporal.
"""

from typing import Optional
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from src.config import FUNDODOPOCO, DEFAULT_FATOR_M3H_PARA_CMH
from src.utils import format_hours, format_hours_status
from src.data.preprocessing import compute_ultrasonic_temperature_error
from src.models.pump_cycles import (
    detect_pump_cycles_and_volumes,
    compute_rate_statistics,
)
from src.models.trends import compute_recent_rising_trend
from src.components.charts import (
    plot_historical_extremes_chart,
    plot_rate_histograms,
    plot_pump_cycles_volume_chart,
)


def render_tab_timeseries(
    filled_df: pd.DataFrame,
    weather_df: pd.DataFrame,
    d_on: float,
    d_off: float,
    dist_borda_cm: float,
    tem_bomba2: bool,
    d_on2: float,
    d_off2: float,
    vazao_bomba_m3h: float,
    fator_m3h_para_cmh: float,
    factor_mm_cm: float,
    d_overflow: float = 0.0,
) -> None:
    """Renderiza a Aba 1 com série temporal, imputação de gaps, extremos históricos e ciclos de bombeamento."""
    st.subheader("Série Temporal do Nível do Poço")
    st.caption(
        "Painel sincronizado em tempo real: o gráfico superior mostra o nível do poço (esq.) e a chuva (dir.). "
        "O gráfico inferior mostra a variação da temperatura local (°C) em alinhamento temporal perfeito com as medições."
    )

    df_cycles_top, stats_cycles_top = detect_pump_cycles_and_volumes(
        filled_df, fator_m3h_para_cmh, vazao_bomba_m3h=vazao_bomba_m3h
    )
    vol_total_m3_top = stats_cycles_top.get("volume_total_m3", 0.0)
    vol_total_L_top = vol_total_m3_top * 1000.0
    n_cycles_top = stats_cycles_top.get("total_acionamentos", 0)

    col_top1, col_top2, col_top3, col_top4 = st.columns(4)
    col_top1.metric("💧 Vol. Total Expulso", f"{vol_total_m3_top:.3f} m³", help="Volume total acumulado de água expulso pelas bombas no período selecionado")
    col_top2.metric("🪣 Litros Expulsos", f"{vol_total_L_top:,.0f} L", help="Volume total em Litros expulso pelas bombas no período")
    col_top3.metric("⚡ Ciclos da Bomba", f"{n_cycles_top} acionamentos", help="Número total de ciclos curtos de bombeamento detectados no período")
    if "nivel_imputed" in filled_df.columns and not filled_df.empty:
        curr_lvl_top = (FUNDODOPOCO - filled_df['nivel_imputed'].iloc[-1])
        col_top4.metric("📏 Altura Atual d'Água", f"{curr_lvl_top:.1f} cm", delta=f"{filled_df['nivel_imputed'].iloc[-1]:.1f} cm ao topo", delta_color="off")
    st.markdown("---")

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
    has_temp_data = "temperatura_c" in filled_df.columns and filled_df["temperatura_c"].notna().any()

    if has_temp_data:
        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.07,
            row_heights=[0.68, 0.32],
            specs=[[{"secondary_y": True}], [{"secondary_y": False}]],
        )
    else:
        fig = go.Figure()

    # Barras de precipitação no eixo secundário (renderizadas primeiro para ficarem atrás)
    w_fut = pd.DataFrame()
    if not weather_df.empty:
        sensor_min = filled_df["dt_round"].min()
        sensor_max = filled_df["dt_round"].max()

        max_chart_dt = sensor_max
        if not weather_df.empty:
            max_chart_dt = max(max_chart_dt, weather_df.index.max())
        if trend and trend["era5_sim_df"] is not None and not trend["era5_sim_df"].empty:
            max_chart_dt = max(max_chart_dt, trend["era5_sim_df"]["dt_round"].max())

        w_chart = weather_df[
            (weather_df.index >= sensor_min) & (weather_df.index <= max_chart_dt)
        ]

        if not w_chart.empty:
            w_hist = w_chart[w_chart.index <= sensor_max]
            w_fut = w_chart[w_chart.index > sensor_max]

            if not w_hist.empty:
                trace_rain_hist = go.Bar(
                    x=w_hist.index,
                    y=w_hist["precipitation"],
                    name="Chuva Recente (mm/h)",
                    marker_color="rgba(30, 144, 255, 0.35)",
                )
                if has_temp_data:
                    fig.add_trace(trace_rain_hist, row=1, col=1, secondary_y=True)
                else:
                    trace_rain_hist.yaxis = "y2"
                    fig.add_trace(trace_rain_hist)
            if not w_fut.empty:
                trace_rain_fut = go.Bar(
                    x=w_fut.index,
                    y=w_fut["precipitation"],
                    name="Previsão Chuva ERA5 (mm/h)",
                    marker_color="rgba(46, 204, 113, 0.45)",
                )
                if has_temp_data:
                    fig.add_trace(trace_rain_fut, row=1, col=1, secondary_y=True)
                else:
                    trace_rain_fut.yaxis = "y2"
                    fig.add_trace(trace_rain_fut)
    elif "precipitation_5min" in filled_df.columns and filled_df["precipitation_5min"].sum() > 0:
        if len(filled_df) > 1:
            step_min = (filled_df["dt_round"].iloc[1] - filled_df["dt_round"].iloc[0]).total_seconds() / 60.0
            if step_min <= 0:
                step_min = 5.0
        else:
            step_min = 5.0
        precip_mmh = filled_df["precipitation_5min"] * (60.0 / step_min)

        trace_rain_grid = go.Bar(
            x=filled_df["dt_round"],
            y=precip_mmh,
            name="Chuva (mm/h)",
            marker_color="rgba(30, 144, 255, 0.35)",
        )
        if has_temp_data:
            fig.add_trace(trace_rain_grid, row=1, col=1, secondary_y=True)
        else:
            trace_rain_grid.yaxis = "y2"
            fig.add_trace(trace_rain_grid)

    trace_sensor = go.Scatter(
        x=filled_df["dt_round"],
        y=filled_df["nivel_cm"],
        mode="markers",
        name="Dados Reais (Sensor)",
        marker=dict(size=4, color="#1f77b4"),
    )
    trace_imputed = go.Scatter(
        x=filled_df["dt_round"],
        y=filled_df["nivel_imputed"],
        mode="lines",
        name="Modelo Estimado (Sem Gaps)",
        line=dict(color="#ff7f0e", width=1.5),
    )

    if has_temp_data:
        fig.add_trace(trace_sensor, row=1, col=1, secondary_y=False)
        fig.add_trace(trace_imputed, row=1, col=1, secondary_y=False)
    else:
        fig.add_trace(trace_sensor)
        fig.add_trace(trace_imputed)

    if trend and trend["rate_cmh"] > 0:
        t_latest = trend["latest_time"]
        t_sensor_dt = t_latest + pd.Timedelta(hours=trend["time_sensor_h"])
        t_overflow_dt = t_latest + pd.Timedelta(hours=trend["time_overflow_h"])

        trace_proj_base = go.Scatter(
            x=[t_latest, t_sensor_dt, t_overflow_dt],
            y=[trend["curr_d"], 0, d_overflow],
            mode="lines+markers",
            name=f"Projeção Base ({trend['rate_cmh']:.2f} cm/h)",
            line=dict(color="#777777", width=2, dash="dash"),
            marker=dict(size=5, symbol="circle"),
        )
        if has_temp_data:
            fig.add_trace(trace_proj_base, row=1, col=1, secondary_y=False)
        else:
            fig.add_trace(trace_proj_base)

        # Projeção Corrigida com Previsão ERA5 (Com Chuva Futura)
        if trend["era5_sim_df"] is not None and not trend["era5_sim_df"].empty:
            trace_proj_era5 = go.Scatter(
                x=trend["era5_sim_df"]["dt_round"],
                y=trend["era5_sim_df"]["nivel_imputed"],
                mode="lines",
                name="Projeção Corrigida (ERA5 + Chuva)",
                line=dict(color="#d9534f", width=2.5, dash="dot"),
            )
            if has_temp_data:
                fig.add_trace(trace_proj_era5, row=1, col=1, secondary_y=False)
            else:
                fig.add_trace(trace_proj_era5)

    b1_on_label = "Bomba 1 Liga (Água Alta)" if tem_bomba2 else "Bomba Liga (Água Alta)"
    b1_off_label = "Bomba 1 Desliga (Água Baixa)" if tem_bomba2 else "Bomba Desliga (Água Baixa)"

    if has_temp_data:
        fig.add_hline(y=d_on, line_dash="dash", line_color="red", annotation_text=b1_on_label, row=1, col=1)
        fig.add_hline(y=d_off, line_dash="dash", line_color="green", annotation_text=b1_off_label, row=1, col=1)
        if tem_bomba2:
            fig.add_hline(y=d_on2, line_dash="dash", line_color="#f0ad4e", annotation_text="Bomba 2 Liga (Emergência)", row=1, col=1)
            fig.add_hline(y=d_off2, line_dash="dash", line_color="#5cb85c", annotation_text="Bomba 2 Desliga", row=1, col=1)
        fig.add_hline(y=d_overflow, line_dash="dot", line_color="black", annotation_text="Topo do Poço / Borda Superior (0 cm)", row=1, col=1)

        # Subplot 2: Temperatura Local sincronizada no tempo
        fig.add_trace(
            go.Scatter(
                x=filled_df["dt_round"],
                y=filled_df["temperatura_c"],
                mode="lines",
                name="Temperatura Local (°C)",
                line=dict(color="#ff6b6b", width=2),
                hovertemplate="%{x|%d/%m/%Y %H:%M}<br>Temperatura: <b>%{y:.1f} °C</b><extra></extra>",
            ),
            row=2, col=1
        )
        if not w_fut.empty and "temperature_2m" in w_fut.columns:
            fig.add_trace(
                go.Scatter(
                    x=w_fut.index,
                    y=w_fut["temperature_2m"],
                    mode="lines",
                    name="Previsão Temp. (°C)",
                    line=dict(color="#f39c12", width=2, dash="dash"),
                    hovertemplate="%{x|%d/%m/%Y %H:%M}<br>Previsão Temp.: <b>%{y:.1f} °C</b><extra></extra>",
                ),
                row=2, col=1
            )

        fig.update_yaxes(
            title_text="Nível (cm)",
            autorange="reversed",
            row=1, col=1, secondary_y=False
        )
        fig.update_yaxes(
            title_text="Chuva (mm/h)",
            showgrid=False,
            rangemode="tozero",
            tickfont=dict(color="rgba(30, 144, 255, 0.8)"),
            title_font=dict(color="rgba(30, 144, 255, 0.8)"),
            row=1, col=1, secondary_y=True
        )
        fig.update_yaxes(
            title_text="Temp. (°C)",
            tickfont=dict(color="#e74c3c"),
            title_font=dict(color="#e74c3c"),
            row=2, col=1
        )
        fig.update_xaxes(title_text="Data / Hora", row=2, col=1)

        fig.update_layout(
            height=640,
            margin=dict(l=20, r=60, t=30, b=40),
            legend=dict(orientation="h", y=-0.15, x=0.5, xanchor="center"),
            bargap=0,
            hovermode="x unified",
        )
    else:
        fig.add_hline(y=d_on, line_dash="dash", line_color="red", annotation_text=b1_on_label)
        fig.add_hline(y=d_off, line_dash="dash", line_color="green", annotation_text=b1_off_label)
        if tem_bomba2:
            fig.add_hline(y=d_on2, line_dash="dash", line_color="#f0ad4e", annotation_text="Bomba 2 Liga (Emergência)")
            fig.add_hline(y=d_off2, line_dash="dash", line_color="#5cb85c", annotation_text="Bomba 2 Desliga")
        fig.add_hline(y=d_overflow, line_dash="dot", line_color="black", annotation_text="Topo do Poço / Borda Superior (0 cm)")
        fig.update_layout(
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
                tickfont=dict(color="rgba(30, 144, 255, 0.8)"),
                title_font=dict(color="rgba(30, 144, 255, 0.8)"),
            ),
            xaxis=dict(title="Data / Hora"),
            legend=dict(orientation="h", y=-0.2, x=0.5, xanchor="center"),
            height=520,
            margin=dict(l=20, r=60, t=30, b=40),
            bargap=0,
        )

    st.plotly_chart(fig, width="stretch")

    # Resumo Estatístico da Temperatura
    if has_temp_data:
        w_temp_valid = filled_df["temperatura_c"].dropna()
        if not w_temp_valid.empty:
            with st.expander("🌡️ Resumo da Temperatura Local Sincronizada", expanded=False):
                col_tp1, col_tp2, col_tp3 = st.columns(3)
                col_tp1.metric("Temperatura Mínima", f"{w_temp_valid.min():.1f} °C")
                col_tp2.metric("Temperatura Média", f"{w_temp_valid.mean():.1f} °C")
                col_tp3.metric("Temperatura Máxima", f"{w_temp_valid.max():.1f} °C")

    # Análise de Erro do Sensor Ultrassônico por Variação Térmica
    if has_temp_data:
        err_df = compute_ultrasonic_temperature_error(filled_df, v_esp32=343.0)
        if not err_df.empty and "erro_distancia_cm" in err_df.columns:
            with st.expander("📏 Análise de Erro do Sensor Ultrassônico por Variação Térmica (ESP32 vs Temp. Real)", expanded=False):
                st.markdown(
                    "O firmware do ESP32 utiliza velocidade do som constante: "
                    "`float distancia = duracao * 0.0343 / 2.0;` ($v_0 = 343.0\\text{ m/s}$, válida para $T \\approx 19.3^\\circ\\text{C}$).\n\n"
                    "A velocidade real do som varia com a temperatura ambiente segundo a física: "
                    "$v(T) = 331.3 \\times \\sqrt{1 + \\frac{T}{273.15}}\\text{ m/s}$. "
                    "Abaixo é apresentado o impacto real dessa variação de temperatura nas medições do poço:"
                )

                max_abs_err = err_df["erro_distancia_cm"].abs().max()
                mean_err = err_df["erro_distancia_cm"].mean()
                max_pct_err = err_df["erro_relativo_pct"].abs().max()
                v_min = err_df["v_som_m_s"].min()
                v_max = err_df["v_som_m_s"].max()

                col_err1, col_err2, col_err3, col_err4 = st.columns(4)
                col_err1.metric("Erro Absoluto Máximo", f"{max_abs_err:.1f} cm", help="Diferença máxima entre o medido pela ESP32 e o valor real corrigido")
                col_err2.metric("Erro Médio de Leitura", f"{mean_err:+.1f} cm", help="Desvio médio em todo o período monitorado")
                col_err3.metric("Desvio Relativo Máximo", f"{max_pct_err:.2f} %", help="Percentual máximo de variação gerado pela temperatura")
                col_err4.metric("Velocidade do Som Variação", f"{v_min:.1f} a {v_max:.1f} m/s", help="Constante ESP32: 343.0 m/s")

                st.markdown("---")

                fig_err = make_subplots(
                    rows=2,
                    cols=1,
                    shared_xaxes=True,
                    vertical_spacing=0.08,
                    row_heights=[0.6, 0.4],
                    specs=[[{"secondary_y": False}], [{"secondary_y": False}]],
                )

                fig_err.add_trace(
                    go.Scatter(
                        x=err_df["dt_round"],
                        y=err_df["nivel_cm"],
                        mode="markers",
                        name="Medição Bruta ESP32 (v0=343 m/s)",
                        marker=dict(size=4, color="#1f77b4"),
                    ),
                    row=1, col=1
                )
                fig_err.add_trace(
                    go.Scatter(
                        x=err_df["dt_round"],
                        y=err_df["distancia_corrigida_cm"],
                        mode="lines",
                        name="Distância Real Corrigida (Temp. Real)",
                        line=dict(color="#2ecc71", width=2),
                    ),
                    row=1, col=1
                )

                fig_err.add_trace(
                    go.Scatter(
                        x=err_df["dt_round"],
                        y=err_df["erro_distancia_cm"],
                        mode="lines",
                        name="Erro Absoluto d_ESP32 - d_Real (cm)",
                        line=dict(color="#e74c3c", width=1.8),
                        fill="tozeroy",
                        fillcolor="rgba(231, 76, 60, 0.15)",
                    ),
                    row=2, col=1
                )

                fig_err.update_yaxes(title_text="Distância ao Sensor (cm)", autorange="reversed", row=1, col=1)
                fig_err.update_yaxes(title_text="Erro (cm)", row=2, col=1)
                fig_err.update_xaxes(title_text="Data / Hora", row=2, col=1)

                fig_err.update_layout(
                    height=520,
                    margin=dict(l=20, r=20, t=20, b=30),
                    legend=dict(orientation="h", y=-0.15, x=0.5, xanchor="center"),
                    hovermode="x unified",
                )
                st.plotly_chart(fig_err, width="stretch")

    if not has_rain_data:
        st.info("ℹ️ Dados de precipitação não disponíveis para o período exibido (API Open-Meteo).")

    st.subheader("Últimos Registros")
    df_export = filled_df.copy()
    num_cols = df_export.select_dtypes(include="number").columns
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

            num_cols_kpi = 5 if tem_bomba2 else 4
            cols = st.columns(num_cols_kpi)

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

    rate_stats = compute_rate_statistics(filled_df, fator_m3h_para_cmh, vazao_bomba_m3h)
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
            st.plotly_chart(fig_ext, width="stretch")

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
                    st.plotly_chart(fig_h_ench, width="stretch")
                else:
                    st.info("Sem dados suficientes de subida para gerar o histograma.")

            with h_col2:
                if fig_h_esv:
                    st.plotly_chart(fig_h_esv, width="stretch")
                else:
                    st.info("Sem dados suficientes de esvaziamento para gerar o histograma.")

    # Apuração Precisa do Volume Expulso por Acionamento da Bomba
    with st.container(border=True):
        st.subheader("💧 Apuração Precisa do Volume Expulso por Acionamento Real da Bomba")
        st.caption(
            "Cálculo exato baseado na diferença entre o **nível mais alto da água registrado logo antes do acionamento** "
            "e o **nível mais baixo da água logo após o desligamento**. "
            "Este método compensa eventuais variações de atracamento/desatracamento das bóias elétricas."
        )

        c_calc1, c_calc2 = st.columns([1, 1])
        with c_calc1:
            st.markdown("##### 🧮 Comparação de Métodos & Recalibração de Seção")
            c_man_a, c_man_b = st.columns(2)
            with c_man_a:
                d_top_high = st.number_input(
                    "Nível Alto (distância ao topo - cm)",
                    value=148.5, step=0.5, format="%.1f",
                    help="Menor distância lida pelo sensor (água alta) antes da bomba atracar."
                )
                d_top_low = st.number_input(
                    "Nível Baixo (distância ao topo - cm)",
                    value=174.0, step=0.5, format="%.1f",
                    help="Maior distância lida pelo sensor (água baixa) após desligar."
                )
            with c_man_b:
                dur_manual_min = st.number_input(
                    "Tempo Bomba Ligada (minutos)",
                    value=3.5, step=0.1, format="%.1f",
                    help="Tempo de operação da bomba em minutos (com vazão de 37 m³/h)."
                )
                st.caption(f"⚡ Capacidade 1ª Bomba: **{vazao_bomba_m3h:.1f} m³/h** ({vazao_bomba_m3h*1000.0/60.0:.1f} L/min)")

            delta_h_manual = max(0.0, d_top_low - d_top_high)
            vol_geom_manual_L = delta_h_manual * (100.0 / fator_m3h_para_cmh * 10.0) if fator_m3h_para_cmh > 0 else delta_h_manual * 55.0
            vol_bomba_manual_L = (vazao_bomba_m3h * 1000.0) * (dur_manual_min / 60.0)
            fator_rec_L_cm_man = vol_bomba_manual_L / delta_h_manual if delta_h_manual > 0 else 55.0
            fator_rec_cmh_man = 1000.0 / fator_rec_L_cm_man if fator_rec_L_cm_man > 0 else DEFAULT_FATOR_M3H_PARA_CMH

            res_col1, res_col2 = st.columns(2)
            res_col1.metric("1️⃣ Volume p/ Nível (Δh)", f"{vol_geom_manual_L:.0f} L", delta=f"Δh = {delta_h_manual:.1f} cm", delta_color="off")
            res_col2.metric("2️⃣ Volume p/ Tempo Bomba", f"{vol_bomba_manual_L:.0f} L", delta=f"{dur_manual_min:.1f} min × 37m³/h", delta_color="off")
            st.caption(
                f"🎯 **Recalibração Hidráulica Resultante:** **{fator_rec_L_cm_man:.1f} Litros/cm** "
                f"(Área poço: **{fator_rec_L_cm_man/10.0:.2f} m²** | Fator: **{fator_rec_cmh_man:.3f} cm/h por m³/h**)"
            )

        with c_calc2:
            st.markdown("##### 📋 Acionamentos Reais & Recalibração por Telemetria")
            df_cycles, stats_cycles = detect_pump_cycles_and_volumes(filled_df, fator_m3h_para_cmh, vazao_bomba_m3h=vazao_bomba_m3h)
            if not df_cycles.empty:
                m_cyc1, m_cyc2, m_cyc3, m_cyc4 = st.columns(4)
                m_cyc1.metric("Acionamentos", f"{stats_cycles['total_acionamentos']}")
                m_cyc2.metric("Vol. Total Expulso", f"{stats_cycles['volume_total_m3']:.3f} m³", delta=f"{stats_cycles['volume_total_m3']*1000:,.0f} L", delta_color="off")
                m_cyc3.metric("Vol. Nível Médio", f"{stats_cycles['volume_medio_L']:.0f} L")
                m_cyc4.metric("Fator Recalib. Médio", f"{stats_cycles.get('fator_recalib_medio_L_cm', 55.0):.1f} L/cm", delta=f"{stats_cycles.get('area_poco_recalib_m2', 5.50):.2f} m²", delta_color="off")

                st.dataframe(
                    df_cycles.rename(columns={
                        "t_liga": "Hora LIGA",
                        "t_desliga": "Hora DESLIGA",
                        "duracao_min": "Duração (min)",
                        "d_alto_cm": "Nível Máx (cm)",
                        "d_baixo_cm": "Nível Mín (cm)",
                        "delta_cm": "Queda Δh (cm)",
                        "vol_expulso_L": "Vol. Nível (L)",
                        "vol_bomba_L": "Vol. Bomba (L)",
                        "fator_recalib_L_cm": "Recalib (L/cm)",
                        "vol_expulso_m3": "Volume (m³)",
                    }),
                    width="stretch",
                    height=220,
                )
            else:
                st.info("Nenhum ciclo completo de bomba identificado no período carregado.")

        if not df_cycles.empty:
            st.markdown("---")
            st.markdown("##### 📈 Gráfico Temporal e Volume Acumulado Expulso pelas Bombas")
            st.caption(
                "Cada barra representa um acionamento da bomba: a **largura** corresponde exatamente ao tempo "
                "em que a bomba ficou ligada (Início → Fim) e a **altura** representa o volume de água expulso em Litros."
            )
            fig_v_cycles = plot_pump_cycles_volume_chart(df_cycles)
            if fig_v_cycles is not None:
                st.plotly_chart(fig_v_cycles, width="stretch")
