"""
Módulo gerador do Diagrama Esquemático em escala real da seção transversal do poço.
"""

from typing import Optional
import plotly.graph_objects as go
from src.config import FUNDODOPOCO


def build_well_diagram_figure(
    dist_borda_cm: float,
    d_on: float,
    d_off: float,
    tem_bomba2: bool = False,
    d_on2: float = 0.0,
    d_off2: float = 0.0,
    latest_distance: Optional[float] = None,
) -> go.Figure:
    """Constrói a figura Plotly com corte esquemático em escala real do poço (Topo do Poço = 0 cm)."""
    y_topo = 0.0
    y_sensor = abs(dist_borda_cm)
    y_risco = y_sensor + 20.0
    y_don = d_on
    y_doff = d_off
    y_don2 = d_on2
    y_doff2 = d_off2
    y_fundo = FUNDODOPOCO

    if latest_distance is not None:
        y_agua = latest_distance
    else:
        y_agua = y_don

    dist_sensor_agua = max(y_agua - y_sensor, 0.0)

    y_top = -20.0
    y_bottom = y_fundo + 20.0

    w = 1.0
    wwall = 0.08

    fig = go.Figure()

    # 1. Fundo do poço
    fig.add_shape(
        type="rect",
        x0=-w/2, x1=w/2, y0=y_fundo, y1=y_fundo + 10,
        fillcolor="#795548", line_color="#4e342e", line_width=2
    )

    # 2. Paredes do poço
    for xi0, xi1 in [(-w/2 - wwall, -w/2), (w/2, w/2 + wwall)]:
        fig.add_shape(
            type="rect",
            x0=xi0, x1=xi1, y0=y_topo, y1=y_fundo + 10,
            fillcolor="#9e9e9e", line_color="#616161", line_width=1.5
        )

    # 3. Sensor e haste
    fig.add_shape(
        type="rect",
        x0=-w/4, x1=w/4, y0=y_sensor - 5, y1=y_sensor,
        fillcolor="#ff9800", line_color="#e67e22", line_width=2
    )
    fig.add_shape(
        type="line",
        x0=0, x1=0, y0=y_topo, y1=y_sensor - 5,
        line=dict(color="#333333", width=2.5, dash="solid")
    )

    # 4. Faixa de Risco do Sensor
    fig.add_shape(
        type="rect",
        x0=-w/2, x1=w/2, y0=y_sensor, y1=y_risco,
        fillcolor="rgba(231,76,60,0.15)", line_width=0
    )
    fig.add_shape(
        type="line",
        x0=-w/2, x1=w/2, y0=y_risco, y1=y_risco,
        line=dict(color="#e74c3c", width=1.5, dash="dot")
    )

    # 5. Zona de atuação da Bomba 1
    fig.add_shape(
        type="rect",
        x0=-w/2, x1=w/2, y0=min(y_don, y_doff), y1=max(y_don, y_doff),
        fillcolor="rgba(52,152,219,0.15)", line_width=0
    )

    # 6. Água atual no poço
    if y_agua <= y_fundo:
        fig.add_shape(
            type="rect",
            x0=-w/2 + 0.01, x1=w/2 - 0.01,
            y0=max(y_agua, y_top), y1=y_fundo,
            fillcolor="rgba(30,144,255,0.40)",
            line_color="rgba(30,144,255,0.7)", line_width=1.5
        )

    # 7. Linha do Topo do Poço (0 cm)
    fig.add_shape(
        type="line",
        x0=-w/2 - wwall, x1=w/2 + wwall, y0=0, y1=0,
        line=dict(color="black", width=3.5)
    )

    # 8. Linhas de liga/desliga bomba
    diag_lines = [
        (y_don, f"Bomba 1 LIGA ({y_don:.1f} cm)", "#e74c3c"),
        (y_doff, f"Bomba 1 DESLIGA ({y_doff:.1f} cm)", "#27ae60"),
    ]
    if tem_bomba2:
        diag_lines.extend([
            (y_don2, f"Bomba 2 LIGA ({y_don2:.1f} cm)", "#d35400"),
            (y_doff2, f"Bomba 2 DESLIGA ({y_doff2:.1f} cm)", "#16a085"),
        ])

    for y_lv, _, clr in diag_lines:
        fig.add_shape(
            type="line",
            x0=-w/2, x1=w/2, y0=y_lv, y1=y_lv,
            line=dict(color=clr, width=2, dash="dash")
        )

    # 9. Linha do nível atual da água
    fig.add_shape(
        type="line",
        x0=-w/2 + 0.01, x1=w/2 - 0.01, y0=y_agua, y1=y_agua,
        line=dict(color="#0056b3", width=3)
    )

    # 10. Anotações laterais
    ann_x = w/2 + wwall + 0.08
    annotations = [
        dict(x=ann_x, y=0, text="<b>Topo do Poço</b> (0 cm)",
             xanchor="left", showarrow=True, ax=30, ay=0,
             font=dict(color="black", size=12),
             arrowcolor="black", arrowwidth=2),
        dict(x=ann_x, y=y_sensor, text=f"<b>Sensor Físico</b><br>+{y_sensor:.0f} cm pendurado no poço",
             xanchor="left", showarrow=True, ax=30, ay=0,
             font=dict(color="#e67e22", size=11),
             arrowcolor="#e67e22", arrowwidth=1.5),
        dict(x=ann_x, y=y_risco, text=f"<b>Risco Submersão</b><br>+{y_risco:.0f} cm (20 cm do sensor)",
             xanchor="left", showarrow=True, ax=30, ay=0,
             font=dict(color="#e74c3c", size=10),
             arrowcolor="#e74c3c", arrowwidth=1.2),
        dict(x=ann_x, y=y_agua,
             text=f"<b>Nível Atual da Água</b><br>+{y_agua:.1f} cm do topo (sensor lê: {dist_sensor_agua:.1f} cm)",
             xanchor="left", showarrow=True, ax=30, ay=0,
             font=dict(color="#0056b3", size=11),
             arrowcolor="#0056b3", arrowwidth=1.8),
        dict(x=-ann_x, y=y_don,
             text=f"Bomba 1 LIGA<br>+{y_don:.1f} cm do topo",
             xanchor="right", showarrow=True, ax=-30, ay=0,
             font=dict(color="#e74c3c", size=10),
             arrowcolor="#e74c3c", arrowwidth=1.2),
        dict(x=-ann_x, y=y_doff,
             text=f"Bomba 1 DESLIGA<br>+{y_doff:.1f} cm do topo",
             xanchor="right", showarrow=True, ax=-30, ay=0,
             font=dict(color="#27ae60", size=10),
             arrowcolor="#27ae60", arrowwidth=1.2),
        dict(x=-ann_x, y=y_fundo,
             text=f"Fundo do poço<br>+{y_fundo:.0f} cm do topo",
             xanchor="right", showarrow=True, ax=-30, ay=0,
             font=dict(color="#795548", size=10),
             arrowcolor="#795548", arrowwidth=1.2),
    ]

    fig.update_layout(
        title=f"📐 Corte Esquemático do Poço em Escala Real (Topo do Poço = 0 cm | Nível Atual = {y_agua:.1f} cm)",
        xaxis=dict(range=[-w*1.6, w*1.6], showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(autorange="reversed", range=[y_bottom, y_top], title="Profundidade a partir do Topo do Poço (cm)"),
        annotations=annotations,
        height=650,
        margin=dict(l=20, r=20, t=50, b=20),
        plot_bgcolor="#f8f9fa",
        paper_bgcolor="white",
    )

    return fig
