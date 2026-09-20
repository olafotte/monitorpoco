"""
Componente da Aba 8: Diagrama Esquemático da Seção Transversal do Poço.
"""

from typing import Optional
import streamlit as st

from src.config import FUNDODOPOCO
from src.components.diagram import build_well_diagram_figure


def render_tab_diagram(
    dist_borda_cm: float,
    d_on: float,
    d_off: float,
    tem_bomba2: bool = False,
    d_on2: float = 0.0,
    d_off2: float = 0.0,
    latest_distance: Optional[float] = None,
    capacidade_total_cm: float = FUNDODOPOCO,
) -> None:
    """Renderiza a Aba 8 com o diagrama esquemático do poço em corte e métricas geométricas."""
    st.subheader("🗂️ Diagrama do Poço de Drenagem")
    st.caption("Diagrama esquemático em escala real a partir do Topo do Poço (0 cm). Atualiza automaticamente ao alterar qualquer parâmetro.")

    y_sensor = abs(dist_borda_cm)
    y_fundo = FUNDODOPOCO

    if latest_distance is not None:
        y_agua = latest_distance
    else:
        y_agua = d_on

    dist_sensor_agua = max(y_agua - y_sensor, 0.0)

    fig_diag = build_well_diagram_figure(
        dist_borda_cm=dist_borda_cm,
        d_on=d_on,
        d_off=d_off,
        tem_bomba2=tem_bomba2,
        d_on2=d_on2,
        d_off2=d_off2,
        latest_distance=latest_distance,
    )
    st.plotly_chart(fig_diag, width="stretch")

    col_l1, col_l2, col_l3 = st.columns(3)
    col_l1.metric("Profundidade Total do Poço", f"{y_fundo:.0f} cm", help="Fundo do poço a partir do topo superior")
    if latest_distance is not None:
        altura_agua_cm = max(y_fundo - y_agua, 0.0)
        col_l2.metric("Lâmina d'Água no Fundo", f"{altura_agua_cm:.0f} cm")
        col_l3.metric("Distância da Água ao Sensor", f"{dist_sensor_agua:.0f} cm", delta=f"Sensor pendurado a {y_sensor:.0f} cm do topo")
    else:
        col_l2.metric("Lâmina d'Água", "Sem leitura")
        col_l3.metric("Distância ao Sensor", "Sem leitura")

    col_l1.metric("Capacidade total do poço", f"{capacidade_total_cm:.0f} cm", help="Fundo do poço até a borda superior")
    if latest_distance is not None:
        dist_ao_transbordo = latest_distance + dist_borda_cm
        altura_agua_cm = max(FUNDODOPOCO - latest_distance, 0.0)
        col_l2.metric("Nível da água (acima do fundo)", f"{altura_agua_cm:.0f} cm")
        col_l3.metric("📦 Folga até a borda", f"{dist_ao_transbordo:.0f} cm", delta=f"{dist_ao_transbordo / capacidade_total_cm * 100:.0f}% da capacidade")
    else:
        col_l2.metric("Nível da água", "Sem leitura")
        col_l3.metric("📦 Folga até a borda", "Sem leitura")

    st.markdown(f"""
    **Legenda:**
    - 🟥 Zona vermelha: região acima do sensor (borda do poço) — água aqui = transbordamento
    - 🟦 Zona azul: faixa de operação da bomba (liga a {d_on:.0f} cm, desliga a {d_off:.0f} cm)
    - 🟦 Água atual: nível medido pelo sensor
    - 🟠 Sensor: ponto de referência zero (distância = 0)
    """)
