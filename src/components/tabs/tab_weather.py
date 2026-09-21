"""
Componente da Aba 4: Dados de Precipitação e Temperatura Local (Open-Meteo).
"""

from typing import Optional
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.config import DEFAULT_FATOR_M3H_PARA_CMH, DEFAULT_VAZAO_BOMBA_M3H
from src.utils import now_local
from src.components.charts import (
    plot_rain_vs_pump_correlation_scatter,
    plot_rain_vs_pump_cumulative_timeline,
)


def render_tab_weather(
    weather_df: pd.DataFrame,
    latitude: float,
    longitude: float,
    factor_mm_cm: float,
    filled_df: Optional[pd.DataFrame] = None,
    fator_m3h_para_cmh: float = DEFAULT_FATOR_M3H_PARA_CMH,
    vazao_bomba_m3h: float = DEFAULT_VAZAO_BOMBA_M3H,
    latest_temp: Optional[float] = None,
    temp_min_24h: Optional[float] = None,
    temp_max_24h: Optional[float] = None,
    temp_min_period: Optional[float] = None,
    temp_max_period: Optional[float] = None,
    temp_avg_period: Optional[float] = None,
) -> None:
    """Renderiza a Aba 4 com histórico e previsões horárias e diárias de precipitação e temperatura."""
    st.subheader("🌦️ Dados de Precipitação e Temperatura Local – Open-Meteo")
    st.caption(f"Localização: lat {latitude:.4f}, lon {longitude:.4f} | Fonte: Open-Meteo (histórico + previsão 7 dias)")

    if weather_df.empty:
        st.error("Não foi possível obter dados meteorológicos da API Open-Meteo. Verifique a conexão ou as coordenadas.")
        return

    now_tz = now_local()

    # Separar histórico e previsão
    hist_df = weather_df[weather_df.index <= now_tz].copy()
    fcast_df = weather_df[weather_df.index > now_tz].copy()

    # ── Acumulado de chuva no período mostrado no dashboard ──────────────────────
    precip_period = None
    window_str = "Período"
    period_help = "Chuva acumulada na janela selecionada no dashboard"

    if filled_df is not None and not filled_df.empty:
        t_col = "dt_round" if "dt_round" in filled_df.columns else "timestamp"
        if t_col in filled_df.columns:
            t_min = filled_df[t_col].min()
            t_max = filled_df[t_col].max()
            if pd.notna(t_min) and pd.notna(t_max):
                delta_h = (t_max - t_min).total_seconds() / 3600.0
                if delta_h >= 48:
                    window_str = f"{delta_h / 24.0:.1f} dias"
                elif delta_h > 0:
                    window_str = f"{delta_h:.0f}h"
                else:
                    window_str = "Período"

                if "precipitation_5min" in filled_df.columns:
                    precip_period = float(filled_df["precipitation_5min"].sum())
                elif not weather_df.empty:
                    precip_period = float(
                        weather_df[(weather_df.index >= t_min) & (weather_df.index <= t_max)]["precipitation"].sum()
                    )

                if precip_period is not None:
                    aporte_cm = precip_period * factor_mm_cm
                    period_help = (
                        f"Chuva acumulada total na janela carregada no dashboard: "
                        f"{t_min.strftime('%d/%m/%Y %H:%M')} até {t_max.strftime('%d/%m/%Y %H:%M')} ({window_str}). "
                        f"Aporte teórico estimado no poço: {aporte_cm:.1f} cm (fator {factor_mm_cm:.2f} cm/mm)."
                    )

    last_24h = weather_df[weather_df.index >= (now_tz - pd.Timedelta(hours=24))]
    next_24h = weather_df[
        (weather_df.index > now_tz) & (weather_df.index <= (now_tz + pd.Timedelta(hours=24)))
    ]
    next_48h = weather_df[
        (weather_df.index > now_tz) & (weather_df.index <= (now_tz + pd.Timedelta(hours=48)))
    ]
    next_7d = weather_df[
        (weather_df.index > now_tz) & (weather_df.index <= (now_tz + pd.Timedelta(days=7)))
    ]
    total_7d_precip = next_7d["precipitation"].sum() if not next_7d.empty else 0.0
    max_intensity = weather_df["precipitation"].max()

    st.markdown("#### 🌧️ Métricas de Precipitação")
    col_w0, col_w1, col_w2, col_w3, col_w4, col_w5 = st.columns(6)
    col_w0.metric(
        "Acumulado no Período",
        f"{precip_period:.1f} mm" if precip_period is not None else "N/A",
        delta=f"Janela: {window_str}" if window_str else None,
        delta_color="off",
        help=period_help,
    )
    col_w1.metric("Acumulado últimas 24h", f"{last_24h['precipitation'].sum():.1f} mm")
    col_w2.metric("Previsão próximas 24h", f"{next_24h['precipitation'].sum():.1f} mm")
    col_w3.metric("Previsão próximas 48h", f"{next_48h['precipitation'].sum():.1f} mm")
    col_w4.metric("Previsão próximos 7 dias", f"{total_7d_precip:.1f} mm")
    col_w5.metric("Intensidade máx.", f"{max_intensity:.1f} mm/h")

    if "temperature_2m" in weather_df.columns:
        st.markdown("#### 🌡️ Métricas de Temperatura Local")
        col_wt1, col_wt2, col_wt3, col_wt4, col_wt5 = st.columns(5)
        col_wt1.metric("Temperatura Atual", f"{latest_temp:.1f} °C" if latest_temp is not None else "N/A")
        col_wt2.metric("Temp. Mínima (24h)", f"{temp_min_24h:.1f} °C" if temp_min_24h is not None else "N/A")
        col_wt3.metric("Temp. Máxima (24h)", f"{temp_max_24h:.1f} °C" if temp_max_24h is not None else "N/A")
        col_wt4.metric("Faixa no Período", f"{temp_min_period:.1f}°C a {temp_max_period:.1f}°C" if temp_min_period is not None else "N/A")
        col_wt5.metric("Média no Período", f"{temp_avg_period:.1f} °C" if temp_avg_period is not None else "N/A")

    st.markdown("---")

    # Gráfico de precipitação horária completo
    st.subheader("Precipitação Horária – Histórico e Previsão (7 Dias)")
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
    st.plotly_chart(fig_rain, width="stretch")

    # Gráfico de Variação da Temperatura Local Horária
    if "temperature_2m" in weather_df.columns and not weather_df["temperature_2m"].dropna().empty:
        st.subheader("🌡️ Variação da Temperatura Local – Histórico e Previsão (7 Dias)")
        fig_temp = go.Figure()

        if not hist_df.empty and "temperature_2m" in hist_df.columns:
            fig_temp.add_trace(
                go.Scatter(
                    x=hist_df.index,
                    y=hist_df["temperature_2m"],
                    mode="lines",
                    name="Temperatura Histórica (°C)",
                    line=dict(color="#e74c3c", width=2),
                    hovertemplate="%{x|%d/%m/%Y %H:%M}<br>Temp. Histórica: <b>%{y:.1f} °C</b><extra></extra>",
                )
            )
        if not fcast_df.empty and "temperature_2m" in fcast_df.columns:
            fig_temp.add_trace(
                go.Scatter(
                    x=fcast_df.index,
                    y=fcast_df["temperature_2m"],
                    mode="lines",
                    name="Previsão Temperatura (°C)",
                    line=dict(color="#f39c12", width=2, dash="dash"),
                    hovertemplate="%{x|%d/%m/%Y %H:%M}<br>Previsão Temp.: <b>%{y:.1f} °C</b><extra></extra>",
                )
            )

        fig_temp.add_shape(
            type="line",
            x0=_now_str,
            x1=_now_str,
            y0=0,
            y1=1,
            yref="paper",
            line=dict(dash="dash", color="orange", width=1.5),
        )
        fig_temp.add_annotation(
            x=_now_str,
            y=1,
            yref="paper",
            text="Agora",
            showarrow=False,
            xanchor="left",
            font=dict(color="orange"),
        )
        fig_temp.update_layout(
            xaxis_title="Data / Hora",
            yaxis_title="Temperatura (°C)",
            legend=dict(orientation="h", y=-0.2, x=0.5, xanchor="center"),
            height=380,
            margin=dict(l=20, r=20, t=20, b=40),
            hovermode="x unified",
        )
        st.plotly_chart(fig_temp, width="stretch")

    # ── Correlação Hidráulica: Chuva Acumulada (mm) vs. Volume Expulso (Litros) ──
    if filled_df is not None and not filled_df.empty:
        st.markdown("---")
        st.subheader("💧 Correlação Hidráulica: Chuva Acumulada (mm) vs. Volume Expulso (Litros)")
        st.caption(
            "Análise quantitativa empírica baseada no histórico de telemetria do poço: correlaciona diretamente os milímetros de chuva "
            "com os Litros de água efetivamente expulsos pelos acionamentos reais da bomba."
        )

        fig_scatter, stats_corr = plot_rain_vs_pump_correlation_scatter(
            filled_df,
            fator_m3h_para_cmh=fator_m3h_para_cmh,
            vazao_bomba_m3h=vazao_bomba_m3h,
        )

        fig_cum = plot_rain_vs_pump_cumulative_timeline(
            filled_df,
            fator_m3h_para_cmh=fator_m3h_para_cmh,
            vazao_bomba_m3h=vazao_bomba_m3h,
        )

        if stats_corr and stats_corr.get("days_count", 0) > 0:
            c_cr1, c_cr2, c_cr3, c_cr4, c_cr5 = st.columns(5)
            c_cr1.metric("🌧️ Chuva no Histórico", f"{stats_corr['total_rain_mm']:.1f} mm")
            c_cr2.metric(
                "💧 Vol. Total Expulso",
                f"{stats_corr['total_vol_L']:,.0f} L",
                delta=f"{stats_corr['total_vol_m3']:.2f} m³",
                delta_color="off",
            )
            c_cr3.metric("⚡ Ciclos da Bomba", f"{stats_corr['total_cycles']} ciclos")

            r2_val = stats_corr["r2"]
            r_val = stats_corr["r_pearson"]
            r_qual = "Forte" if abs(r_val) >= 0.7 else ("Moderada" if abs(r_val) >= 0.4 else "Fraca / Lençol")
            c_cr4.metric(
                "📈 Correlação (R²)",
                f"{r2_val:.2f}",
                delta=f"R = {r_val:+.2f} ({r_qual})",
                delta_color="normal" if r_val > 0.5 else "off",
            )

            area_efetiva = stats_corr["area_efetiva_m2"]
            c_cr5.metric(
                "📐 Área Efetiva Estimada",
                f"{area_efetiva:.0f} m²",
                delta=f"{stats_corr['slope_L_mm']:.1f} L/mm",
                delta_color="off",
                help="Relação empírica entre Litros expulsos e milímetros de chuva (1 mm = 1 L/m² de área de captação).",
            )

            tipo_grafico = st.radio(
                "Modo de Visualização:",
                options=["📈 Dispersão Diária & Linha de Tendência", "⏱️ Evolução Temporal Contínua (Eixos Duplos)"],
                horizontal=True,
                key="tipo_graf_corr",
            )

            if "Dispersão" in tipo_grafico and fig_scatter is not None:
                st.plotly_chart(fig_scatter, width="stretch")
            elif fig_cum is not None:
                st.plotly_chart(fig_cum, width="stretch")

            df_daily_show = stats_corr.get("df_daily")
            if df_daily_show is not None and not df_daily_show.empty:
                with st.expander("Ver tabela de dados diários de chuva vs. volume expulso", icon=":material/table_chart:"):
                    st.dataframe(
                        df_daily_show[[
                            "data_str", "chuva_mm", "vol_expulso_L", "vol_expulso_m3", "n_ciclos", "ratio_L_mm"
                        ]].rename(columns={
                            "data_str": "Data",
                            "chuva_mm": "Chuva no Dia (mm)",
                            "vol_expulso_L": "Volume Expulso (Litros)",
                            "vol_expulso_m3": "Volume Expulso (m³)",
                            "n_ciclos": "Acionamentos da Bomba",
                            "ratio_L_mm": "Relação (L/mm)",
                        }),
                        width="stretch",
                        hide_index=True,
                    )
        else:
            st.info("Dados insuficientes para gerar correlação no período selecionado.")

    # Resumo Diário da Previsão dos Próximos 7 Dias
    st.subheader("📅 Resumo Diário da Previsão – Próximos 7 Dias")
    if not fcast_df.empty:
        daily_grp = fcast_df.groupby(pd.DatetimeIndex(fcast_df.index).date)
        daily_rows = []
        for date_val, group in daily_grp:
            p_tot = group["precipitation"].sum()
            p_max = group["precipitation"].max()

            if p_tot == 0:
                cond = "☀️ Sem chuva"
            elif p_tot < 5.0:
                cond = "🌦️ Chuva fraca"
            elif p_tot < 25.0:
                cond = "🌧️ Chuva moderada"
            else:
                cond = "⛈️ Chuva forte"

            d_item = date_val[0] if isinstance(date_val, tuple) else date_val
            day_str = pd.Timestamp(d_item).strftime("%d/%m/%Y (%A)")
            day_str_pt = (
                day_str.replace("Monday", "Segunda-feira")
                .replace("Tuesday", "Terça-feira")
                .replace("Wednesday", "Quarta-feira")
                .replace("Thursday", "Quinta-feira")
                .replace("Friday", "Sexta-feira")
                .replace("Saturday", "Sábado")
                .replace("Sunday", "Domingo")
            )
            daily_rows.append({
                "Data / Dia": day_str_pt,
                "Precipitação Acumulada (mm)": round(p_tot, 1),
                "Intensidade Máxima (mm/h)": round(p_max, 1),
                "Condição Estimada": cond,
            })

        if daily_rows:
            df_daily = pd.DataFrame(daily_rows)
            st.dataframe(df_daily, width="stretch", hide_index=True)
            if total_7d_precip > 0:
                nivel_equiv_7d = total_7d_precip * factor_mm_cm
                st.info(
                    f"📊 Acumulado total previsto nos 7 dias: **{total_7d_precip:.1f} mm**, "
                    f"equivalente a um aporte estimado de **{nivel_equiv_7d:.1f} cm** no nível do poço."
                )
    else:
        st.info("Sem dados de previsão diária disponíveis.")

    # Previsão detalhada próximas 48h
    st.subheader("📋 Previsão Detalhada – Próximas 48 Horas")
    if not next_48h.empty:
        fcast_table = next_48h.rename(columns={"precipitation": "Precipitação (mm/h)", "temperature_2m": "Temperatura (°C)"}).copy()
        fcast_table.index = pd.to_datetime(fcast_table.index).strftime("%d/%m/%Y %H:%M")
        fcast_table_rain = fcast_table[fcast_table["Precipitação (mm/h)"] > 0]
        if fcast_table_rain.empty:
            st.success("✅ Nenhuma precipitação prevista nas próximas 48 horas.")
        else:
            st.dataframe(fcast_table_rain, width="stretch")
            total_prev = next_48h["precipitation"].sum()
            nivel_equiv_cm = total_prev * factor_mm_cm
            st.info(
                f"📈 Volume previsto nas próximas 48h: **{total_prev:.1f} mm** (variação estimada de "
                f"**{nivel_equiv_cm:.1f} cm** no poço com fator {factor_mm_cm:.2f} cm/mm)."
            )
    else:
        st.info("Dados de previsão não disponíveis.")

    # Previsão Horária Completa (7 dias)
    with st.expander("Ver previsão horária completa (próximos 7 dias)", icon=":material/calendar_month:"):
        if not fcast_df.empty:
            show_fcast_7d = fcast_df.rename(columns={"precipitation": "Precipitação (mm/h)", "temperature_2m": "Temperatura (°C)"}).copy()
            show_fcast_7d.index = pd.to_datetime(show_fcast_7d.index).strftime("%d/%m/%Y %H:%M")
            st.dataframe(show_fcast_7d, width="stretch")
        else:
            st.write("Sem previsão horária disponível.")

    # Histórico completo dos últimos dias
    with st.expander("Ver histórico de precipitação (últimos dias)", icon=":material/folder:"):
        if not hist_df.empty:
            show_hist = hist_df.rename(columns={"precipitation": "Precipitação (mm/h)", "temperature_2m": "Temperatura (°C)"}).copy()
            show_hist.index = pd.to_datetime(show_hist.index).strftime("%d/%m/%Y %H:%M")
            st.dataframe(show_hist, width="stretch")
        else:
            st.write("Sem dados históricos disponíveis.")
