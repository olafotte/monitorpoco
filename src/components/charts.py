"""
Componentes visuais e gráficos Plotly reutilizáveis para o sistema de monitoramento de poço.
"""

from typing import Tuple, Optional, Any
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from src.models.pump_cycles import segment_operational_periods


def plot_historical_extremes_chart(
    df: pd.DataFrame
) -> Tuple[Optional[go.Figure], Optional[Any], Optional[Any], Optional[Any], Optional[Any]]:
    """Gera gráfico interativo Plotly anotando exatamente quando ocorreram as máximas e mínimas históricas."""
    if df is None or df.empty or "dt_round" not in df.columns:
        return None, None, None, None, None

    col_nivel = "nivel_imputed" if "nivel_imputed" in df.columns else "nivel_cm"
    if col_nivel not in df.columns:
        return None, None, None, None, None

    df_calc = df.sort_values("dt_round").reset_index(drop=True)
    df_ench, df_esv = segment_operational_periods(df_calc)

    pos_max_water = int(df_calc[col_nivel].idxmin())
    pos_min_water = int(df_calc[col_nivel].idxmax())

    row_max_w = df_calc.iloc[pos_max_water]
    row_min_w = df_calc.iloc[pos_min_water]

    row_peak_rise = df_ench.loc[df_ench["rate_cmh"].idxmax()] if not df_ench.empty else None
    row_peak_drop = df_esv.loc[df_esv["rate_cmh"].idxmax()] if not df_esv.empty else None

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=df_calc["dt_round"],
            y=df_calc[col_nivel],
            mode="lines",
            name="Nível do Poço (cm)",
            line=dict(color="#1f77b4", width=2.0),
            hovertemplate="<b>Nível: %{y:.1f} cm</b><br>Data: %{x|%d/%m/%Y %H:%M}<extra></extra>"
        )
    )

    # 1. Água mais alta (Pico)
    d1 = float(row_max_w[col_nivel])
    t1_dt = row_max_w["dt_round"]
    t1_str = pd.to_datetime(t1_dt).strftime("%d/%m/%Y às %H:%M")
    fig.add_trace(
        go.Scatter(
            x=[t1_dt], y=[d1], mode="markers", name="Água Mais Alta (Pico)",
            marker=dict(symbol="star", size=15, color="#d9534f", line=dict(width=1.5, color="black")),
            showlegend=True
        )
    )
    fig.add_annotation(
        x=t1_dt, y=d1,
        text=f"<b>🔴 Água Mais Alta: {d1:.1f} cm</b><br>{t1_str}",
        showarrow=True, arrowhead=2, arrowsize=1.2, arrowcolor="#d9534f",
        ax=0, ay=45,
        bgcolor="rgba(255, 255, 255, 0.95)", bordercolor="#d9534f", borderwidth=1.5,
        font=dict(size=11, color="#333333")
    )

    # 2. Água mais baixa (Vale)
    d2 = float(row_min_w[col_nivel])
    t2_dt = row_min_w["dt_round"]
    t2_str = pd.to_datetime(t2_dt).strftime("%d/%m/%Y às %H:%M")
    fig.add_trace(
        go.Scatter(
            x=[t2_dt], y=[d2], mode="markers", name="Água Mais Baixa (Vale)",
            marker=dict(symbol="diamond", size=13, color="#0275d8", line=dict(width=1.5, color="black")),
            showlegend=True
        )
    )
    fig.add_annotation(
        x=t2_dt, y=d2,
        text=f"<b>🔵 Água Mais Baixa: {d2:.1f} cm</b><br>{t2_str}",
        showarrow=True, arrowhead=2, arrowsize=1.2, arrowcolor="#0275d8",
        ax=0, ay=-45,
        bgcolor="rgba(255, 255, 255, 0.95)", bordercolor="#0275d8", borderwidth=1.5,
        font=dict(size=11, color="#333333")
    )

    # 3. Maior taxa de subida
    if row_peak_rise is not None:
        r3 = float(row_peak_rise["rate_cmh"])
        t3_dt = row_peak_rise["t_start"]
        t3_str = pd.to_datetime(t3_dt).strftime("%d/%m/%Y às %H:%M")
        d3 = float(row_peak_rise["d_start"])
        fig.add_trace(
            go.Scatter(
                x=[t3_dt], y=[d3], mode="markers", name="Pico Taxa Subida",
                marker=dict(symbol="triangle-up", size=13, color="#f0ad4e", line=dict(width=1.5, color="black")),
                showlegend=True
            )
        )
        fig.add_annotation(
            x=t3_dt, y=d3,
            text=f"<b>⚡ Pico Subida: +{r3:.1f} cm/h</b><br>{t3_str}",
            showarrow=True, arrowhead=2, arrowcolor="#f0ad4e",
            ax=50, ay=-35,
            bgcolor="rgba(255, 255, 255, 0.95)", bordercolor="#f0ad4e", borderwidth=1.5,
            font=dict(size=11, color="#333333")
        )

    # 4. Maior taxa de descida (Bomba)
    if row_peak_drop is not None:
        r4 = float(row_peak_drop["rate_cmh"])
        t4_dt = row_peak_drop["t_start"]
        t4_str = pd.to_datetime(t4_dt).strftime("%d/%m/%Y às %H:%M")
        d4 = float(row_peak_drop["d_start"])
        fig.add_trace(
            go.Scatter(
                x=[t4_dt], y=[d4], mode="markers", name="Pico Taxa Descida (Bomba)",
                marker=dict(symbol="triangle-down", size=13, color="#5cb85c", line=dict(width=1.5, color="black")),
                showlegend=True
            )
        )
        fig.add_annotation(
            x=t4_dt, y=d4,
            text=f"<b>🌊 Pico Descida: -{r4:.1f} cm/h</b><br>{t4_str}",
            showarrow=True, arrowhead=2, arrowcolor="#5cb85c",
            ax=-50, ay=35,
            bgcolor="rgba(255, 255, 255, 0.95)", bordercolor="#5cb85c", borderwidth=1.5,
            font=dict(size=11, color="#333333")
        )

    fig.update_layout(
        title="📈 Ocorrência Temporal dos Extremos de Nível e Períodos Operacionais",
        yaxis=dict(autorange="reversed", title="Nível da Água (cm abaixo do topo do poço)"),
        xaxis=dict(title="Data / Hora"),
        legend=dict(orientation="h", y=-0.25, x=0.5, xanchor="center"),
        height=540,
        margin=dict(l=30, r=40, t=50, b=60),
    )

    return fig, row_max_w, row_min_w, row_peak_rise, row_peak_drop


