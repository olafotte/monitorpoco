"""
Script para extrair e sobrepor 5+ séries reais de ciclos de acionamento da bomba no gráfico comparativo da Aba 2.
"""
import sys

path = 'streamlit_app.py'
content = open(path, encoding='utf-8').read()

old_chart_block = '''    # 2. Localizar e alinhar o evento de acionamento real da bomba mais recente (t = 0 em d_on)
    real_event_found = False
    if "filled_df" in locals() or "filled_df" in globals():
        try:
            if not filled_df.empty:
                s_lev = filled_df["nivel_cm"].fillna(filled_df["nivel_imputed"])
                df_w = filled_df.copy()
                df_w["val"] = s_lev

                on_idx = df_w[df_w["val"] <= (d_on + 3.0)].index
                if len(on_idx) == 0:
                    on_idx = df_w.sort_values("val").head(5).index

                for idx in reversed(on_idx):
                    loc = df_w.index.get_loc(idx)
                    sub = df_w.iloc[loc:min(loc+24, len(df_w))]
                    if len(sub) < 3:
                        continue
                    d_start = sub["val"].iloc[0]
                    d_end = sub["val"].max()
                    if (d_end - d_start) >= 3.0:
                        t0_ev = sub["dt_round"].iloc[0]
                        sub_df = sub.copy()
                        sub_df["t_min"] = (sub_df["dt_round"] - t0_ev).dt.total_seconds() / 60.0
                        dt_h_ev = (sub_df["dt_round"].iloc[-1] - t0_ev).total_seconds() / 3600.0
                        real_rate_cmh = (sub_df["val"].iloc[-1] - d_start) / dt_h_ev if dt_h_ev > 0 else 0.0

                        fig_comp.add_trace(go.Scatter(
                            x=sub_df["t_min"], y=sub_df["val"],
                            mode="markers+lines",
                            name=f"Medição Real do Sensor ({t0_ev.strftime('%d/%m %H:%M')})",
                            line=dict(color="#e67e22", width=2.5, dash="dot"),
                            marker=dict(size=7, color="#e67e22")
                        ))

                        mc1, mc2, mc3 = st.columns(3)
                        mc1.metric("Taxa Esvaziamento Teórica", f"{r_liq_teorico:.1f} cm/h", help="14 m³/h menos lençol freático")
                        mc2.metric("Taxa Esvaziamento Real Medida", f"{real_rate_cmh:.1f} cm/h", delta=f"{real_rate_cmh - r_liq_teorico:+.1f} cm/h")
                        mc3.metric("Tempo de Rebaixamento Real", f"{sub_df['t_min'].iloc[-1]:.0f} min", help=f"Tempo teórico ideal: {t_teorico_min:.1f} min")
                        real_event_found = True
                        break
        except Exception:
            pass'''

new_chart_block = '''    # 2. Localizar e alinhar múltiplos ciclos reais de acionamento da bomba (t = 0 em d_on)
    real_cycles = []
    palette_cycles = ["#e67e22", "#2ecc71", "#9b59b6", "#e74c3c", "#f1c40f", "#1abc9c", "#e84393", "#00cec9"]

    if "filled_df" in locals() or "filled_df" in globals():
        try:
            if not filled_df.empty:
                s_lev = filled_df["nivel_cm"].fillna(filled_df["nivel_imputed"])
                df_w = filled_df.copy()
                df_w["val"] = s_lev

                on_idx = df_w[df_w["val"] <= (d_on + 3.0)].index
                if len(on_idx) == 0:
                    on_idx = df_w.sort_values("val").head(30).index

                for idx in reversed(on_idx):
                    loc = df_w.index.get_loc(idx)
                    t_curr = df_w["dt_round"].iloc[loc]

                    # Evitar duplicatas no mesmo ciclo (separação mínima de 30 min)
                    if any(abs((t_curr - c["start_dt"]).total_seconds()) < 1800 for c in real_cycles):
                        continue

                    sub = df_w.iloc[loc:min(loc+24, len(df_w))]
                    if len(sub) < 3:
                        continue
                    d_start = sub["val"].iloc[0]
                    d_end = sub["val"].max()
                    if (d_end - d_start) >= 3.0:
                        t0_ev = sub["dt_round"].iloc[0]
                        sub_df = sub.copy()
                        sub_df["t_min"] = (sub_df["dt_round"] - t0_ev).dt.total_seconds() / 60.0
                        dt_h_ev = (sub_df["dt_round"].iloc[-1] - t0_ev).total_seconds() / 3600.0
                        real_rate_cmh = (sub_df["val"].iloc[-1] - d_start) / dt_h_ev if dt_h_ev > 0 else 0.0

                        real_cycles.append({
                            "start_dt": t0_ev,
                            "sub_df": sub_df,
                            "rate_cmh": real_rate_cmh,
                            "duration_min": sub_df["t_min"].iloc[-1],
                        })
                        if len(real_cycles) >= 6:  # Extrair até 6 ciclos reais distintos
                            break
        except Exception:
            pass

    if real_cycles:
        for idx_c, c in enumerate(real_cycles, 1):
            clr = palette_cycles[(idx_c - 1) % len(palette_cycles)]
            t_label = c["start_dt"].strftime("%d/%m %H:%M")
            fig_comp.add_trace(go.Scatter(
                x=c["sub_df"]["t_min"], y=c["sub_df"]["val"],
                mode="markers+lines",
                name=f"Ciclo Real {idx_c} ({t_label}) — {c['rate_cmh']:.1f} cm/h",
                line=dict(color=clr, width=2, dash="dot"),
                marker=dict(size=6, color=clr)
            ))

        avg_real_rate = np.mean([c["rate_cmh"] for c in real_cycles])
        avg_real_dur = np.mean([c["duration_min"] for c in real_cycles])

        mc1, mc2, mc3 = st.columns(3)
        mc1.metric("Taxa Esvaziamento Teórica", f"{r_liq_teorico:.1f} cm/h", help="14 m³/h menos lençol freático")
        mc2.metric(f"Média Real ({len(real_cycles)} Ciclos Medidos)", f"{avg_real_rate:.1f} cm/h", delta=f"{avg_real_rate - r_liq_teorico:+.1f} cm/h")
        mc3.metric("Tempo Médio de Rebaixamento", f"{avg_real_dur:.0f} min (Real)", delta=f"Teórico: {t_teorico_min:.1f} min")

        with st.expander(f"📋 Ver Detalhes dos {len(real_cycles)} Ciclos Reais Medidos pelo Sensor"):
            det_rows = []
            for i, c in enumerate(real_cycles, 1):
                det_rows.append({
                    "Ciclo #": i,
                    "Data / Hora Ativação (t=0)": c["start_dt"].strftime("%d/%m/%Y %H:%M"),
                    "Taxa Rebaixamento (cm/h)": f"{c['rate_cmh']:.1f} cm/h",
                    "Tempo até Desligar (min)": f"{c['duration_min']:.0f} min",
                    "Diferença vs. Teórico": f"{c['rate_cmh'] - r_liq_teorico:+.1f} cm/h",
                })
            st.dataframe(pd.DataFrame(det_rows).set_index("Ciclo #"), use_container_width=True)'''

assert old_chart_block in content, "old_chart_block not found"
content = content.replace(old_chart_block, new_chart_block, 1)

open(path, 'w', encoding='utf-8').write(content)
print("Multiple (5+) real pump activation series added to Tab 2 chart successfully.")
