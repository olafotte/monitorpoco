"""src/components/tabs/tab_report.py
Tab 9: Relatório de Adequação Técnica da Bomba.
Avalia a capacidade instalada de drenagem frente a normas técnicas (ABNT NBR 10844)
e chuvas de projeto (Curvas IDF / Estação Ponte Adolfo Konder / ERA5).
"""

import datetime as dt
from typing import Dict, Any, List, Optional
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.config import FUNDODOPOCO
from src.data.adolfo_konder import load_adolfo_konder_dataset
from src.data.defesa_civil import fetch_defesa_civil_rankings
from src.data.weather import fetch_historical_5years_precip
from src.models.gumbel import fit_gumbel, gumbel_quantile
from src.utils import LOCAL_TZ, now_local


def render_tab_report(
    latitude: float,
    longitude: float,
    fator_m3h_para_cmh: float,
    factor_mm_cm: float,
    era5_correction: float,
    dist_borda_cm: float,
    capacidade_total_cm: float,
    vazao_bomba_m3h: float,
    vazao_total_m3h: float,
    r_pump_param: float,
    r_pump_total_param: float,
    tem_bomba2: bool,
    d_on: float,
    d_off: float,
    d_on2: float,
    d_off2: float,
) -> None:
    """Renderiza a aba de Relatório de Adequação Técnica da Bomba."""
    _now_br = now_local()

    st.subheader("📋 Relatório de Adequação da Bomba de Drenagem")
    st.caption(
        "Relatório técnico automático baseado nos parâmetros do sistema, Curvas IDF de Gumbel da "
        "Estação Pluviométrica Ponte Adolfo Konder (Blumenau) e normas ABNT NBR 10844. Atualizado a cada re-execução."
    )

    # ── Parâmetros derivados ────────────────────────────────────────────────────
    area_poco_m2 = round(100.0 / fator_m3h_para_cmh, 2)
    area_drenagem_m2 = round(factor_mm_cm * area_poco_m2 / 0.1, 1)
    volume_buffer_L = round(dist_borda_cm * 0.01 * area_poco_m2 * 1000, 0)

    # ── Buscar dados históricos (cache compartilhado) ───────────────────────────
    _dc_rep = fetch_defesa_civil_rankings()
    _oldest_rep = "2015-01-08"
    if _dc_rep:
        _all_d = []
        for _dfr in _dc_rep.values():
            for _rr in _dfr["Data/Hora"]:
                try:
                    _all_d.append(pd.to_datetime(_rr, dayfirst=True))
                except Exception:
                    pass
        if _all_d:
            _oldest_rep = min(_all_d).strftime("%Y-%m-%d")

    with st.spinner("Carregando dados históricos para o relatório…"):
        rep_hist_df, _rep_err = fetch_historical_5years_precip(
            latitude, longitude, start_date_str=_oldest_rep
        )

    if not rep_hist_df.empty and era5_correction != 1.0:
        rep_hist_df = rep_hist_df.copy()
        rep_hist_df["precipitation"] = rep_hist_df["precipitation"] * era5_correction

    # ── Obter dados IDF da Estação Ponte Adolfo Konder (Medições Locais Reais) ──
    _konder_rep_df = load_adolfo_konder_dataset()
    _fonte_idf_rep = "Estação Pluviométrica Ponte Adolfo Konder (Medições Locais Reais - Blumenau 2021–2025)"
    _usou_konder_rep = False

    _DURACOES = {"1h": 1, "2h": 2, "3h": 3, "6h": 6, "12h": 12, "24h": 24}
    _DURACOES_K = {"1h": 4, "2h": 8, "3h": 12, "6h": 24, "12h": 48, "24h": 96}
    _TRS = [2, 5, 10, 25, 50, 100]
    _TR_NORMA = 25

    idf_rep: Dict[str, Dict[int, float]] = {}

    if not _konder_rep_df.empty:
        _usou_konder_rep = True
        for _lbl, _n15 in _DURACOES_K.items():
            _roll = _konder_rep_df["precip_15min"].rolling(_n15, min_periods=1).sum()
            _nh = _n15 / 4.0
            _intens = _roll / _nh
            _ann = _intens.groupby(pd.DatetimeIndex(_intens.index).year).max().dropna()
            if len(_ann) >= 2:
                _a, _u = fit_gumbel(np.asarray(_ann.values, dtype=np.float64))
                idf_rep[_lbl] = {tr: round(gumbel_quantile(_a, _u, tr), 1) for tr in _TRS}

    if not idf_rep:
        _fonte_idf_rep = f"Modelo ERA5 (Open-Meteo ×{era5_correction:.2f})"
        if not rep_hist_df.empty:
            for _lbl, _nh in _DURACOES.items():
                _roll = rep_hist_df["precipitation"].rolling(_nh).sum()
                _intens = _roll / _nh
                _ann = _intens.groupby(_intens.index.year).max().dropna()
                if len(_ann) >= 3:
                    _a, _u = fit_gumbel(np.asarray(_ann.values, dtype=np.float64))
                    idf_rep[_lbl] = {tr: round(gumbel_quantile(_a, _u, tr), 1) for tr in _TRS}

    # ── Análise de adequação (referência: 1h) ──────────────────────────────────
    _dur_ref = "1h"
    adequacy_rep = {}
    if _dur_ref in idf_rep:
        for tr in _TRS:
            q_mmh = idf_rep[_dur_ref][tr]
            q_cmh = round(q_mmh * factor_mm_cm, 1)
            margem = round((r_pump_total_param - q_cmh) / q_cmh * 100, 1)
            adequacy_rep[tr] = {
                "q_mmh": q_mmh,
                "q_cmh": q_cmh,
                "pump_cmh": round(r_pump_total_param, 1),
                "adequate": bool(r_pump_total_param >= q_cmh),
                "margem_pct": margem,
            }

    vered = adequacy_rep.get(_TR_NORMA, {})
    _adeq_val = vered.get("adequate", None)
    bomba_ok = bool(_adeq_val) if _adeq_val is not None else None

    # ── CABEÇALHO ───────────────────────────────────────────────────────────────
    st.markdown("---")
    col_tit, col_badge = st.columns([3, 1])
    with col_tit:
        st.markdown("## Relatório de Adequação Hidráulica")
        st.markdown(
            f"**Local:** lat {latitude:.4f}, lon {longitude:.4f} &nbsp;|&nbsp; "
            f"**Emitido:** {_now_br.strftime('%d/%m/%Y %H:%M')} (Horário de Brasília)"
        )
    with col_badge:
        if bomba_ok is True:
            st.success("BOMBA ADEQUADA\n\nTr 25a – NBR 10844")
        elif bomba_ok is False:
            st.error("BOMBA INSUFICIENTE\n\nTr 25a – NBR 10844")
        else:
            st.warning("DADOS INSUFICIENTES")

    # ── Diagnóstico automático quando dados insuficientes ───────────────────────
    if bomba_ok is None:
        with st.expander("Diagnóstico — por que os dados são insuficientes?", icon=":material/find_in_page:", expanded=True):
            st.markdown("**Verificando cada etapa do pipeline de dados:**")

            # 1. Fetch histórico
            if rep_hist_df.empty:
                st.error(f"❌ **Dados históricos ERA5 não carregados.**  \nErro retornado: `{_rep_err}`")
                st.markdown(
                    "**Possíveis causas:**\n"
                    "- Sem conexão com a internet\n"
                    "- API Open-Meteo Archive temporariamente indisponível\n"
                    "- Coordenadas inválidas (lat/lon fora do range aceito pela API)\n\n"
                    f"**Parâmetros enviados:** lat={latitude}, lon={longitude}, start={_oldest_rep}"
                )
            else:
                _anos_diag = sorted(rep_hist_df.index.year.unique())
                _n_rows = len(rep_hist_df)
                st.success(
                    f"✅ **Dados ERA5 carregados:** {_n_rows:,} horas | "
                    f"Período: {_anos_diag[0]}–{_anos_diag[-1]} | "
                    f"Anos disponíveis: {len(_anos_diag)}"
                )

            # 2. Data de início (Defesa Civil)
            if _dc_rep:
                st.success(f"✅ **Defesa Civil:** dados carregados. Data mais antiga encontrada: `{_oldest_rep}`")
            else:
                st.warning(
                    f"⚠️ **Defesa Civil:** sem dados (scraping falhou ou cache ausente). "
                    f"Usando data de início padrão: `{_oldest_rep}`"
                )

            # 3. IDF por duração
            _anos_diag = list(pd.DatetimeIndex(rep_hist_df.index).year.unique()) if not rep_hist_df.empty else []
            st.markdown("**Status da curva IDF por duração:**")
            _idf_diag_rows = []
            if not rep_hist_df.empty:
                for _lbl2, _nh2 in _DURACOES.items():
                    _roll2 = rep_hist_df["precipitation"].rolling(_nh2).sum()
                    _intens2 = _roll2 / _nh2
                    _ann2 = _intens2.groupby(pd.DatetimeIndex(_intens2.index).year).max().dropna()
                    _ok = len(_ann2) >= 3
                    _idf_diag_rows.append({
                        "Duração": _lbl2,
                        "Anos com máxima anual": len(_ann2),
                        "Mín. necessário": 3,
                        "IDF calculada": "✅ Sim" if _ok else "❌ Não (anos insuficientes)",
                        "Máx. observada (mm/h)": f"{_ann2.max():.1f}" if not _ann2.empty else "—",
                    })
            else:
                for _lbl2 in _DURACOES:
                    _idf_diag_rows.append({
                        "Duração": _lbl2,
                        "Anos com máxima anual": 0,
                        "Mín. necessário": 3,
                        "IDF calculada": "❌ Sem dados ERA5",
                        "Máx. observada (mm/h)": "—",
                    })
            st.dataframe(
                pd.DataFrame(_idf_diag_rows).set_index("Duração"),
                width="stretch",
            )

            # 4. Adequação
            if _dur_ref in idf_rep:
                st.success(f"✅ **IDF para duração de referência ({_dur_ref})** calculada. Análise de adequação disponível.")
            else:
                st.error(
                    f"❌ **IDF para duração de referência ({_dur_ref}) não disponível.** "
                    f"A análise de adequação requer pelo menos 3 anos de dados históricos "
                    f"com a janela de {_dur_ref}. Durações calculadas: `{list(idf_rep.keys()) or 'nenhuma'}`"
                )

            # 5. Resumo e sugestões de ação
            st.markdown("**Sugestões para resolver:**")
            if rep_hist_df.empty:
                st.markdown(
                    "1. Verifique a conexão com internet e recarregue a página\n"
                    "2. Confirme que as coordenadas estão corretas no sidebar\n"
                    "3. Tente ajustar a data de início da análise (aba Histórico & IDF)"
                )
            elif len(_anos_diag) < 3:
                st.markdown(
                    f"1. Apenas {len(_anos_diag)} ano(s) de dados disponíveis — "
                    f"são necessários pelo menos 3\n"
                    "2. Aumente o período de análise na aba **Histórico & IDF**\n"
                    "3. Se a API retornar dados muito recentes, aguarde alguns dias para "
                    f"o período se completar"
                )
            else:
                st.markdown(
                    "Os dados foram carregados mas a análise de adequação falhou. "
                    "Verifique se `fit_gumbel` recebeu valores válidos (sem NaN)."
                )

    st.markdown("---")

    # -- SEÇÃO 1: PARÂMETROS ----------------------------------------------------------------
    st.markdown("### 1. Parâmetros do Sistema Instalado")
    st.markdown(
        """
        Esta seção descreve o que foi instalado no local e é a base de comparação com
        o que as normas exigem. Antes de qualquer cálculo, precisamos saber exatamente
        com o que estamos trabalhando: qual é a capacidade real da bomba, qual é o tamanho
        físico do poço e quanta área de telhado/terra contribui com chuva para esse poço.
        """
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Capacidade Instalada",
        f"{vazao_total_m3h:.1f} m³/h",
        help=f"1ª Bomba: {vazao_bomba_m3h:.1f} m³/h" + (f" + 2ª Bomba: {vazao_bomba_m3h:.1f} m³/h (2x em paralelo)" if tem_bomba2 else " (1 bomba isolada)")
    )
    c2.metric("Velocidade de Rebaixamento", f"{r_pump_total_param:.1f} cm/h",
              help="Taxa máxima de rebaixamento do nível do poço com todas as bombas operando.")
    c3.metric("Seção Transversal do Poço", f"{area_poco_m2:.2f} m²",
              help="Área da abertura interna do poço. Poços maiores sobem mais devagar sob chuva igual.")
    c4.metric("Área de Drenagem Estimada", f"{area_drenagem_m2:.0f} m²",
              help=(
                  "Estimação da área total (telhados, pisos, jardins) cujas águas pluviais "
                  "são direcionadas para este poço. Calculado pelo fator de amplificação calibrado."
              ))

    _t_esvaziar = round(FUNDODOPOCO / r_pump_total_param, 1)
    _vol_1mm = round(area_drenagem_m2 * 0.001 * 1000, 0)
    _bomba2_rows = (
        f"| Bóia 2 LIGA quando distância | **≤ {d_on2:.0f} cm** | Emergência: aciona 2ª bomba em paralelo (+30 cm) |\n"
        f"| Bóia 2 DESLIGA quando distância | **≥ {d_off2:.0f} cm** | Desliga a 2ª bomba quando o nível baixa |\n"
    ) if tem_bomba2 else ""

    st.markdown(
        f"**O que esses números significam na prática?**\n\n"
        f"- A capacidade total instalada de bombeamento é de **{r_pump_total_param:.1f} cm/h** ({vazao_total_m3h:.1f} m³/h).\n"
        f"  Isso significa: se a chuva parar agora, o sistema leva cerca de **{_t_esvaziar} horas** "
        f"para esvaziar completamente o poço a partir do nível máximo.\n"
        f"- A área de drenagem de **{area_drenagem_m2:.0f} m²** significa que cada 1 mm de chuva coloca "
        f"aproximadamente **{_vol_1mm:.0f} litros** de água dentro do poço.\n"
        f"- O poço tem capacidade total de **{capacidade_total_cm:.0f} cm** de coluna d'água: "
        f"{FUNDODOPOCO} cm do sensor até o fundo + {dist_borda_cm:.0f} cm do sensor até a borda superior.\n\n"
        f"💡 **Nota Técnica — Por que a área/volume equivalente é maior que a seção do cilindro de concreto?**\n"
        f"> Embora a camisa cilíndrica de concreto do poço tenha um diâmetro físico de **1,20 m** (seção transversal de **1,13 m²**), o poço não opera de forma isolada. As galerias e tubulações de drenagem que deságuam nele estão instaladas em diferentes cotas com inclinação suave (semi-horizontais).\n>\n"
        f"> Quando o nível de água sobe no poço, ocorre o **efeito de remanso (backwater effect)**: a água retrocede e invade a rede de tubulações a montante. Para subir cada 1 cm no sensor do poço, a chuva precisa preencher não apenas o tubo vertical do poço, mas também metros de galerias conectadas (*Pipe Storage*). Esse volume em rede funciona como um **reservatório de amortecimento físico**, fazendo com que a seção acumuladora equivalente de água se comporte como uma área significativamente maior do que a camisa cilíndrica de 1,20 m.\n\n"
        f"| Parâmetro | Valor | Interpretação |\n"
        f"|---|---|---|\n"
        f"| Profundidade sensor → fundo | **{FUNDODOPOCO} cm** | Reservatório principal de água |\n"
        f"| Sensor → borda superior | **{dist_borda_cm:.0f} cm** | Margem de segurança extra antes do transbordamento |\n"
        f"| Capacidade total do poço | **{capacidade_total_cm:.0f} cm** | Volume total que o poço suporta |\n"
        f"| Volume de segurança acima do sensor | **{volume_buffer_L:.0f} L** | Reserva que ganha tempo antes do transbordo |\n"
        f"| Bóia 1 LIGA quando distância | **≤ {d_on:.0f} cm** | Quando a água sobe e aciona a 1ª bomba |\n"
        f"| Bóia 1 DESLIGA quando distância | **≥ {d_off:.0f} cm** | Quando o poço esvazia até desligar a 1ª bomba |\n"
        f"{_bomba2_rows}"
        f"| Fator correção ERA5 aplicado | **×{era5_correction:.2f}** | Ajuste dos dados climáticos (ver Seção 2) |"
    )

    # -- SEÇÃO 2: BASE DE DADOS -----------------------------------------------------------------
    st.markdown("### 2. Base de Dados Pluviométrica Utilizada")
    st.markdown(
        f"""
Para saber se a bomba é adequada, precisamos saber com qual chuva ela precisa lidar.
Usamos dados históricos reais de precipitação ao longo de vários anos. Essas informações
vêm de duas fontes complementares:

- **Estação Pluviométrica Ponte Adolfo Konder (Medições Locais Reais 2021–2025):** pluviômetro físico
  instalado no centro de Blumenau que registra chuvas continuamente em intervalos de 15 minutos. É a nossa
  **fonte primária para o cálculo da Curva IDF e Chuva de Projeto**, por capturar com máxima fidelidade os picos
  reais de tempestades locais.

- **Open-Meteo ERA5 (reanálise do ECMWF):** modelo climático global utilizado como base secundária e para histórico
  estendido de longo prazo.

O **fator de correção ×{era5_correction:.2f}** aplicado ao ERA5 significa que os picos reais observados
pela Defesa Civil são em média {(era5_correction-1)*100:.0f}% maiores do que o ERA5 registra.
Sem essa correção, estaremos projetando para uma chuva menor do que a real — o que
levaria a uma avaliação otimista demais sobre a adequação da bomba.
        """
    )
    if not rep_hist_df.empty:
        _anos = sorted(rep_hist_df.index.year.unique())
        _prec_med = rep_hist_df["precipitation"].sum() / len(_anos)
        _max_h = rep_hist_df["precipitation"].max()
        ca, cb, cc, cd = st.columns(4)
        ca.metric("Período analisado", f"{_anos[0]}–{_anos[-1]}")
        cb.metric("Anos de dados", str(len(_anos)))
        cc.metric("Precipitação média anual", f"{_prec_med:.0f} mm",
                  help="Média de chuva por ano no período analisado. Blumenau é uma das cidades mais chuvosas do Brasil.")
        cd.metric("Máx. horária ERA5 (corrigida)", f"{_max_h:.1f} mm/h",
                  help="A hora mais chuvosa registrada no período completo após correção.")
        st.caption(f"Fonte: Open-Meteo Archive API (ERA5) com fator ×{era5_correction:.2f} | Dados desde: {_oldest_rep}")
        if _dc_rep and "01h" in _dc_rep and "1h" in idf_rep:
            _dc_max = _dc_rep["01h"]["Acumulado_mm"].max()
            _era_tr100 = idf_rep["1h"].get(100, None)
            if _era_tr100:
                _ratio = _dc_max / _era_tr100 * 100
                st.info(
                    f"Validação cruzada: o maior evento de 1h registrado pela Defesa Civil foi de "
                    f"**{_dc_max:.1f} mm/h**. A curva IDF do ERA5 (corrigida) estima que chuvas assim "
                    f"correspondem a Tr ≈ 100 anos ({_era_tr100:.1f} mm/h no ERA5). "
                    f"Relação DC/ERA5-Tr100 = **{_ratio:.0f}%** — "
                    + (
                        "valores próximos de 100% indicam boa consistência entre as fontes."
                        if 80 <= _ratio <= 120
                        else f"diferença relevante — o fator de correção ×{era5_correction:.2f} tenta compensar isso."
                    )
                )
    else:
        st.warning(f"Dados históricos não disponíveis. Erro: {_rep_err}")

    # -- SEÇÃO 3: CURVA IDF -----------------------------------------------------------------------
    st.markdown("### 3. Intensidades de Projeto – Curva IDF")
    st.markdown(
        """
**O que é a curva IDF?**

IDF significa **Intensidade – Duração – Frequência**. É a ferramenta fundamental do projeto
hidrológico: ela responde à pergunta *"com que intensidade pode chover, por quanto tempo,
e com que frequência isso ocorre?"*

**O que é período de retorno (Tr)?**

Tr = 25 anos significa que aquele evento de chuva tem **probabilidade de 4% de ocorrer
em qualquer ano**. Não significa que vai chover exatamente assim a cada 25 anos exatos
— pode ocorrer dois anos seguidos, ou não ocorrer por 40 anos. É uma forma estatística
de descrever a *raridade* do evento. Quanto maior o Tr, mais rara é a chuva.

**Como foram calculados os valores?**

Para cada duração de chuva (1h, 2h, etc.), identificamos o maior evento registrado
em cada ano do histórico. Com esses valores extremos anuais, ajustamos uma
*distribuição de Gumbel* — modelo estatístico especificamente desenvolvido para análise
de máximos. Ele extrapola para períodos de retorno mais longos do que o período de dados
disponível, com incerteza crescente para Tr muito maiores que o histórico.

**Como usar a tabela abaixo:**

Cada célula mostra a *intensidade média* (mm/h) da chuva de projeto para aquela
combinação de duração e Tr. Por exemplo, "Tr 25a / 1h" é a intensidade de uma chuva
que dura 1 hora e tem Tr = 25 anos — o cenário que a norma NBR 10844 exige que o
sistema suporte.

A linha tracejada vermelha no gráfico indica a intensidade a partir da qual a bomba
instalada não consegue mais acompanhar o enchimento do poço (ponto de saturação).
        """
    )
    if idf_rep:
        _idf_rows = []
        for _lbl in _DURACOES:
            if _lbl not in idf_rep:
                continue
            row = {"Duração": _lbl}
            row.update({f"Tr {tr}a": f"{idf_rep[_lbl][tr]:.1f} mm/h" for tr in _TRS})
            _idf_rows.append(row)
        st.dataframe(pd.DataFrame(_idf_rows).set_index("Duração"), width="stretch")

        # Gráfico IDF
        fig_idf_r = go.Figure()
        _colors = px.colors.qualitative.Set2
        for _i, tr in enumerate(_TRS):
            _xv = [_DURACOES[l] for l in _DURACOES if l in idf_rep]
            _yv = [idf_rep[l][tr] for l in _DURACOES if l in idf_rep]
            fig_idf_r.add_trace(go.Scatter(
                x=_xv, y=_yv, mode="lines+markers", name=f"Tr {tr}a",
                line=dict(color=_colors[_i % len(_colors)],
                          width=3 if tr == _TR_NORMA else 1.5,
                          dash="solid" if tr == _TR_NORMA else "dot"),
                marker=dict(size=8 if tr == _TR_NORMA else 5),
            ))
        # Linha de saturação da bomba
        _sat_mmh = r_pump_param / factor_mm_cm
        fig_idf_r.add_hline(
            y=_sat_mmh, line_dash="dash", line_color="#e74c3c",
            annotation_text=f"Saturação bomba: {_sat_mmh:.1f} mm/h",
            annotation_position="top right",
            annotation_font_color="#e74c3c",
        )
        fig_idf_r.update_layout(
            title="Curva IDF – linha tracejada vermelha = limite de saturação da bomba instalada",
            xaxis_title="Duração (horas)", yaxis_title="Intensidade média (mm/h)",
            height=400,
            legend=dict(orientation="h", y=-0.28, x=0.5, xanchor="center"),
        )
        st.plotly_chart(fig_idf_r, width="stretch")
    else:
        st.warning("IDF não calculada — dados insuficientes.")

    # -- SEÇÃO 4: ADEQUAÇÃO -----------------------------------------------------------------------
    st.markdown("### 4. Comparação: Chuva de Projeto vs Capacidade da Bomba")
    st.markdown(
        f"""
**Como a bomba é comparada com a chuva de projeto?**

A lógica é simples: a chuva entra no poço a uma certa taxa (cm/h), e a bomba precisa
remover água a uma taxa igual ou maior para que o nível não suba continuamente.

Para converter chuva (mm/h) em taxa de enchimento do poço (cm/h), usamos o
**Fator de Amplificação** de {factor_mm_cm:.2f} calibrado em eventos reais:
cada 1 mm/h de chuva sobre a área de drenagem eleva o nível do poço em **{factor_mm_cm:.2f} cm/h**.

A *duração de referência de 1 hora* é a mais crítica para bombas de recalque:
é o tempo suficiente para saturar o poço, e é o padrão que a maioria das normas usa
para dimensionar sistemas de bombeamento de águas pluviais.

**Como ler a tabela e o gráfico:**
- Cada linha mostra: qual chuva (mm/h) ocorre naquele Tr, como isso se traduz em
  taxa de enchimento (cm/h), e se a bomba instalada consegue ou não acompanhar.
- As barras coloridas no gráfico mostram o quanto a bomba precisa trabalhar.
- A linha verde é a capacidade real da bomba instalada.
- Barras **acima** da linha verde = a bomba **não consegue acompanhar** essa chuva.
- Barras **abaixo** da linha = a bomba **dá conta** e o nível não sobe indefinidamente.
        """
    )
    if adequacy_rep:
        _adq_rows = []
        for tr in _TRS:
            a = adequacy_rep[tr]
            _adq_rows.append({
                "Tr (anos)": tr,
                "Intensidade 1h (mm/h)": a["q_mmh"],
                "Taxa de enchimento (cm/h)": a["q_cmh"],
                "Capacidade da Bomba (cm/h)": a["pump_cmh"],
                "Margem (%)": f"{a['margem_pct']:+.1f}%",
                "Resultado": "✅ Bomba dá conta" if a["adequate"] else "❌ Bomba insuficiente",
            })
        st.dataframe(pd.DataFrame(_adq_rows).set_index("Tr (anos)"), width="stretch")
        st.caption(
            f"Margem positiva = bomba tem folga. Margem negativa = falta capacidade. "
            f"A norma NBR 10844 exige que a bomba seja adequada para Tr = {_TR_NORMA} anos."
        )

        fig_adq_r = go.Figure()
        fig_adq_r.add_trace(go.Bar(
            x=[str(tr) for tr in _TRS],
            y=[adequacy_rep[tr]["q_cmh"] for tr in _TRS],
            name="Taxa de enchimento do poço (chuva de projeto)",
            marker_color=["#e74c3c" if not adequacy_rep[tr]["adequate"] else "#f39c12" for tr in _TRS],
        ))
        fig_adq_r.add_hline(
            y=r_pump_total_param, line_dash="dash", line_color="#27ae60",
            annotation_text=f"Bomba(s) instalada(s): {r_pump_total_param:.1f} cm/h ({vazao_total_m3h:.1f} m³/h)",
            annotation_position="top left", annotation_font_color="#27ae60",
        )
        fig_adq_r.update_layout(
            title="Barras vermelhas = bomba insuficiente | Amarelas = bomba adequada | Linha verde = capacidade instalada",
            xaxis_title="Período de Retorno (anos)",
            yaxis_title="Taxa de enchimento necessária (cm/h)",
            height=350,
        )
        st.plotly_chart(fig_adq_r, width="stretch")

    # -- SEÇÃO 5: BUFFER --------------------------------------------------------------------------
    st.markdown("### 5. Tempo de Amortecimento do Poço (Margem de Segurança Física)")
    st.markdown(
        f"""
**O poço como reservatório de segurança**

Mesmo que a bomba instalada não consiga remover água tão rápido quanto ela entra
durante uma chuva forte, o poço ainda tem uma margem física antes de transbordar:
são **{dist_borda_cm:.0f} cm** entre o sensor e a borda superior. Esses {dist_borda_cm:.0f} cm
funcionam como um reservatório de emergência.

O **tempo de amortecimento** é quanto tempo leva para o poço se encher completamente
com o excesso de água que a bomba não consegue remover. Isso importa porque a maioria
das chuvas intensas não sustenta sua intensidade máxima por mais de 30 a 60 minutos.
Se o poço aguentar esse pico sem transbordar, a bomba consegue esvaziar o restante
depois que a chuva diminuir.

**Como ler a tabela:**
- "Excesso não removido" = taxa de enchimento menos capacidade da bomba.
  Se for zero, a bomba dá conta e o poço nunca enche completamente naquele Tr.
- "Tempo até transbordo" = quanto tempo o poço aguenta com chuva contínua na
  intensidade máxima de projeto. Na prática, o sistema normalmente resiste mais tempo
  porque a intensidade varia ao longo do evento de chuva.
        """
    )
    if adequacy_rep:
        _buf_rows = []
        for tr in _TRS:
            a = adequacy_rep[tr]
            excesso = max(a["q_cmh"] - r_pump_total_param, 0.0)
            if excesso > 0:
                t_min = round(dist_borda_cm / excesso * 60, 0)
                nota = f"{t_min:.0f} min até transbordo"
            else:
                nota = "Sem risco — bomba suficiente para esse Tr"
            _buf_rows.append({
                "Tr (anos)": tr,
                "Excesso não removido (cm/h)": round(excesso, 1),
                "Buffer disponível (cm)": dist_borda_cm,
                "Tempo até transbordo": nota,
            })
        st.dataframe(pd.DataFrame(_buf_rows).set_index("Tr (anos)"), width="stretch")
        st.caption(
            "Nota: o tempo até transbordo assume chuva contínua na intensidade máxima de projeto. "
            "Cenário conservador — na prática, a intensidade varia e o sistema geralmente aguenta mais."
        )

    # -- SEÇÃO 6: NORMAS --------------------------------------------------------------------------
    st.markdown("### 6. Normas e Critérios de Projeto Aplicados")
    st.markdown(
        f"""
As normas de engenharia estabelecem o **nível mínimo de proteção** que uma instalação
deve ter, equilibrando custo e risco. Elas foram desenvolvidas por especialistas com base
em dados históricos e experiência acumulada ao longo de décadas:

- **ABNT NBR 10844:1989** — *Instalações prediais de águas pluviais*: a principal norma
  brasileira para dimensionamento de calhas, condutores e bombas de águas pluviais em
  edificações. Para uso coletivo (condomínios, comércio), exige que o sistema suporte
  chuvas com **Tr ≥ 25 anos**. Isso significa que há no máximo 4% de chance
  por ano de a chuva superar o que o sistema foi projetado para suportar.

- **ABNT NBR 5626:2020** — Norma geral para dimensionamento de sistemas prediais hidráulicos.

- **Manual de Drenagem DAEE/SP** — utilizado amplamente no Brasil para drenagem urbana;
  recomenda Tr entre 10 e 100 anos dependendo do risco (área residencial, via pública,
  área crítica, etc.).

**Por que Tr = 25 anos foi escolhido como critério principal?**

Um critério mais conservador (ex: Tr = 100 anos) seria mais seguro, mas exigiria uma bomba
muito maior e mais cara. Um critério muito brando (Tr = 2 anos) significaria transbordamento
frequente. Tr = 25 anos é o balanço estabelecido pela NBR 10844 para edificações coletivas —
um transbordamento esperado *no pior caso* a cada 25 anos de operação.

| Norma | Aplicação | Tr recomendado |
|---|---|---|
| **ABNT NBR 10844:1989** | Instalações prediais de águas pluviais | **Tr ≥ 25 anos** (uso coletivo) |
| **ABNT NBR 5626:2020** | Sistemas de água fria e quente | Dimensionamento geral |
| **Manual DAEE/SP** | Drenagem urbana | Tr 10–100 anos conforme risco |
| **ERA5 + DC Blumenau** | Dados históricos locais | Corrigido por ×{era5_correction:.2f} |
        """
    )

    # -- SEÇÃO 7: PARECER TÉCNICO FINAL -----------------------------------------------------------
    st.markdown("### 7. Parecer Técnico Final")
    if not adequacy_rep:
        st.warning("Dados insuficientes para emitir parecer.")
    else:
        _tr_max_ok = max((tr for tr in _TRS if adequacy_rep[tr]["adequate"]), default=None)
        if bomba_ok is True:
            _mg = vered.get("margem_pct", 0)
            _conf = "alta" if _mg >= 50 else ("moderada" if _mg >= 20 else "baixa — margem estreita")
            _txt_sistema = f"de 2 bombas em paralelo ({vazao_total_m3h:.1f} m³/h = {r_pump_total_param:.1f} cm/h)" if tem_bomba2 else f"da bomba instalada ({vazao_bomba_m3h:.1f} m³/h = {r_pump_param:.1f} cm/h)"
            st.success(
                f"✅ APROVADO — O sistema está dimensionado conforme a norma NBR 10844.\n\n"
                f"A capacidade do sistema {_txt_sistema} "
                f"consegue remover água mais rápido do que ela entra durante uma chuva de "
                f"período de retorno de **{_TR_NORMA} anos** (probabilidade de 4% ao ano). "
                f"A margem de segurança é de **{_mg:+.1f}%** — confiabilidade **{_conf}**."
            )
            if _mg < 20:
                st.warning(
                    f"Atenção: apesar de aprovada, a margem de {_mg:.1f}% é estreita. "
                    f"Qualquer redução de vazão por desgaste da bomba, entupimento das grades "
                    f"ou aumento da área de drenagem pode tornar o sistema insuficiente. "
                    f"Recomenda-se monitoramento periódico e medição da vazão real da bomba."
                )
        else:
            _def = abs(vered.get("margem_pct", 0))
            _q_needed = vered.get("q_cmh", r_pump_total_param * 1.3) / fator_m3h_para_cmh
            _q_norma_cmh = vered.get("q_cmh", r_pump_total_param * 1.3)
            _txt_sistema = f"de 2 bombas em paralelo ({vazao_total_m3h:.1f} m³/h = {r_pump_total_param:.1f} cm/h)" if tem_bomba2 else f"da bomba instalada ({vazao_bomba_m3h:.1f} m³/h = {r_pump_param:.1f} cm/h)"
            st.error(
                f"❌ REPROVADO — O sistema está subdimensionado para a norma NBR 10844.\n\n"
                f"A capacidade do sistema {_txt_sistema} "
                f"NÃO CONSEGUE remover água na mesma velocidade que ela entra durante "
                f"uma chuva de Tr = {_TR_NORMA} anos. O sistema está com **{_def:.1f}%** menos "
                f"capacidade do que o necessário segundo a norma. "
                + (
                    f"O sistema atual só é suficiente para chuvas de até Tr = **{_tr_max_ok} anos** "
                    f"(probabilidade de {100//_tr_max_ok}% ao ano). "
                    if _tr_max_ok else ""
                )
                + f"Para cumprir a norma, seria necessária uma capacidade total de pelo menos **{_q_needed:.1f} m³/h**."
            )
            # Explicar o que acontece na pratica
            _excesso_norma = _q_norma_cmh - r_pump_total_param
            if _excesso_norma > 0 and dist_borda_cm > 0:
                _t_buf = dist_borda_cm / _excesso_norma * 60
                st.info(
                    f"📌 O que acontece durante uma chuva de Tr = {_TR_NORMA} anos:\n\n"
                    f"A água entra no poço a **{_q_norma_cmh:.1f} cm/h** mas as bombas só conseguem "
                    f"remover **{r_pump_total_param:.1f} cm/h**. A diferença de **{_excesso_norma:.1f} cm/h** "
                    f"faz o nível subir mesmo com a bomba ligada. Com os **{dist_borda_cm:.0f} cm** de "
                    f"buffer disponível, o poço levaria cerca de **{_t_buf:.0f} minutos** para transbordar "
                    f"(assumindo chuva contínua na intensidade máxima — o que raramente ocorre na prática, mas é teoricamente possível e por isso a norma exige esse cálculo)."
                )

        # Recomendações
        st.markdown("#### Recomendações")
        _recs = []
        if bomba_ok is False:
            _q_min = vered.get("q_cmh", r_pump_total_param * 1.3) / fator_m3h_para_cmh
            if not tem_bomba2:
                _recs.append(
                    f"**Instalar 2ª bomba em paralelo:** a capacidade necessária para a NBR 10844 (Tr 25a) "
                    f"é de **{_q_min:.1f} m³/h**. Adicionar uma 2ª bomba idêntica de {vazao_bomba_m3h:.1f} m³/h "
                    f"em paralelo eleva a capacidade total para {vazao_bomba_m3h*2:.1f} m³/h."
                )
            else:
                _recs.append(
                    f"**Aumentar capacidade dos conjuntos motobomba:** mesmo com 2 bombas em paralelo "
                    f"({vazao_total_m3h:.1f} m³/h), a NBR 10844 exige **{_q_min:.1f} m³/h**. "
                    f"Recomenda-se substituir os conjuntos por bombas de maior vazão unitária."
                )
        if dist_borda_cm < 50:
            _recs.append(
                f"**Ampliar a margem de segurança física:** a distância atual do sensor à borda é de apenas "
                f"{dist_borda_cm:.0f} cm. Aumentar para pelo menos 80 cm (rebaixando o sensor ou elevando "
                f"a borda do poço) dá mais tempo para o sistema agir antes de transbordar — especialmente "
                f"útil durante picos de chuva quando a bomba não dá conta."
            )
        if era5_correction > 1.5:
            _recs.append(
                f"**Instalar pluviômetro local:** o fator de correção ERA5 de ×{era5_correction:.2f} indica "
                f"que os dados de satélite subestimam significativamente a chuva neste local. "
                f"Um pluviômetro próprio forneceria dados muito mais precisos para calibração "
                f"do sistema e do fator de amplificação."
            )
        _recs.append(
            "**Prever grupo gerador ou nobreak:** eventos de chuva forte frequentemente causam "
            "queda de energia elétrica. Sem energia, a bomba para e o poço pode transbordar muito "
            "mais rápido. Um no-break ou gerador garante operação contínua nos momentos mais críticos."
        )
        _recs.append(
            "**Manutenção semestral obrigatória:** ao longo do tempo, a vazão real da bomba "
            "diminui por desgaste, incrustações e sedimentos. É essencial medir a vazão real "
            "semestralmente, limpar as grades de entrada e verificar as bóias. Uma bomba envelhecida "
            "pode estar entregando apenas 70% da vazão nominal na prática."
        )
        for _r in _recs:
            st.markdown(f"- {_r}")

    # -- DOWNLOAD & IMPRESSÃO ----------------------------------------------------------------------
    st.markdown("---")
    col_dl1, col_dl2 = st.columns(2)
    with col_dl1:
        _rpt_txt = (
            f"RELATORIO DE ADEQUACAO - POCO DE DRENAGEM\n"
            f"Data: {_now_br.strftime('%d/%m/%Y %H:%M')} (Horario de Brasilia)\n"
            f"Local: lat {latitude:.4f}, lon {longitude:.4f}\n\n"
            f"SISTEMA INSTALADO\n"
            f"  Bomba: {vazao_bomba_m3h:.1f} m3/h = {r_pump_param:.1f} cm/h\n"
            f"  Secao do poco: {area_poco_m2:.2f} m2\n"
            f"  Area de drenagem estimada: {area_drenagem_m2:.0f} m2\n"
            f"  Capacidade total: {capacidade_total_cm:.0f} cm\n\n"
            f"CRITERIO DE PROJETO\n"
            f"  Norma: ABNT NBR 10844:1989 | Tr = {_TR_NORMA} anos | Duracao ref.: 1h\n\n"
            f"RESULTADO\n"
            f"  {'APROVADO' if bomba_ok else 'REPROVADO'}\n"
            f"  Margem Tr 25a: {vered.get('margem_pct', 'N/A')}%\n"
        )
        st.download_button(
            "⬇️ Baixar Resumo do Relatório (.txt)",
            data=_rpt_txt.encode("utf-8"),
            file_name=f"relatorio_poco_{dt.date.today().isoformat()}.txt",
            mime="text/plain",
        )

    with col_dl2:
        with st.expander("Visualizar documento para impressão / PDF", icon=":material/print:", expanded=False):
            _txt_bom = f"{vazao_total_m3h:.1f} m³/h ({'2 bombas em paralelo' if tem_bomba2 else '1 bomba isolada'})"
            _bg_card = "#e8f5e9" if bomba_ok else "#ffebee"
            _bd_card = "#2e7d32" if bomba_ok else "#c62828"
            _status_txt = "SISTEMA APROVADO (NBR 10844)" if bomba_ok else "SISTEMA REPROVADO (SUBDIMENSIONADO)"
            _expl_txt = (
                "O sistema instalado cumpre integralmente as exigências de remoção de vazão para o período de retorno Tr = 25 anos exigido pela NBR 10844."
                if bomba_ok else
                f"A capacidade instalada ({r_pump_total_param:.1f} cm/h) não atinge a taxa de afluxo requerida pela NBR 10844 (Tr 25a: {vered.get('q_cmh', 0):.1f} cm/h)."
            )
            st.markdown(
                f"""
                <div style="background-color: white; padding: 20px; border: 1px solid #ccc; border-radius: 8px; color: #222;">
                    <div style="text-align: center; border-bottom: 2px solid #0056b3; padding-bottom: 8px; margin-bottom: 15px;">
                        <h3 style="margin: 0; color: #0056b3;">PARECER TÉCNICO DE ADEQUAÇÃO HIDRÁULICA</h3>
                        <p style="margin: 3px 0 0 0; color: #555; font-size: 13px;">CONDOMÍNIO RESIDENCIAL — POÇO DE DRENAGEM PLUVIAL</p>
                        <p style="margin: 2px 0 0 0; color: #777; font-size: 11px;">Emissão: {_now_br.strftime('%d/%m/%Y às %H:%M')} (Horário de Brasília)</p>
                    </div>
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 15px; font-size: 13px;">
                        <tr style="background-color: #f8f9fa;">
                            <td style="padding: 6px; border: 1px solid #ddd;"><b>Localização:</b> Lat {latitude:.4f}, Lon {longitude:.4f}</td>
                            <td style="padding: 6px; border: 1px solid #ddd;"><b>Norma Técnica:</b> ABNT NBR 10844 (Tr = 25 anos)</td>
                        </tr>
                        <tr>
                            <td style="padding: 6px; border: 1px solid #ddd;"><b>Capacidade Instalada:</b> {_txt_bom}</td>
                            <td style="padding: 6px; border: 1px solid #ddd;"><b>Vazão de Esvaziamento:</b> {r_pump_total_param:.1f} cm/h</td>
                        </tr>
                    </table>
                    <div style="background-color: {_bg_card}; border-left: 4px solid {_bd_card}; padding: 12px; margin-bottom: 15px;">
                        <h4 style="margin: 0 0 4px 0; color: {_bd_card};">{_status_txt}</h4>
                        <p style="margin: 0; font-size: 13px;">{_expl_txt}</p>
                    </div>
                    <p style="font-size: 11px; color: #888; text-align: center; margin: 10px 0 0 0;">
                        Documento emitido pelo Sistema de Monitoramento e Análise Hidráulica de Poços de Drenagem.
                    </p>
                </div>
                """,
                unsafe_allow_html=True
            )