def plot_rate_histograms(df: pd.DataFrame) -> Tuple[Optional[go.Figure], Optional[go.Figure]]:
    """Gera dois histogramas Plotly com a distribuição das taxas médias calculadas por período completo."""
    df_ench, df_esv = segment_operational_periods(df)

    fig_ench = None
    if not df_ench.empty:
        fig_ench = px.histogram(
            df_ench,
            x="rate_cmh",
            nbins=100,
            title="🟢 Distribuição das Taxas de Subida / Enchimento por Período (cm/h)",
            labels={"rate_cmh": "Taxa Média do Período (cm/h)", "count": "Frequência (Períodos)"},
            color_discrete_sequence=["#2e7d32"],
        )
        fig_ench.update_layout(
            height=360,
            margin=dict(l=20, r=20, t=40, b=40),
            showlegend=False,
            bargap=0.05,
        )

    fig_esv = None
    if not df_esv.empty:
        fig_esv = px.histogram(
            df_esv,
            x="rate_cmh",
            nbins=100,
            title="🔵 Distribuição das Taxas de Descida / Bombeamento por Período (cm/h)",
            labels={"rate_cmh": "Taxa Média do Bombeamento (cm/h)", "count": "Frequência (Períodos)"},
            color_discrete_sequence=["#1565c0"],
        )
        fig_esv.update_layout(
            height=360,
            margin=dict(l=20, r=20, t=40, b=40),
            showlegend=False,
            bargap=0.05,
        )

    return fig_ench, fig_esv


def plot_pump_cycles_volume_chart(df_cycles: pd.DataFrame) -> Optional[go.Figure]:
    """Gera gráfico de barras verticais onde a largura de cada barra corresponde 
    ao intervalo temporal de acionamento da bomba [t_liga, t_desliga] e a altura 
    representa o volume de água expulso em Litros.
    """
    if df_cycles is None or df_cycles.empty:
        return None

    df_plot = df_cycles.copy()
    df_plot["t_liga"] = pd.to_datetime(df_plot["t_liga"])
    df_plot["t_desliga"] = pd.to_datetime(df_plot["t_desliga"])

    df_plot["x_mid"] = df_plot["t_liga"] + (df_plot["t_desliga"] - df_plot["t_liga"]) / 2
    df_plot["width_ms"] = (df_plot["t_desliga"] - df_plot["t_liga"]).dt.total_seconds() * 1000.0

    fig = go.Figure()

    fig.add_trace(
        go.Bar(
            x=df_plot["x_mid"],
            y=df_plot["vol_expulso_L"],
            width=df_plot["width_ms"],
            name="Volume Expulso (L)",
            marker=dict(
                color=df_plot["vol_expulso_L"],
                colorscale="Blues",
                showscale=True,
                colorbar=dict(title="Volume (L)", len=0.8),
                line=dict(color="#0d47a1", width=1.2),
            ),
            customdata=np.stack((
                df_plot["t_liga"].dt.strftime("%d/%m/%Y %H:%M:%S"),
                df_plot["t_desliga"].dt.strftime("%d/%m/%Y %H:%M:%S"),
                df_plot["duracao_min"],
                df_plot["d_alto_cm"],
                df_plot["d_baixo_cm"],
                df_plot["delta_cm"],
                df_plot["vol_expulso_m3"],
            ), axis=-1),
            hovertemplate=(
                "<b>🌊 Ciclo de Bombeamento Real</b><br>"
                "🟢 Início (LIGA): %{customdata[0]}<br>"
                "🔴 Fim (DESLIGA): %{customdata[1]}<br>"
                "⏱️ Duração: %{customdata[2]:.1f} min<br>"
                "📏 Nível Inicial: %{customdata[3]:.1f} cm | Nível Final: %{customdata[4]:.1f} cm<br>"
                "📉 Rebaixamento Δh: %{customdata[5]:.1f} cm<br>"
                "<b>💧 Volume Expulso: %{y:,.0f} Litros (%{customdata[6]:.3f} m³)</b>"
                "<extra></extra>"
            ),
        )
    )

    fig.update_layout(
        title="📊 Volume Expulso por Ciclo de Bombeamento (Largura da Barra = Duração do Acionamento)",
        xaxis=dict(
            title="Data / Hora do Acionamento [Início → Fim]",
            type="date",
        ),
        yaxis=dict(
            title="Volume Expulso (Litros)",
            gridcolor="rgba(0,0,0,0.08)",
        ),
        height=450,
        margin=dict(l=40, r=40, t=50, b=50),
        bargap=0.1,
    )

    return fig
