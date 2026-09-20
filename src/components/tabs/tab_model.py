"""
Componente da Aba 2: Formulação do Modelo Matemático e Calibração dos Regimes.
"""

from typing import List, Dict, Any
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.config import DEFAULT_FATOR_M3H_PARA_CMH


def render_tab_model(
    filled_df: pd.DataFrame,
    d_on: float,
    d_off: float,
    vazao_bomba_m3h: float,
    r_pump_param: float,
    r_gnd_param: float,
    fator_m3h_para_cmh: float,
) -> None:
    """Renderiza a Aba 2 com a formulação matemática do balanço hídrico e comparação teórica vs. medições reais."""
    st.header("Formulação do Modelo Matemático")
    st.markdown("O comportamento hidrodinâmico do poço de drenagem é regido pela **Equação de Balanço de Massa / Continuidade**:")
    st.latex(r"\frac{dV}{dt} = A \cdot \frac{dh}{dt} = Q_{\text{entrada}}(t) - Q_{\text{saída}}(t)")
    st.markdown(r"Como o sensor mede a distância $d(t)$ no topo em relação à lâmina d'água ($h(t) = H_{\text{sensor}} - d(t)$), temos $\frac{dh}{dt} = -\frac{dd}{dt}$.")
    st.markdown("Portanto, a taxa instantânea de variação da distância $d(t)$ é dada por:")
    st.latex(r"\frac{dd(t)}{dt} = \frac{Q_{\text{saída}}(t) - Q_{\text{entrada}}(t)}{A}")
    st.subheader("Regimes Operacionais Recalibrados")
    st.markdown(r"**1️⃣ Entrada Lenta (Lençol Freático – Estado $S_0$)**")
    st.markdown(r"- $Q_{\text{saída}} = 0$ (Bomba desligada)")
    st.latex(r"\frac{dd}{dt} = -r_{\text{freático}} \approx -1.44\text{ cm/h}")
    st.markdown(r"**2️⃣ Entrada por Chuva ($S_1$)**")
    st.markdown("- Relacionada com a precipitação em mm pela taxa de amplificação do pátio:")
    st.latex(r"\text{Taxa de Entrada (cm/h)} = \frac{\text{Chuva (mm)}}{\text{Horas}} \times 2.83")
    st.markdown(r"**3️⃣ Esvaziamento Forçado (Bomba Ativa – Estado $S_2$)**")
    st.latex(rf"Q_{{\text{{saída}}}} = {vazao_bomba_m3h * 1000:,.0f}\text{{ L/h}} ({vazao_bomba_m3h:.1f}\text{{ m}}^3/\text{{h}}) \Rightarrow r_{{\text{{bomba}}}} \approx {r_pump_param:.1f}\text{{ cm/h}}")

    st.markdown("---")
    st.subheader("📐 Estimativa e Calibração do Modelo de Dois Fatores (m³/h para cm/h)")
    st.markdown(r"""
### 1. Relação Teórica do Poço Cilíndrico Isolado
Para um poço cilíndrico ideal de diâmetro interno $D = 1{,}20\text{ m}$, a área da seção transversal isolada ($A$) é:
""")
    st.latex(r"A = \pi \cdot \left(\frac{D}{2}\right)^2 = \pi \cdot (0{,}60)^2 \approx 1{,}131\text{ m}^2 \quad (\mathbf{11{,}31\text{ Litros/cm}})")
    st.markdown(r"""
Se o poço operasse totalmente isolado, a conversão teórica para uma bomba de **37,0 m³/h** (616,7 L/min) resultaria em uma velocidade de rebaixamento de $r_{\text{bomba}} \approx 3.271\text{ cm/h}$ (54,5 cm/min), esvaziando 22 cm em apenas 24 segundos.

### 2. Modelo Hidrológico de Dois Fatores (*Dual-Factor Model*)
Na prática, o sistema comporta-se sob **dois regimes distintos**:

* **⚡ Regime 1: Bombeamento Rápido por Bomba (37,0 m³/h em 2 a 3 minutos)**  
  Durante os acionamentos curtos da bomba de $37{,}0\text{ m}^3/\text{h}$, a água expulsa vem da coluna principal do poço e das tubulações de chegada direta. A água retida na porosidade do solo e galerias distantes não consegue fluir rapidamente por capilaridade (*rendimento específico*).  
  A calibração por telemetria indica que **$55{,}0\text{ Litros}$ entram/saem por cm de nível no poço** ($A_{\text{bomba}} = 5{,}50\text{ m}^2$).
""")
    st.latex(r"F_{\text{bomba}} = \frac{100}{A_{\text{bomba}}} = \frac{100}{5{,}50} \approx 18{,}182\text{ (cm/h por m}^3/\text{h)}")
    st.latex(r"r_{\text{bomba}} = Q_{\text{bomba}} \cdot F_{\text{bomba}} = 37{,}0\text{ m}^3/\text{h} \times 18{,}182 = 672{,}7\text{ cm/h} \quad (\mathbf{11{,}2\text{ cm/min}})")
    st.markdown(r"""
* **🌧️ Regime 2: Infiltração Lenta / Chuva (Acumulo e Retenção do Pátio em Horas)**  
  Em eventos de chuva prolongada ou testes estáticos de infiltração, a água satura o solo úmido ao redor e as tubulações de retração, expandindo a capacidade de retenção para **$187{,}5\text{ Litros/cm}$** ($A_{\text{chuva}} = 18{,}75\text{ m}^2$).  
  O fator de amplificação calibrado para precipitação sobre o pátio é de **$2{,}83\text{ cm de poço por mm de chuva}$**.
""")

    st.markdown("---")
    st.subheader("📈 Comparativo no Momento LIGA → DESLIGA: Esvaziamento Teórico vs. 5+ Séries Reais (Sensor)")
    st.caption("Alinhamento do instante exato de atracamento da bomba (quando a água atinge d_on, t = 0 min) para comparar a rampa de esvaziamento teórica com múltiplos eventos reais gravados pelo sensor ultrassônico.")

    # 1. Rampa Teórica de Esvaziamento
    r_liq_teorico = max(r_pump_param - r_gnd_param, 0.1)
    t_teorico_min = (d_off - d_on) / r_liq_teorico * 60.0
    t_teor_arr = np.linspace(0, max(t_teorico_min * 1.4, 35.0), 100)
    d_teor_arr = [min(d_on + (r_liq_teorico / 60.0) * t, d_off) for t in t_teor_arr]

    fig_comp = go.Figure()
    fig_comp.add_trace(go.Scatter(
        x=t_teor_arr, y=d_teor_arr, mode="lines",
        name=f"Esvaziamento Teórico (-{r_liq_teorico:.1f} cm/h)",
        line=dict(color="#1f77b4", width=3.5)
    ))

    # 2. Localizar e alinhar o início das rampas reais
    real_cycles: List[Dict[str, Any]] = []
    palette_cycles = ["#e67e22", "#2ecc71", "#9b59b6", "#e74c3c", "#f1c40f", "#1abc9c", "#e84393", "#00cec9"]

    if filled_df is not None and not filled_df.empty:
        try:
            s_lev = filled_df["nivel_cm"].fillna(filled_df["nivel_imputed"])
            df_w = filled_df.copy()
            df_w["val"] = s_lev

            diff_15m = df_w["val"].shift(-3) - df_w["val"]
            triggers = df_w[(df_w["val"] <= (d_on + 6.0)) & (diff_15m >= 2.0)].index

            if len(triggers) == 0:
                diff_5m = df_w["val"].shift(-1) - df_w["val"]
                triggers = df_w[(df_w["val"] <= (d_on + 10.0)) & (diff_5m >= 1.0)].index

            for idx in reversed(triggers):
                loc_val = df_w.index.get_loc(idx)
                loc = int(loc_val.start if isinstance(loc_val, slice) else (loc_val[0] if isinstance(loc_val, np.ndarray) else loc_val))
                t_curr = df_w["dt_round"].iloc[loc]

                if any(abs((t_curr - c["start_dt"]).total_seconds()) < 1200 for c in real_cycles):
                    continue

                sub = df_w.iloc[loc:min(loc+24, len(df_w))]
                if len(sub) < 2:
                    continue

                max_dist_loc = sub["val"].idxmax()
                end_loc_val = sub.index.get_loc(max_dist_loc)
                end_loc = int(end_loc_val.start if isinstance(end_loc_val, slice) else (end_loc_val[0] if isinstance(end_loc_val, np.ndarray) else end_loc_val))
                sub_cut = sub.iloc[0:end_loc+1]

                if len(sub_cut) >= 2 and (sub_cut["val"].iloc[-1] - sub_cut["val"].iloc[0]) >= 3.0:
                    sub_copy = sub_cut.copy()
                    t0_ev = sub_copy["dt_round"].iloc[0]
                    sub_copy["t_min"] = (sub_copy["dt_round"] - t0_ev).dt.total_seconds() / 60.0
                    dt_h_ev = (sub_copy["dt_round"].iloc[-1] - t0_ev).total_seconds() / 3600.0
                    real_rate_cmh = (sub_copy["val"].iloc[-1] - sub_copy["val"].iloc[0]) / dt_h_ev if dt_h_ev > 0 else 0.0

                    real_cycles.append({
                        "start_dt": t0_ev,
                        "sub_df": sub_copy,
                        "rate_cmh": real_rate_cmh,
                        "duration_min": sub_copy["t_min"].iloc[-1],
                    })
                    if len(real_cycles) >= 6:
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

        avg_real_rate = float(np.mean([c["rate_cmh"] for c in real_cycles]))
        avg_real_dur = float(np.mean([c["duration_min"] for c in real_cycles]))

        mc1, mc2, mc3 = st.columns(3)
        mc1.metric(
            "Taxa Esvaziamento Teórica",
            f"{r_liq_teorico:.1f} cm/h",
            help=f"Bomba ({r_pump_param:.1f} cm/h | {vazao_bomba_m3h:.1f} m³/h) menos lençol freático ({r_gnd_param:.1f} cm/h)"
        )
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
            st.dataframe(pd.DataFrame(det_rows).set_index("Ciclo #"), width="stretch")

    fig_comp.add_hline(y=d_on, line_dash="dash", line_color="red", annotation_text=f"Bóia LIGA ({d_on:.1f} cm)", annotation_position="top right")
    fig_comp.add_hline(y=d_off, line_dash="dash", line_color="green", annotation_text=f"Bóia DESLIGA ({d_off:.1f} cm)", annotation_position="top right")

    fig_comp.update_layout(
        title="Rampa de Esvaziamento do Poço: Instante da Ativação da Bomba (t = 0 min, Múltiplos Eventos)",
        xaxis_title="Tempo Decorrido desde a Ativação da Bomba (Minutos)",
        yaxis=dict(autorange="reversed", title="Nível da Água (cm abaixo do topo do poço)"),
        height=480,
        margin=dict(l=20, r=20, t=40, b=40)
    )
    st.plotly_chart(fig_comp, width="stretch")

    if not real_cycles:
        st.info("ℹ️ Exibindo rampa teórica de esvaziamento. Conecte ao banco Turso DB para sobrepor dados em tempo real dos eventos de acionamento.")
