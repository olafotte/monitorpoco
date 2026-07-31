"""
Script para corrigir o LaTeX de (dd/dt) e adicionar o gráfico auxiliar de ciclo no final da Aba 2.
"""
import sys

path = 'streamlit_app.py'
content = open(path, encoding='utf-8').read()

old_sec3_latex = '''    st.markdown(
        """
        ### 3. Estimativa Prática pelos Dados do Sensor Ultrassônico
        O modelo estima e valida empiricamente o fator de conversão $F$ analisando os ciclos de acionamento da bomba capturados pelo sensor ultrassônico durante períodos sem chuva (quando apenas o lençol freático está ativo):

        1. **Fase de Esvaziamento (Bomba LIGADA):** O sensor mede a velocidade líquida de rebaixamento da água $(\\frac{dd}{dt})_{\\text{desce}}$.
        2. **Fase de Enchimento (Bomba DESLIGADA):** O sensor mede a velocidade de subida por afluxo do lençol freático $(\\frac{dd}{dt})_{\\text{subida}}$.
        3. **Vazão Bruta da Bomba ($r_{\\text{bomba}}$):** A soma das duas velocidades elimina a interferência do lençol freático e fornece a velocidade bruta da bomba registrada no sensor:
        """
    )
    st.latex(r"r_{\\text{bomba}} = \\left(\\frac{dd}{dt}\\right)_{\\text{desce}} + \\left(\\frac{dd}{dt}\\right)_{\\text{subida}} \\approx 76,6\\text{ cm/h}")
    st.markdown(
        """
        4. **Cálculo do Fator Calibrado ($F$):** Dividindo essa taxa de variação observada pelo sensor em \\text{cm/h} pela vazão nominal hidráulica da bomba $Q_{\\text{bomba}} = 14,0\\text{ m}^3/\\text{h}$:
        """
    )
    st.latex(r"F_{\\text{calibrado}} = \\frac{r_{\\text{bomba}}\\text{ (cm/h)}}{Q_{\\text{bomba}}\\text{ (m}^3/\\text{h)}} = \\frac{76,6\\text{ cm/h}}{14,0\\text{ m}^3/\\text{h}} = 5,471\\text{ (cm/h por m}^3/\\text{h)}")'''

new_sec3_latex = '''    st.markdown(
        r"""
        ### 3. Estimativa Prática pelos Dados do Sensor Ultrassônico
        O modelo estima e valida empiricamente o fator de conversão $F$ analisando os ciclos de acionamento da bomba capturados pelo sensor ultrassônico durante períodos sem chuva (quando apenas o lençol freático está ativo):

        1. **Fase de Esvaziamento (Bomba LIGADA):** O sensor mede a velocidade líquida de rebaixamento da água $\left(\frac{dd}{dt}\right)_{\text{desce}} \approx -75,16\text{ cm/h}$.
        2. **Fase de Enchimento (Bomba DESLIGADA):** O sensor mede a velocidade de subida por afluxo do lençol freático $\left(\frac{dd}{dt}\right)_{\text{subida}} \approx +1,44\text{ cm/h}$.
        3. **Vazão Bruta da Bomba ($r_{\text{bomba}}$):** A soma em módulo das duas velocidades elimina a interferência do lençol freático e fornece a velocidade bruta da bomba registrada no sensor:
        """
    )
    st.latex(r"r_{\text{bomba}} = \left|\left(\frac{dd}{dt}\right)_{\text{desce}}\right| + \left(\frac{dd}{dt}\right)_{\text{subida}} = 75,16 + 1,44 = 76,6\text{ cm/h}")
    st.markdown(
        r"""
        4. **Cálculo do Fator Calibrado ($F$):** Dividindo essa taxa de variação observada pelo sensor em $\text{cm/h}$ pela vazão nominal hidráulica da bomba $Q_{\text{bomba}} = 14,0\text{ m}^3/\text{h}$:
        """
    )
    st.latex(r"F_{\text{calibrado}} = \frac{r_{\text{bomba}}\text{ (cm/h)}}{Q_{\text{bomba}}\text{ (m}^3/\text{h)}} = \frac{76,6\text{ cm/h}}{14,0\text{ m}^3/\text{h}} = 5,471\text{ (cm/h por m}^3/\text{h)}")

    st.markdown("---")
    st.subheader("📈 Gráfico Auxiliar: Ciclo Típico de Operação & Taxas Observadas pelo Sensor")
    st.caption("Ilustração em tempo real das taxas de variação medidas pelo sensor ultrassônico durante as fases de atracamento (Bomba LIGADA) e repouso (Bomba DESLIGADA).")

    t_cycle = np.linspace(0, 2.0, 200)
    d_cycle = []
    for t_i in t_cycle:
        if t_i <= 1.458:
            d_val = d_off - r_gnd_param * t_i
        elif t_i <= 1.737:
            d_val = d_on + (76.6 - r_gnd_param) * (t_i - 1.458)
        else:
            d_val = d_off - r_gnd_param * (t_i - 1.737)
        d_cycle.append(d_val)

    fig_aux = go.Figure()
    fig_aux.add_trace(go.Scatter(
        x=t_cycle, y=d_cycle, mode="lines", name="Distância lida pelo Sensor (cm)",
        line=dict(color="#1f77b4", width=3)
    ))

    fig_aux.add_annotation(
        x=0.7, y=d_off - 1.0,
        text=f"<b>1. Fase Enchimento (Bomba DESLIGADA):</b><br>Taxa Subida = +{r_gnd_param:.2f} cm/h (Freático)",
        showarrow=True, arrowhead=2, ax=40, ay=-40,
        font=dict(color="green", size=11), arrowcolor="green"
    )
    fig_aux.add_annotation(
        x=1.6, y=(d_on + d_off) / 2.0,
        text=f"<b>2. Fase Esvaziamento (Bomba LIGADA):</b><br>Taxa Rebaixamento = -{(76.6 - r_gnd_param):.2f} cm/h (Líquida)",
        showarrow=True, arrowhead=2, ax=-120, ay=0,
        font=dict(color="red", size=11), arrowcolor="red"
    )

    fig_aux.add_hline(y=d_on, line_dash="dash", line_color="red", annotation_text=f"Bóia 1 LIGA ({d_on:.1f} cm)", annotation_position="top right")
    fig_aux.add_hline(y=d_off, line_dash="dash", line_color="green", annotation_text=f"Bóia 1 DESLIGA ({d_off:.1f} cm)", annotation_position="top right")

    fig_aux.update_layout(
        title="Ciclo de Operação do Poço (Demonstração de Slopes/Taxas pelo Sensor)",
        xaxis_title="Tempo (Horas)",
        yaxis=dict(autorange="reversed", title="Distância do Sensor à Água (cm)"),
        height=420,
        margin=dict(l=20, r=20, t=40, b=40)
    )
    st.plotly_chart(fig_aux, use_container_width=True)'''

assert old_sec3_latex in content, "old_sec3_latex not found"
content = content.replace(old_sec3_latex, new_sec3_latex, 1)

open(path, 'w', encoding='utf-8').write(content)
print("Fix for LaTeX formatting and auxiliary chart added successfully.")
