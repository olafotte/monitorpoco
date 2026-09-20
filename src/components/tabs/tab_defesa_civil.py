"""
Componente da Aba 7: Dados da Defesa Civil de Blumenau e Estação Ponte Adolfo Konder.
"""

from typing import Dict, Any, Optional
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.data.adolfo_konder import fetch_live_adolfo_konder, load_adolfo_konder_dataset
from src.data.defesa_civil import fetch_defesa_civil_rankings
from src.models.idf import compute_gumbel_idf_results
from src.models.gumbel import fit_gumbel, gumbel_quantile


def render_tab_defesa_civil(
    latitude: float,
    longitude: float,
    era5_correction: float,
    factor_mm_cm: float,
    r_pump_param: float,
) -> None:
    """Renderiza a Aba 7 com dados ao vivo da Defesa Civil, rankings e estação Ponte Adolfo Konder."""
    st.subheader("🌉 Estação Ponte Adolfo Konder (Telemetria ao Vivo)")
    st.caption(
        "Dados coletados em tempo real diretamente do portal AlertaBlu / Defesa Civil de Blumenau. "
        "Fonte: [defesacivil.blumenau.sc.gov.br](https://defesacivil.blumenau.sc.gov.br/d/)"
    )
    live_konder = fetch_live_adolfo_konder()
    if live_konder.get("sucesso"):
        c_k1, c_k2, c_k3, c_k4, c_k5 = st.columns(5)
        with c_k1:
            st.metric("Chuva 15 min", f"{live_konder.get('precip_15min_mm', 0.0):.1f} mm")
        with c_k2:
            st.metric("Chuva 1 hora", f"{live_konder.get('precip_1h_mm', 0.0):.1f} mm")
        with c_k3:
            st.metric("Chuva 24 horas", f"{live_konder.get('precip_24h_mm', 0.0):.1f} mm")
        with c_k4:
            niv_rio = live_konder.get('nivel_rio_m')
            niv_str = f"{niv_rio:.2f} m" if niv_rio is not None else "N/A"
            var_rio = live_konder.get('variacao_rio_m')
            var_str = f"{var_rio:+.2f} m/h" if var_rio is not None else None
            st.metric("Nível do Rio", niv_str, delta=var_str)
        with c_k5:
            st.metric("Status do Rio", live_konder.get("status_rio", "Normalidade"))
        st.caption(f"🕒 Leitura mais recente: **{live_konder.get('hora_leitura', 'N/A')}** | Estação Central Blumenau")
    else:
        st.info("ℹ️ Telemetria em tempo real temporariamente indisponível. Utilizando histórico da estação.")

    st.markdown("---")
    st.subheader("🛑 Maiores Chuvas Históricas – Defesa Civil de Blumenau")
    st.caption(
        "Dados oficiais do AlertaBlu – Últimos Rankings por duração (15min, 30min, 1h, 24h, 96h). "
        "Fonte: [defesacivil.blumenau.sc.gov.br](https://defesacivil.blumenau.sc.gov.br/d/maiores-chuvas)"
    )

    intensidade_sat_dc = r_pump_param / factor_mm_cm

    with st.spinner("⏳ Buscando dados do site da Defesa Civil de Blumenau..."):
        dc_rankings = fetch_defesa_civil_rankings()

    if not dc_rankings:
        st.error(
            "Não foi possível buscar os dados da Defesa Civil de Blumenau. "
            "Verifique a conexão com a internet ou se o site está acessível."
        )
    else:
        dur_para_horas = {
            "15min": 0.25,
            "30min": 0.5,
            "01h": 1.0,
            "1h": 1.0,
            "24h": 24.0,
            "96h": 96.0,
        }

        todas_regioes = sorted(set(
            regiao
            for df_dur in dc_rankings.values()
            for regiao in df_dur["Região"].dropna().unique()
        ))
        opcao_todas = "★ Todas as regiões (máximo global)"
        opcoes_regiao = [opcao_todas] + todas_regioes

        col_dur, col_reg = st.columns(2)
        with col_dur:
            dur_selecionada = st.selectbox(
                "Duração:",
                options=list(dc_rankings.keys()),
                key="dc_dur_select",
            )
        with col_reg:
            regiao_selecionada = st.selectbox(
                "Região / Bairro:",
                options=opcoes_regiao,
                key="dc_regiao_select",
                help="Filtra o ranking pelas estações de uma região específica, ou exibe o máximo global.",
            )

        st.subheader("🏆 Recordes Absolutos por Duração")
        resumo_cols = st.columns(len(dc_rankings))
        for i, (dur, df_dur) in enumerate(dc_rankings.items()):
            if regiao_selecionada == opcao_todas:
                df_top = df_dur
            else:
                df_top = df_dur[df_dur["Região"] == regiao_selecionada]
            if df_top.empty:
                resumo_cols[i].metric(label=f"Máx. em {dur}", value="—", help=f"Nenhum registro para {regiao_selecionada}")
                continue
            top = df_top.sort_values("Acumulado_mm", ascending=False).iloc[0]
            dur_h = dur_para_horas.get(dur, 1.0)
            intensidade_top = top["Acumulado_mm"] / dur_h if dur_h > 0 else 0
            resumo_cols[i].metric(
                label=f"Máx. em {dur}",
                value=f"{top['Acumulado_mm']:.1f} mm",
                help=f"{top['Estação']} ({top['Região']}) | {top['Data/Hora']} | {intensidade_top:.1f} mm/h",
            )

        st.markdown("---")

        df_sel = dc_rankings[dur_selecionada].copy()
        if regiao_selecionada != opcao_todas:
            df_sel_filtrado = df_sel[df_sel["Região"] == regiao_selecionada].copy()
            if df_sel_filtrado.empty:
                durs_com_dados = [
                    dur for dur, df_dur in dc_rankings.items()
                    if not df_dur[df_dur["Região"] == regiao_selecionada].empty
                ]
                msg_durs = ", ".join(f"**{d}**" for d in durs_com_dados) if durs_com_dados else "nenhuma"
                st.info(
                    f"ℹ️ A região **{regiao_selecionada}** não aparece no ranking de **{dur_selecionada}** "
                    f"(o top-10 dessa duração é dominado por outras regiões). "
                    f"Durações com registros para {regiao_selecionada}: {msg_durs}. "
                    "Exibindo ranking global para referência."
                )
            else:
                df_sel = df_sel_filtrado
        dur_h_sel = dur_para_horas.get(dur_selecionada, 1.0) if dur_selecionada else 1.0
        df_sel["Intensidade_mmh"] = df_sel["Acumulado_mm"] / dur_h_sel
        ultima_atualiz = df_sel["ultima_atualizacao"].iloc[0] if not df_sel.empty else ""

        st.caption(f"Tabela: Maiores Chuvas em **{dur_selecionada}** | Última atualização: {ultima_atualiz}")

        cores = [
            "rgba(231,76,60,0.85)" if row["Intensidade_mmh"] >= intensidade_sat_dc else "rgba(30,100,220,0.75)"
            for _, row in df_sel.iterrows()
        ]
        fig_dc = go.Figure()
        fig_dc.add_trace(go.Bar(
            x=df_sel["Acumulado_mm"],
            y=[f"{row['Estação']} ({row['Data/Hora']})" for _, row in df_sel.iterrows()],
            orientation="h",
            marker_color=cores,
            text=[f"{v:.1f} mm" for v in df_sel["Acumulado_mm"]],
            textposition="outside",
            customdata=df_sel[["Estação", "Região", "Intensidade_mmh"]].values,
            hovertemplate=(
                "<b>%{customdata[0]}</b> (%{customdata[1]})<br>"
                "Acumulado: %{x:.1f} mm<br>"
                f"Intensidade média: %{{customdata[2]:.1f}} mm/h<br>"
                "<extra></extra>"
            ),
        ))
        acum_sat = intensidade_sat_dc * dur_h_sel
        fig_dc.add_vline(x=acum_sat, line_dash="dash", line_color="red")
        fig_dc.add_annotation(
            x=acum_sat, y=1, yref="paper",
            text=f"Saturação bomba<br>({acum_sat:.1f} mm em {dur_selecionada})",
            showarrow=True, arrowhead=2, arrowcolor="red",
            font=dict(color="red", size=10), xanchor="left",
        )
        fig_dc.update_layout(
            xaxis_title=f"Acumulado em {dur_selecionada} (mm)",
            yaxis=dict(autorange="reversed"),
            height=max(300, 50 + 40 * len(df_sel)),
            margin=dict(l=10, r=120, t=20, b=30),
        )
        st.plotly_chart(fig_dc, width="stretch")

        n_acima_sat = (df_sel["Intensidade_mmh"] >= intensidade_sat_dc).sum()
        if n_acima_sat > 0:
            st.warning(
                f"⚠️ **{n_acima_sat} de {len(df_sel)} registros** neste ranking têm intensidade ≥ **{intensidade_sat_dc:.1f} mm/h** "
                f"(limite de saturação da bomba). Em eventos como esses, o poço encheria mais rápido do que a bomba conseguiria esvaziar."
            )
        else:
            st.success(
                f"✅ Nenhum registro deste ranking supera o limite de saturação da bomba ({intensidade_sat_dc:.1f} mm/h)."
            )

        st.subheader("📋 Tabela Completa")
        df_display = df_sel[["Estação", "Região", "Data/Hora", "Acumulado_mm", "Intensidade_mmh"]].copy()
        df_display.columns = ["Estação", "Região", "Data/Hora", f"Acumulado em {dur_selecionada} (mm)", "Intensidade média (mm/h)"]
        df_display[f"Acumulado em {dur_selecionada} (mm)"] = df_display[f"Acumulado em {dur_selecionada} (mm)"].round(1)
        df_display["Intensidade média (mm/h)"] = df_display["Intensidade média (mm/h)"].round(1)
        df_display.index = pd.RangeIndex(1, len(df_display) + 1)
        df_display.index.name = "#"
        st.dataframe(df_display, width="stretch")

        st.markdown("---")

        # Contexto IDF
        st.subheader("📈 Contexto IDF: Maiores Chuvas Históricas vs. Curva Gumbel (Open-Meteo)")
        st.caption(
            "Pontos vermelhos = recordes da Defesa Civil de Blumenau | "
            "Linhas = curvas IDF estimadas da tab Histórico & IDF (5 anos)"
        )

        idf_results, _ = compute_gumbel_idf_results(latitude, longitude, era5_correction)
        idf_ok = bool(idf_results)

        dc_points_x = []
        dc_points_y = []
        dc_points_label = []
        for dur, df_dur in dc_rankings.items():
            dh = dur_para_horas.get(dur, None)
            if dh is None:
                continue
            df_pts = df_dur if regiao_selecionada == opcao_todas else df_dur[df_dur["Região"] == regiao_selecionada]
            if df_pts.empty:
                continue
            top = df_pts.sort_values("Acumulado_mm", ascending=False).iloc[0]
            dc_points_x.append(dh)
            dc_points_y.append(top["Acumulado_mm"] / dh)
            dc_points_label.append(f"{top['Estação']} ({dur}: {top['Acumulado_mm']:.0f}mm)")

        fig_ctx = go.Figure()

        if idf_ok:
            duracoes_h_ctx = [1, 2, 3, 6, 12, 24]
            palette_ctx = ["#1a73e8", "#34a853", "#fbbc04", "#ea4335", "#9c27b0", "#00bcd4"]
            trs_ctx = [2, 5, 10, 25, 50, 100]
            for i_tr, tr in enumerate(trs_ctx):
                y_ctx = [idf_results.get(d, {}).get(tr, None) for d in duracoes_h_ctx]
                fig_ctx.add_trace(go.Scatter(
                    x=duracoes_h_ctx, y=y_ctx,
                    mode="lines",
                    name=f"Tr={tr}a (Gumbel)",
                    line=dict(color=palette_ctx[i_tr % len(palette_ctx)],
                              width=2.5 if tr == 25 else 1.2,
                              dash="solid" if tr in [25, 100] else "dot"),
                    opacity=0.7,
                ))

        if dc_points_x:
            fig_ctx.add_trace(go.Scatter(
                x=dc_points_x, y=dc_points_y,
                mode="markers+text",
                name="Recorde Defesa Civil BNU",
                marker=dict(size=12, color="red", symbol="star"),
                text=dc_points_label,
                textposition="top center",
                textfont=dict(size=9),
            ))

        fig_ctx.add_hline(
            y=intensidade_sat_dc,
            line_dash="dash", line_color="darkred",
            annotation_text=f"Saturação bomba ({intensidade_sat_dc:.1f} mm/h)",
            annotation_position="top right",
        )
        fig_ctx.update_layout(
            xaxis=dict(
                title="Duração (horas)",
                tickvals=[0.25, 0.5, 1, 2, 3, 6, 12, 24, 96],
                ticktext=["15min", "30min", "1h", "2h", "3h", "6h", "12h", "24h", "96h"],
            ),
            yaxis_title="Intensidade média (mm/h)",
            legend=dict(orientation="h", y=-0.3, x=0.5, xanchor="center"),
            height=480,
            margin=dict(l=20, r=20, t=20, b=80),
        )
        st.plotly_chart(fig_ctx, width="stretch")

        if not idf_ok:
            st.info(
                "ℹ️ Para ver as curvas IDF junto com os recordes, acesse primeiro a aba "
                "**📊 Histórico & IDF (5 anos)** para que os dados sejam calculados."
            )

        st.markdown("---")
        st.info(
            "🔗 Dados originais: [Defesa Civil de Blumenau – Maiores Chuvas]("
            "https://defesacivil.blumenau.sc.gov.br/d/maiores-chuvas). "
            "Os dados são atualizados pelo AlertaBlu conforme novos eventos ocorrem."
        )

    # Seção exclusiva Estação Ponte Adolfo Konder
    st.markdown("---")
    st.subheader("🌊 Estação Pluviométrica Ponte Adolfo Konder — Análise de Gumbel & Recordes (2021–2025)")
    st.caption(
        "Seção exclusiva dedicada à Estação Pluviométrica física instalada na Ponte Adolfo Konder (Centro de Blumenau). "
        "Contém a análise de frequência de extremos por Ajuste da Distribuição de Gumbel e os maiores picos gravados no poço de monitoramento."
    )

    konder_sec_df = load_adolfo_konder_dataset()
    if not konder_sec_df.empty:
        pico_15m_rec = konder_sec_df["precip_15min"].max()
        pico_15m_dt = konder_sec_df["precip_15min"].idxmax()
        pico_1h_rec = konder_sec_df["precipitation"].max()
        pico_1h_dt = konder_sec_df["precipitation"].idxmax()
        tot_leituras_k = len(konder_sec_df)

        mk1, mk2, mk3, mk4 = st.columns(4)
        mk1.metric("Recorde 15min (Pulso)", f"{pico_15m_rec:.1f} mm", help=f"Data: {pd.to_datetime(pico_15m_dt).strftime('%d/%m/%Y %H:%M')} | Taxa inst.: {pico_15m_rec*4:.1f} mm/h")
        mk2.metric("Recorde Horário (1h)", f"{pico_1h_rec:.1f} mm/h", help=f"Data: {pd.to_datetime(pico_1h_dt).strftime('%d/%m/%Y %H:%M')}")
        mk3.metric("Período Coberto", "2021–2025", help=f"{konder_sec_df.index.min().strftime('%d/%m/%Y')} a {konder_sec_df.index.max().strftime('%d/%m/%Y')}")
        mk4.metric("Total de Leituras", f"{tot_leituras_k:,}", help="Intervalos contínuos de 15 minutos")

        st.markdown("#### 📈 Curvas IDF de Gumbel — Estação Ponte Adolfo Konder")
        st.caption("Intensidades máximas calculadas por Gumbel (método dos momentos) sobre as séries reais de 15min a 24h.")

        dur_map_k = {
            "15min": (0.25, konder_sec_df["precip_mmh"]),
            "30min": (0.5, konder_sec_df["precip_15min"].rolling(2, min_periods=1).sum() * 2.0),
            "1h": (1.0, konder_sec_df["precip_15min"].rolling(4, min_periods=1).sum()),
            "2h": (2.0, konder_sec_df["precip_15min"].rolling(8, min_periods=1).sum() / 2.0),
            "6h": (6.0, konder_sec_df["precip_15min"].rolling(24, min_periods=1).sum() / 6.0),
            "24h": (24.0, konder_sec_df["precip_15min"].rolling(96, min_periods=1).sum() / 24.0),
        }
        trs_k = [2, 5, 10, 25, 50, 100]
        idf_k_dict = {}

        for d_lbl, (_, s_k) in dur_map_k.items():
            ann_max_k = np.asarray(s_k.groupby(pd.to_datetime(s_k.index).year).max().dropna().values, dtype=np.float64)
            if len(ann_max_k) >= 2:
                alpha_k, u_k = fit_gumbel(ann_max_k)
                idf_k_dict[d_lbl] = {tr: round(max(gumbel_quantile(alpha_k, u_k, tr), 0.0), 1) for tr in trs_k}

        if idf_k_dict:
            idf_k_df = pd.DataFrame(idf_k_dict).T
            idf_k_df.columns = [f"Tr={tr}a" for tr in trs_k]
            idf_k_df.index.name = "Duração"

            palette_k = ["#1a73e8", "#34a853", "#fbbc04", "#ea4335", "#9c27b0", "#00bcd4"]
            fig_idf_k = go.Figure()
            durs_x = list(idf_k_dict.keys())
            for idx_tr, tr_val in enumerate(trs_k):
                y_vals_k = [idf_k_dict[d_lbl][tr_val] for d_lbl in durs_x]
                fig_idf_k.add_trace(go.Scatter(
                    x=durs_x, y=y_vals_k, mode="lines+markers",
                    name=f"Tr = {tr_val} anos",
                    line=dict(color=palette_k[idx_tr % len(palette_k)], width=2.5 if tr_val == 25 else 1.5),
                    marker=dict(size=7),
                ))
            fig_idf_k.update_layout(
                title="Curvas IDF Gumbel — Estação Ponte Adolfo Konder (2021–2025)",
                xaxis_title="Duração", yaxis_title="Intensidade Média (mm/h)",
                legend=dict(orientation="h", y=-0.22, x=0.5, xanchor="center"),
                height=420, margin=dict(l=20, r=20, t=40, b=50),
            )
            st.plotly_chart(fig_idf_k, width="stretch")

            st.markdown("##### 📋 Tabela IDF Gumbel (Ponte Adolfo Konder — mm/h)")
            def highlight_tr25_k(col):
                return ["background-color: rgba(234,67,53,0.15); font-weight:bold" if col.name == "Tr=25a" else "" for _ in col]
            st.dataframe(idf_k_df.style.apply(highlight_tr25_k), width="stretch")

        st.markdown("#### 🏆 Maiores Picos Registrados na Ponte Adolfo Konder")
        tab_p1, tab_p2 = st.tabs([":material/bolt: Top 10 Pulsos de 15 Minutos", ":material/rainy: Top 10 Eventos Horários (1h)"], on_change="rerun")

        with tab_p1:
            if tab_p1.open:
                top_15m = konder_sec_df.sort_values("precip_15min", ascending=False).head(10).copy()
                top_15m_display = []
                for dt_k, r_k in top_15m.iterrows():
                    val_15 = r_k["precip_15min"]
                    rate_h = val_15 * 4.0
                    sat_status = "🚨 Excede Bomba 1" if rate_h > intensidade_sat_dc else "✅ Suportado"
                    top_15m_display.append({
                        "Data / Hora": dt_k.strftime("%d/%m/%Y %H:%M") if hasattr(dt_k, "strftime") else str(dt_k),
                        "Precipitação (mm / 15min)": f"{val_15:.1f} mm",
                        "Taxa Horária Eq. (mm/h)": f"{rate_h:.1f} mm/h",
                        "Entrada no Poço (cm/h)": f"{rate_h * factor_mm_cm:.1f} cm/h",
                        "Status": sat_status,
                    })
                st.dataframe(pd.DataFrame(top_15m_display), width="stretch")

        with tab_p2:
            if tab_p2.open:
                top_1h = konder_sec_df["precipitation"].resample("1h").sum().sort_values(ascending=False).head(10).copy()
                top_1h_display = []
                for dt_k, val_1h in top_1h.items():
                    sat_status = "🚨 Excede Bomba 1" if val_1h > intensidade_sat_dc else "✅ Suportado"
                    top_1h_display.append({
                        "Data / Hora": dt_k.strftime("%d/%m/%Y %H:00") if hasattr(dt_k, "strftime") else str(dt_k),
                        "Chuva Acumulada 1h (mm)": f"{val_1h:.1f} mm/h",
                        "Entrada no Poço (cm/h)": f"{val_1h * factor_mm_cm:.1f} cm/h",
                        "Status": sat_status,
                    })
                st.dataframe(pd.DataFrame(top_1h_display), width="stretch")
