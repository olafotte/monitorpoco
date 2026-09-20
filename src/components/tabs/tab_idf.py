"""
Componente da Aba 6: Análise Histórica de Chuvas & Curvas IDF (Intensidade-Duração-Frequência).
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.utils import now_local
from src.data.weather import fetch_historical_5years_precip
from src.data.adolfo_konder import load_adolfo_konder_dataset
from src.data.defesa_civil import fetch_defesa_civil_rankings
from src.models.gumbel import fit_gumbel, gumbel_quantile


def render_tab_idf(
    latitude: float,
    longitude: float,
    era5_correction: float,
    factor_mm_cm: float,
    r_pump_param: float,
    r_gnd_param: float,
    tem_bomba2: bool,
    d_on: float,
    d_off: float,
    d_on2: float,
    d_off2: float,
    d_overflow: float = 0.0,
) -> None:
    """Renderiza a Aba 6 com modelagem Gumbel, curvas IDF e validação contra Defesa Civil."""
    st.subheader("📊 Análise Histórica de Chuvas & Curva IDF")

    intensidade_sat_mmh = r_pump_param / factor_mm_cm

    col_anos, col_btn = st.columns([3, 1])
    with col_anos:
        anos_analise = st.slider(
            "Período de análise (anos)",
            min_value=1, max_value=15, value=5, step=1,
            key="hist_anos_slider",
            help="Número de anos de dados do Open-Meteo Archive API a buscar. "
                 "Mais anos = curva IDF mais robusta, mas requisição maior (pode levar até 30s na 1ª vez).",
        )
    with col_btn:
        st.write("")
        if st.button("🔄 Limpar cache", key="clear_hist_cache", help="Força novo download dos dados históricos"):
            fetch_historical_5years_precip.clear()
            st.rerun()

    end_ref = now_local().normalize() - pd.Timedelta(days=1)
    hist_start_str = (end_ref - pd.DateOffset(years=anos_analise)).strftime("%Y-%m-%d")
    hist_start_label = f"{anos_analise} ano{'s' if anos_analise != 1 else ''} (desde {hist_start_str})"

    dc_early = fetch_defesa_civil_rankings()
    if dc_early:
        all_dc_dates = []
        for _df_dur in dc_early.values():
            for _raw in _df_dur["Data/Hora"]:
                try:
                    all_dc_dates.append(pd.to_datetime(_raw, dayfirst=True))
                except Exception:
                    pass
        if all_dc_dates:
            oldest_dc = min(all_dc_dates)
            n_anos_dc = (pd.Timestamp.now() - oldest_dc).days / 365.25
            st.caption(
                f"💡 O evento mais antigo registrado pela Defesa Civil é de "
                f"**{oldest_dc.strftime('%d/%m/%Y')}** (~{n_anos_dc:.0f} anos). "
                f"Selecionar **{n_anos_dc:.0f}+ anos** permite comparar os eventos históricos oficiais com o ERA5."
            )

    with st.spinner(f"⏳ Buscando dados históricos de precipitação ({hist_start_label})..."):
        hist5_df, hist_err = fetch_historical_5years_precip(latitude, longitude, start_date_str=hist_start_str)

    if hist5_df.empty and hist_start_str:
        st.warning(
            f"⚠️ Falha ao buscar dados desde {hist_start_str}: `{hist_err}`. "
            "Tentando janela padrão de 5 anos..."
        )
        with st.spinner("⏳ Buscando 5 anos de dados (fallback)..."):
            hist5_df, hist_err = fetch_historical_5years_precip(latitude, longitude, start_date_str="")

    if hist5_df.empty:
        st.error(
            f"Não foi possível obter dados históricos da Open-Meteo Archive API. "
            f"Erro: `{hist_err}`. Verifique a conexão com a internet e as coordenadas."
        )
        return

    fonte_tab6 = st.radio(
        "Selecione a Base de Dados para Análise Histórica & IDF:",
        options=[
            "📡 Modelo ERA5 Corregido (Série Histórica com Fator de Correção)",
            "🌧️ Estação Real: Ponte Adolfo Konder (Dados Pluviométricos Reais 2021-2025)",
        ],
        index=0,
        horizontal=True,
    )

    hist5_raw_df = hist5_df.copy()

    if "Ponte Adolfo Konder" in fonte_tab6:
        konder_df = load_adolfo_konder_dataset()
        if not konder_df.empty:
            hist5_df = pd.DataFrame({"precipitation": konder_df["precip_15min"].resample("1h").sum()}).dropna()
            st.info("✅ **Análise baseada nas medições reais da Estação Ponte Adolfo Konder (2021–2025).** Fator ERA5 desativado para dados reais de pluviômetro.")
        else:
            st.warning("⚠️ Dados da Estação Ponte Adolfo Konder não encontrados. Utilizando ERA5 Corrigido.")
            if era5_correction != 1.0:
                hist5_df = hist5_df.copy()
                hist5_df["precipitation"] = hist5_df["precipitation"] * era5_correction
    else:
        if era5_correction != 1.0:
            hist5_df = hist5_df.copy()
            hist5_df["precipitation"] = hist5_df["precipitation"] * era5_correction

    anos_disponiveis = sorted(hist5_df.index.year.unique())
    n_anos = len(anos_disponiveis)

    periodo_real = f"{anos_disponiveis[0]}–{anos_disponiveis[-1]} ({n_anos} anos)"
    correcao_txt = f" | Fator ERA5: ×{era5_correction:.2f}" if era5_correction != 1.0 else ""
    st.caption(
        f"Dados horários de {periodo_real} | lat {latitude:.4f}, lon {longitude:.4f} "
        f"| Fonte: Open-Meteo Archive API (ERA5){correcao_txt} | Distribuição: Gumbel (método dos momentos)"
    )

    chuva_anual_media = hist5_df["precipitation"].sum() / n_anos
    max_horario = hist5_df["precipitation"].max()
    n_eventos_sat = (hist5_df["precipitation"] >= intensidade_sat_mmh).sum()
    freq_sat_ano = n_eventos_sat / n_anos

    col_h1, col_h2, col_h3, col_h4 = st.columns(4)
    col_h1.metric("Período analisado", f"{n_anos} anos ({anos_disponiveis[0]}–{anos_disponiveis[-1]})",
                   help="Período obtido alinhado ao evento mais antigo registrado pela Defesa Civil de Blumenau")
    col_h2.metric("Chuva média anual", f"{chuva_anual_media:.0f} mm/ano")
    col_h3.metric("Máx. intensidade horária", f"{max_horario:.1f} mm/h")
    col_h4.metric(
        "Eventos saturantes (1h)",
        f"{n_eventos_sat} total | {freq_sat_ano:.1f}/ano",
        help=f"Horas com precipitação ≥ {intensidade_sat_mmh:.1f} mm/h (limite de saturação da bomba)",
    )

    st.markdown("---")

    duracoes_h = [1, 2, 3, 6, 12, 24]
    trs = [2, 5, 10, 25, 50, 100]

    idf_results = {}
    gumbel_params = {}

    for dur in duracoes_h:
        rolled = hist5_df["precipitation"].rolling(window=dur, min_periods=dur).sum()
        rolled_intensity = rolled / dur
        annual_max = rolled_intensity.groupby(hist5_df.index.year).max().dropna().values
        if len(annual_max) < 2:
            continue
        alpha, u = fit_gumbel(annual_max)
        gumbel_params[dur] = (alpha, u, annual_max)
        idf_results[dur] = {}
        for tr in trs:
            idf_results[dur][tr] = max(gumbel_quantile(alpha, u, tr), 0.0)

    # Gráfico 1 – Precipitação anual acumulada
    st.subheader("🌧️ Precipitação Anual Acumulada")
    anual_acc = hist5_df["precipitation"].groupby(hist5_df.index.year).sum().reset_index()
    anual_acc.columns = ["Ano", "Precipitação (mm)"]
    fig_anual = go.Figure()
    fig_anual.add_trace(go.Bar(
        x=anual_acc["Ano"].astype(str),
        y=anual_acc["Precipitação (mm)"],
        marker_color="rgba(30, 100, 220, 0.75)",
        name="Acumulado anual",
    ))
    fig_anual.add_hline(
        y=chuva_anual_media,
        line_dash="dash", line_color="orange",
        annotation_text=f"Média: {chuva_anual_media:.0f} mm",
        annotation_position="top right",
    )
    fig_anual.update_layout(
        xaxis_title="Ano", yaxis_title="Precipitação acumulada (mm)",
        height=320, margin=dict(l=20, r=20, t=20, b=30),
    )
    st.plotly_chart(fig_anual, width="stretch")

    # Gráfico 2 – Histograma de intensidades horárias
    st.subheader("📊 Distribuição de Intensidades Horárias")
    precip_positivo = hist5_df["precipitation"][hist5_df["precipitation"] > 0]
    fig_hist = go.Figure()
    fig_hist.add_trace(go.Histogram(
        x=precip_positivo,
        nbinsx=60,
        name="Horas com chuva",
        marker_color="rgba(30, 100, 220, 0.6)",
        xbins=dict(start=0, end=max(precip_positivo.max(), intensidade_sat_mmh * 1.5), size=2),
    ))
    fig_hist.add_vline(
        x=intensidade_sat_mmh,
        line_dash="dash", line_color="red",
    )
    fig_hist.add_annotation(
        x=intensidade_sat_mmh, y=1, yref="paper",
        text=f"Saturação bomba<br>{intensidade_sat_mmh:.1f} mm/h",
        showarrow=True, arrowhead=2, arrowcolor="red",
        font=dict(color="red", size=11), xanchor="left",
    )
    fig_hist.update_layout(
        xaxis_title="Intensidade (mm/h)",
        yaxis_title="Número de horas",
        height=320, margin=dict(l=20, r=20, t=20, b=30),
    )
    st.plotly_chart(fig_hist, width="stretch")

    n_eventos_sat_5anos = int((hist5_df["precipitation"] >= intensidade_sat_mmh).sum())
    if n_eventos_sat_5anos > 0:
        st.warning(
            f"⚠️ Em **{n_anos} anos** ocorreram **{n_eventos_sat_5anos} horas** com intensidade ≥ {intensidade_sat_mmh:.1f} mm/h "
            f"(limite de saturação da bomba) – média de **{freq_sat_ano:.1f} evento(s)/ano**. "
            "Nesses momentos o poço enche mais rápido do que a bomba consegue esvaziar."
        )
    else:
        st.success(
            f"✅ Nenhuma hora com intensidade ≥ {intensidade_sat_mmh:.1f} mm/h nos últimos {n_anos} anos. "
            "O sistema não foi saturado no período analisado."
        )

    st.markdown("---")

    # Gráfico 3 – Curva IDF
    st.subheader("📈 Curva IDF – Intensidade–Duração–Frequência")
    st.caption(
        "Distribuição de Gumbel ajustada às máximas anuais horárias. "
        "Cada curva representa um período de retorno (Tr)."
    )

    if idf_results:
        palette = [
            "#1a73e8", "#34a853", "#fbbc04", "#ea4335", "#9c27b0", "#00bcd4"
        ]
        fig_idf = go.Figure()
        for i, tr in enumerate(trs):
            y_idf = [idf_results.get(d, {}).get(tr, None) for d in duracoes_h]
            fig_idf.add_trace(go.Scatter(
                x=duracoes_h,
                y=y_idf,
                mode="lines+markers",
                name=f"Tr = {tr} anos",
                line=dict(color=palette[i % len(palette)], width=2.5 if tr == 25 else 1.5,
                          dash="solid" if tr == 25 else "dot" if tr >= 50 else "solid"),
                marker=dict(size=7),
            ))
        fig_idf.add_hline(
            y=intensidade_sat_mmh,
            line_dash="dash", line_color="red",
            annotation_text=f"Saturação bomba ({intensidade_sat_mmh:.1f} mm/h)",
            annotation_position="top right",
        )
        fig_idf.update_layout(
            xaxis=dict(title="Duração (horas)", tickvals=duracoes_h,
                       ticktext=[f"{d}h" for d in duracoes_h]),
            yaxis_title="Intensidade média (mm/h)",
            legend=dict(orientation="h", y=-0.25, x=0.5, xanchor="center"),
            height=420, margin=dict(l=20, r=20, t=20, b=60),
        )
        st.plotly_chart(fig_idf, width="stretch")

    # Tabela IDF completa
    st.subheader("📋 Tabela IDF – Intensidades por Duração e Período de Retorno (mm/h)")
    if idf_results:
        idf_table = pd.DataFrame(
            {f"Tr={tr}a": [idf_results.get(d, {}).get(tr, None) for d in duracoes_h] for tr in trs},
            index=[f"{d}h" for d in duracoes_h],
        ).round(1)
        idf_table.index.name = "Duração"

        def highlight_tr25(col):
            return ["background-color: rgba(234,67,53,0.15); font-weight:bold" if col.name == "Tr=25a" else "" for _ in col]

        st.dataframe(idf_table.style.apply(highlight_tr25), width="stretch")

    st.markdown("---")

    # Destaque: Chuva de projeto Tr=25 anos
    st.subheader("🚨 Chuva de Projeto – Tr = 25 anos")
    tr25_1h = idf_results.get(1, {}).get(25, None)

    col_idf1, col_idf2, col_idf3 = st.columns(3)
    if tr25_1h:
        col_idf1.metric("Intensidade Tr=25 (1h)", f"{tr25_1h:.1f} mm/h")
        vol_poco_tr25_1h = tr25_1h * factor_mm_cm
        col_idf2.metric("Entrada equivalente no poço (1h)", f"{vol_poco_tr25_1h:.1f} cm/h")
        col_idf3.metric(
            "Excede saturação da bomba?",
            "SIM ⚠️" if tr25_1h > intensidade_sat_mmh else "NÃO ✅",
            delta=f"{tr25_1h - intensidade_sat_mmh:+.1f} mm/h",
            delta_color="inverse",
        )

    # Simulação da chuva de projeto Tr=25 no poço
    if tr25_1h:
        st.markdown("#### Simulação do Impacto no Poço – Chuva Tr=25 anos (1 hora)")
        sim_dur_h_idf = st.selectbox(
            "Duração da chuva de projeto",
            options=[f"{d}h" for d in duracoes_h],
            index=0,
            key="idf_dur_select",
        )
        dur_sel = int(sim_dur_h_idf.replace("h", ""))
        intens_sel = idf_results.get(dur_sel, {}).get(25, tr25_1h)
        taxa_entrada_idf = intens_sel * factor_mm_cm

        dt_h_idf = 5.0 / 60.0
        total_h_idf = max(dur_sel * 1.5, 6.0)
        n_steps_idf = int(total_h_idf / dt_h_idf)
        t_idf = np.linspace(0, total_h_idf, n_steps_idf)

        lvls_idf = []
        curr_idf = d_off
        p1_idf = False
        p2_idf = False
        ovf_idf = False
        ovf_idf_t = None

        for ti in t_idf:
            r_in_idf = (r_gnd_param + taxa_entrada_idf) if ti <= dur_sel else r_gnd_param

            if not p1_idf and curr_idf <= d_on:
                p1_idf = True
            elif p1_idf and curr_idf >= d_off:
                p1_idf = False

            if tem_bomba2:
                if not p2_idf and curr_idf <= d_on2:
                    p2_idf = True
                elif p2_idf and curr_idf >= d_off2:
                    p2_idf = False
            else:
                p2_idf = False

            r_act_idf = (int(p1_idf) + int(p2_idf)) * r_pump_param
            curr_idf += (r_act_idf - r_in_idf) * dt_h_idf

            lvls_idf.append(curr_idf)
            if not ovf_idf and curr_idf <= d_overflow:
                ovf_idf = True
                ovf_idf_t = ti

        fig_idf_sim = go.Figure()
        fig_idf_sim.add_vrect(
            x0=0, x1=dur_sel,
            fillcolor="rgba(30, 144, 255, 0.08)", line_width=0,
            annotation_text="Duração da chuva", annotation_position="top left",
        )
        fig_idf_sim.add_trace(go.Scatter(
            x=t_idf, y=lvls_idf,
            mode="lines",
            name=f"Nível poço (Tr=25a, {dur_sel}h)",
            line=dict(color="#e74c3c", width=2.5),
        ))
        fig_idf_sim.add_hline(y=d_on, line_dash="dash", line_color="red",
                              annotation_text="Bóia 1 LIGA", annotation_position="top right")
        fig_idf_sim.add_hline(y=d_off, line_dash="dash", line_color="green",
                              annotation_text="Bóia 1 DESLIGA", annotation_position="top right")
        if tem_bomba2:
            fig_idf_sim.add_hline(y=d_on2, line_dash="dash", line_color="orange",
                                  annotation_text="Bóia 2 LIGA (Emergência)", annotation_position="top right")
            fig_idf_sim.add_hline(y=d_off2, line_dash="dash", line_color="teal",
                                  annotation_text="Bóia 2 DESLIGA", annotation_position="top right")
        fig_idf_sim.add_hline(y=d_overflow, line_dash="dot", line_color="black",
                              annotation_text="Nível Crítico", annotation_position="top right")
        fig_idf_sim.add_hrect(
            y0=0, y1=d_overflow, fillcolor="rgba(231,76,60,0.08)", line_width=0,
        )
        fig_idf_sim.update_layout(
            title=f"Chuva Tr=25 anos | {dur_sel}h | {intens_sel:.1f} mm/h | Partida: d_off={d_off:.0f} cm",
            xaxis_title="Tempo (horas)",
            yaxis=dict(autorange="reversed", title="Nível da Água (cm abaixo do topo do poço)"),
            height=420, margin=dict(l=20, r=20, t=50, b=30),
        )
        st.plotly_chart(fig_idf_sim, width="stretch")

        if ovf_idf and ovf_idf_t is not None:
            st.error(
                f"🚨 **TRANSBORDO ESPERADO** para chuva Tr=25 anos ({dur_sel}h, {intens_sel:.1f} mm/h): "
                f"o poço atingiria o nível crítico em **{ovf_idf_t*60:.0f} minutos** "
                f"({ovf_idf_t:.2f} horas) após o início da chuva!"
            )
        else:
            st.success(
                f"✅ O sistema suporta a chuva de projeto Tr=25 anos ({dur_sel}h, {intens_sel:.1f} mm/h) "
                "sem transbordo nas condições simuladas."
            )

    # Máximas anuais (expander)
    with st.expander("Ver máximas anuais por duração (entrada do ajuste Gumbel)", icon=":material/bar_chart:"):
        max_table_data = {}
        for dur in duracoes_h:
            if dur in gumbel_params:
                alpha, u, annual_max = gumbel_params[dur]
                max_table_data[f"{dur}h (mm/h)"] = np.round(annual_max, 2)
        if max_table_data:
            n_rows = max(len(v) for v in max_table_data.values())
            idx = [str(y) for y in anos_disponiveis[:n_rows]]
            max_df = pd.DataFrame(max_table_data, index=idx[:n_rows])
            max_df.index.name = "Ano"
            st.dataframe(max_df, width="stretch")
            st.caption(
                "⚠️ Gumbel ajustado com apenas 5 pontos (1 por ano). "
                "Para aplicações de engenharia crítica, recomenda-se série histórica ≥ 30 anos."
            )

    # Seção de Calibração: Ponte Adolfo Konder vs ERA5
    st.markdown("---")
    st.subheader("🎯 Calibração Pluviométrica: Estação Ponte Adolfo Konder vs. ERA5 Bruto (2021–2025)")
    st.caption(
        "Comparação ano a ano dos picos máximos anuais medidos pelo pluviômetro local da Ponte Adolfo Konder (Blumenau) "
        "com a série horária de precipitação do modelo ERA5 (Open-Meteo). Permite validar se o fator de escala ×2.60 reflete a realidade local."
    )
    konder_calib_ds = load_adolfo_konder_dataset()
    if not konder_calib_ds.empty and not hist5_raw_df.empty:
        konder_h_series = konder_calib_ds["precip_15min"].resample("h").sum()
        era_raw_series = hist5_raw_df["precipitation"]

        calib_rows = []
        ratios_list = []
        for y_cal in [2021, 2022, 2023, 2024, 2025]:
            k_val = konder_h_series[str(y_cal)].max() if str(y_cal) in pd.DatetimeIndex(konder_h_series.index).year.astype(str) else np.nan
            e_val = era_raw_series[str(y_cal)].max() if str(y_cal) in pd.DatetimeIndex(era_raw_series.index).year.astype(str) else np.nan
            if pd.notna(k_val) and pd.notna(e_val) and e_val > 0:
                r_cal = k_val / e_val
                ratios_list.append(r_cal)
                calib_rows.append({
                    "Ano": str(y_cal),
                    "Ponte Adolfo Konder (Medição Real mm/h)": round(k_val, 1),
                    "ERA5 Bruto (Satélite mm/h)": round(e_val, 1),
                    "Fator de Correção Requerido": f"×{r_cal:.2f}",
                })
        if calib_rows:
            st.dataframe(pd.DataFrame(calib_rows).set_index("Ano"), width="stretch")
            med_ratio = np.mean(ratios_list) if ratios_list else 2.07
            max_ratio = np.max(ratios_list) if ratios_list else 2.90
            st.info(
                f"💡 **Conclusão da Calibração Local (Ponte Adolfo Konder):**\n"
                f"- A média dos fatores anuais é de **×{med_ratio:.2f}**.\n"
                f"- Para tempestades e picos de eventos graves (ex: 2022 e 2025 com 58.0 mm/h), a subestimação do ERA5 atinge **×{max_ratio:.2f}**.\n"
                f"- Recomenda-se utilizar o **Fator de Correção em ×2.60** no menu lateral para garantir margem de segurança no dimensionamento hidráulico NBR 10844."
            )

    # Seção de Validação: Open-Meteo vs. Defesa Civil
    st.markdown("---")
    st.subheader("🔬 Validação: Open-Meteo Archive vs. Defesa Civil de Blumenau")
    st.caption(
        "O Open-Meteo usa reanálise ERA5 (~9 km de resolução espacial e 1 hora temporal). "
        "A Defesa Civil usa pluviômetros locais que capturam picos pontuais. "
        "Esta seção compara os dois e estima o fator de subestimação do ERA5."
    )

    with st.spinner("Buscando dados da Defesa Civil para validação…"):
        dc_val = fetch_defesa_civil_rankings()

    if not dc_val or hist5_df.empty:
        st.warning("Não foi possível realizar a validação (dados indisponíveis).")
    else:
        dur_val_map = {"15min": 1, "30min": 1, "01h": 1, "24h": 24, "96h": 96}
        dur_val_h = {"15min": 0.25, "30min": 0.5, "01h": 1.0, "24h": 24.0, "96h": 96.0}

        st.markdown("#### 1️⃣ Máximos Globais por Duração — DC (histórico completo) vs. Open-Meteo (5 anos)")
        rows_cmp = []
        for dur, df_dc in dc_val.items():
            dh = dur_val_h.get(dur, 1.0)
            win = dur_val_map.get(dur, 1)
            dc_max_mm = df_dc["Acumulado_mm"].max()
            dc_max_mmh = dc_max_mm / dh

            om_rolled = hist5_df["precipitation"].rolling(window=win, min_periods=win).sum()
            om_max_mm = om_rolled.max()
            om_max_mmh = om_max_mm / dh

            ratio = om_max_mmh / dc_max_mmh if dc_max_mmh > 0 else None
            rows_cmp.append({
                "Duração": dur,
                "DC – Máx. (mm)": round(dc_max_mm, 1),
                "OM – Máx. 5a (mm)": round(om_max_mm, 1),
                "DC – Intensidade (mm/h)": round(dc_max_mmh, 1),
                "OM – Intensidade (mm/h)": round(om_max_mmh, 1),
                "OM / DC (%)": round(ratio * 100, 1) if ratio else None,
            })

        df_cmp = pd.DataFrame(rows_cmp).set_index("Duração")

        def color_ratio(val):
            if val is None:
                return ""
            if val >= 80:
                return "background-color: rgba(52,168,83,0.2)"
            if val >= 60:
                return "background-color: rgba(251,188,4,0.25)"
            return "background-color: rgba(234,67,53,0.2)"

        st.dataframe(
            df_cmp.style.map(color_ratio, subset=["OM / DC (%)"]),
            width="stretch",
        )
        st.caption(
            "🟢 ≥ 80%: boa concordância | 🟡 60–80%: subestimação moderada | 🔴 < 60%: subestimação significativa"
        )

        st.markdown("#### 2️⃣ Eventos Recentes da DC dentro da Janela Open-Meteo (5 anos)")
        st.caption(
            "Para eventos registrados pela DC dentro dos últimos 5 anos, buscamos o valor "
            "que o Open-Meteo registrou na mesma janela temporal e comparamos diretamente."
        )

        om_start = hist5_df.index.min()
        om_end = hist5_df.index.max()
        event_rows = []

        for dur, df_dc in dc_val.items():
            win = dur_val_map.get(dur, 1)
            for _, row in df_dc.iterrows():
                raw_dt = row["Data/Hora"]
                try:
                    dt = pd.to_datetime(raw_dt, dayfirst=True).tz_localize("America/Sao_Paulo")
                except Exception:
                    continue
                if dt < om_start or dt > om_end:
                    continue

                t0 = dt - pd.Timedelta(hours=win + 1)
                t1 = dt + pd.Timedelta(hours=2)
                slice_om = hist5_df.loc[t0:t1, "precipitation"]
                if slice_om.empty or len(slice_om) < win:
                    continue
                om_event_mm = slice_om.rolling(window=win, min_periods=win).sum().max()
                if pd.isna(om_event_mm):
                    continue

                dc_mm = row["Acumulado_mm"]
                ratio_ev = (om_event_mm / dc_mm * 100) if dc_mm else None
                event_rows.append({
                    "Duração": dur,
                    "Estação DC": row["Estação"],
                    "Região": row["Região"],
                    "Data/Hora DC": raw_dt,
                    "DC (mm)": round(dc_mm, 1),
                    "Open-Meteo (mm)": round(om_event_mm, 1),
                    "OM / DC (%)": round(ratio_ev, 1) if ratio_ev else None,
                })

        if event_rows:
            df_ev = pd.DataFrame(event_rows).sort_values("OM / DC (%)")
            df_ev.index = pd.RangeIndex(1, len(df_ev) + 1)
            df_ev.index.name = "#"

            def color_ev(val):
                if val is None:
                    return ""
                if val >= 80:
                    return "background-color: rgba(52,168,83,0.2)"
                if val >= 50:
                    return "background-color: rgba(251,188,4,0.25)"
                return "background-color: rgba(234,67,53,0.2)"

            st.dataframe(
                df_ev.style.map(color_ev, subset=["OM / DC (%)"]),
                width="stretch",
            )

            st.markdown("#### 3️⃣ Dispersão: Open-Meteo vs. Defesa Civil (eventos recentes)")
            fig_scatter = go.Figure()
            max_val = max(df_ev["DC (mm)"].max(), df_ev["Open-Meteo (mm)"].max()) * 1.1
            fig_scatter.add_trace(go.Scatter(
                x=[0, max_val], y=[0, max_val],
                mode="lines", name="Referência 1:1",
                line=dict(dash="dash", color="gray", width=1.5),
            ))
            for dur_label in df_ev["Duração"].unique():
                sub = df_ev[df_ev["Duração"] == dur_label]
                fig_scatter.add_trace(go.Scatter(
                    x=sub["DC (mm)"], y=sub["Open-Meteo (mm)"],
                    mode="markers+text",
                    name=dur_label,
                    text=sub["Estação DC"],
                    textposition="top center",
                    textfont=dict(size=8),
                    marker=dict(size=10),
                    hovertemplate=(
                        "<b>%{text}</b><br>"
                        "DC: %{x:.1f} mm<br>"
                        "OM: %{y:.1f} mm<br>"
                        "<extra>" + dur_label + "</extra>"
                    ),
                ))
            fig_scatter.update_layout(
                xaxis_title="Acumulado Defesa Civil (mm)",
                yaxis_title="Acumulado Open-Meteo ERA5 (mm)",
                height=420,
                margin=dict(l=20, r=20, t=20, b=30),
                legend=dict(orientation="h", y=-0.25, x=0.5, xanchor="center"),
            )
            st.plotly_chart(fig_scatter, width="stretch")
            st.caption(
                "Pontos **abaixo** da linha 1:1 → Open-Meteo subestima o dado local. "
                "Pontos **acima** → Open-Meteo superestima (raro para chuvas intensas pontuais)."
            )

            ratios_validos = df_ev["OM / DC (%)"].dropna()
            if len(ratios_validos) > 0:
                media_ratio = ratios_validos.mean()
                fator_correcao = 100 / media_ratio if media_ratio > 0 else None
                st.markdown("#### 4️⃣ Conclusão — Fator de Correção Estimado")
                col_c1, col_c2, col_c3 = st.columns(3)
                col_c1.metric("Média OM/DC", f"{media_ratio:.1f}%",
                              help="Percentual médio que o Open-Meteo captura dos recordes DC para eventos recentes")
                col_c2.metric("Fator de correção estimado", f"×{fator_correcao:.2f}" if fator_correcao else "N/D",
                              help="Multiplique os valores Open-Meteo por este fator para estimar os picos locais equivalentes")
                col_c3.metric("Eventos comparados", f"{len(ratios_validos)}",
                              help="Número de registros DC com data dentro da janela Open-Meteo (5 anos)")

                if media_ratio < 50:
                    st.error(
                        f"🔴 **Subestimação significativa**: o Open-Meteo captura em média apenas **{media_ratio:.0f}%** "
                        "dos valores registrados pela Defesa Civil. "
                        "Isso é esperado para dados de reanálise de grade grossa em regiões com topografia complexa. "
                        f"Para fins de projeto, considere multiplicar os valores IDF por **{fator_correcao:.1f}×**."
                    )
                elif media_ratio < 75:
                    st.warning(
                        f"🟡 **Subestimação moderada**: Open-Meteo captura ~**{media_ratio:.0f}%** dos picos DC. "
                        f"Fator de correção sugerido: **{fator_correcao:.2f}×**."
                    )
                else:
                    st.success(
                        f"🟢 **Boa concordância**: Open-Meteo captura ~**{media_ratio:.0f}%** dos picos DC. "
                        "Os dados de reanálise são razoavelmente representativos para este local."
                    )
        else:
            st.info(
                "ℹ️ Nenhum evento recente da Defesa Civil (últimos 5 anos) foi encontrado na janela "
                "do Open-Meteo Archive para comparação direta."
            )

        st.markdown("---")
        st.caption(
            "**Por que os dados diferem?** O Open-Meteo Archive usa ERA5 (ECMWF), "
            "uma reanálise global com células de grade de ~9 km. "
            "Eventos de chuva intensa muito localizados (convectivos) são suavizados espacialmente, "
            "resultando em intensidades menores do que as medidas por pluviômetros pontuais. "
            "Já a Defesa Civil usa estações físicas que capturam o pico local real. "
            "Para dimensionamento de sistemas como este poço, os dados da Defesa Civil são mais conservadores e confiáveis."
        )
