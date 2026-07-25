import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

# Page Config
st.set_page_config(
    page_title="Monitor de Nível de Poço & Modelo Matemático",
    page_icon="🌊",
    layout="wide"
)

st.title("🌊 Monitoramento e Modelo Matemático do Poço de Drenagem")
st.markdown("""
Este aplicativo analisa os dados de nível do poço de drenagem, identifica os regimes operacionais
(lençol freático, chuva e bombeamento), preenche lacunas (*gaps*) utilizando um modelo físico diferencial,
e simula o risco de transbordamento baseado na pluviometria em mm.
""")

# Sidebar settings
st.sidebar.header("⚙️ Parâmetros do Modelo (Calibrados)")

# Parameters calibrated on clean dataset
d_on = st.sidebar.number_input("Nível Ativação Bomba (cm)", value=73.0, step=0.5, help="Distância quando a bomba liga (água alta)")
d_off = st.sidebar.number_input("Nível Desativação Bomba (cm)", value=90.0, step=0.5, help="Distância quando a bomba desliga (água baixa)")
d_overflow = st.sidebar.number_input("Nível Crítico de Transbordo (cm)", value=65.0, step=0.5, help="Distância crítica do sensor onde o poço transborda")
r_gnd_param = st.sidebar.number_input("Taxa Lençol Freático (cm/h)", value=1.44, step=0.1)
r_pump_param = st.sidebar.number_input("Vazão de Esvaziamento Bomba (cm/h)", value=76.6, step=1.0, help="Capacidade nominal da bomba (14 m³/h)")
factor_mm_cm = st.sidebar.number_input("Fator de Amplificação (cm de poço / mm de chuva)", value=2.83, step=0.1, help="Relação calibrada na chuva de 22/07 (54mm -> 153cm no poço)")

# File uploader
uploaded_file = st.sidebar.file_uploader("Upload do CSV de leituras (ex: leituras_poco_2.csv)", type=["csv"])

@st.cache_data
def load_and_preprocess(file_source):
    if file_source is not None:
        df = pd.read_csv(file_source)
    else:
        df = pd.read_csv('leituras_poco.csv')
        
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['dt_round'] = df['timestamp'].dt.round('5min')
    df_dedup = df.drop_duplicates(subset=['dt_round']).sort_values('dt_round').copy()
    
    # Grid regular de 5 min
    full_idx = pd.date_range(start=df_dedup['dt_round'].min(), end=df_dedup['dt_round'].max(), freq='5min')
    df_grid = pd.DataFrame({'dt_round': full_idx})
    df_m = pd.merge(df_grid, df_dedup, on='dt_round', how='left')
    return df_m

try:
    df_raw = load_and_preprocess(uploaded_file)
except Exception as e:
    st.error(f"Erro ao carregar os dados: {e}")
    st.stop()

# Helper for simulation gap fill
def simulate_gap(start_val, end_val, steps, dt_min=5.0, r_in_cm_min=1.44/60.0, r_pump_cm_min=76.6/60.0, d_trigger_on=73.0, d_trigger_off=90.0):
    vals = np.zeros(steps)
    vals[0] = start_val
    state = 0 if start_val > d_trigger_on else 1
    curr = start_val
    for i in range(1, steps - 1):
        if state == 0:
            curr -= r_in_cm_min * dt_min
            if curr <= d_trigger_on:
                state = 1
        else:
            curr += (r_pump_cm_min - r_in_cm_min) * dt_min
            if curr >= d_trigger_off:
                state = 0
        vals[i] = curr
    vals[-1] = end_val
    return vals

# Apply Imputation
df_processed = df_raw.copy()
df_processed['nivel_imputed'] = df_processed['nivel_cm'].copy()

# Linear for short gaps, physical simulation for long gaps
gap_starts = df_processed[df_processed['nivel_cm'].isna() & ~df_processed['nivel_cm'].shift(1).isna()].index

for g_start in gap_starts:
    g_ends = df_processed.loc[g_start:]['nivel_cm'].dropna().index
    if len(g_ends) == 0:
        continue
    g_end = g_ends[0]
    gap_len = g_end - g_start + 1
    
    v_start = df_processed.loc[g_start - 1, 'nivel_cm']
    v_end = df_processed.loc[g_end, 'nivel_cm']
    
    if gap_len <= 4:
        lin_vals = np.linspace(v_start, v_end, gap_len + 1)[1:-1]
        df_processed.loc[g_start:g_end-1, 'nivel_imputed'] = lin_vals
    else:
        sim_vals = simulate_gap(v_start, v_end, gap_len + 1, 
                                r_in_cm_min=r_gnd_param/60.0, 
                                r_pump_cm_min=r_pump_param/60.0,
                                d_trigger_on=d_on, d_trigger_off=d_off)
        df_processed.loc[g_start:g_end-1, 'nivel_imputed'] = sim_vals[1:-1]

# Display KPIs
col1, col2, col3, col4 = st.columns(4)
total_obs = len(df_processed)
missing_obs = df_processed['nivel_cm'].isna().sum()
missing_pct = (missing_obs / total_obs) * 100

col1.metric("Total de Leituras Esperadas", f"{total_obs:,}")
col2.metric("Leituras Validadas", f"{total_obs - missing_obs:,}")
col3.metric("Dados Preenchidos (Gaps)", f"{missing_obs:,} ({missing_pct:.1f}%)")
col4.metric("Faixa de Operação", f"{d_on:.1f} cm - {d_off:.1f} cm")

# Tabs
tab1, tab2, tab3 = st.tabs(["📊 Visualização e Imputação", "📐 Modelo Matemático", "🌧️ Simulador Pluviométrico (mm)"])

with tab1:
    st.subheader("Série Temporal do Nível do Poço")
    st.caption("Nota: O eixo Y é invertido para refletir a altura real da água no poço (valores menores de distância = nível de água mais alto).")
    
    fig = go.Figure()
    # Raw Data
    fig.add_trace(go.Scatter(
        x=df_processed['dt_round'], y=df_processed['nivel_cm'],
        mode='markers', name='Dados Reais (Sensor)',
        marker=dict(size=4, color='#1f77b4')
    ))
    # Imputed
    fig.add_trace(go.Scatter(
        x=df_processed['dt_round'], y=df_processed['nivel_imputed'],
        mode='lines', name='Modelo Estimado (Sem Gaps)',
        line=dict(color='#ff7f0e', width=1.5)
    ))
    # Limits
    fig.add_hline(y=d_on, line_dash="dash", line_color="red", annotation_text="Bomba Liga (Água Alta)")
    fig.add_hline(y=d_off, line_dash="dash", line_color="green", annotation_text="Bomba Desliga (Água Baixa)")
    fig.add_hline(y=d_overflow, line_dash="dot", line_color="black", annotation_text="Nível Crítico / Transbordo")
    
    fig.update_layout(
        yaxis=dict(autorange="reversed", title="Distância até o Sensor (cm)"),
        xaxis=dict(title="Data / Hora"),
        height=500,
        margin=dict(l=20, r=20, t=30, b=20)
    )
    st.plotly_chart(fig, use_container_width=True)

with tab2:
    st.header("Formulação do Modelo Matemático")
    st.markdown("O comportamento hidrodinâmico do poço de drenagem é regido pela **Equação de Balanço de Massa / Continuidade**:")
    st.latex(r"\frac{dV}{dt} = A \cdot \frac{dh}{dt} = Q_{\text{entrada}}(t) - Q_{\text{saída}}(t)")
    st.markdown(r"Como o sensor mede a distância $d(t)$ no topo em relação à lâmina d'água ($h(t) = H_{\text{sensor}} - d(t)$), temos $\frac{dh}{dt} = -\frac{dd}{dt}$.")
    st.markdown("Portanto, a taxa instantânea de variação da distância $d(t)$ é dada por:")
    st.latex(r"\frac{dd(t)}{dt} = \frac{Q_{\text{saída}}(t) - Q_{\text{entrada}}(t)}{A}")
    st.subheader("Regimes Operacionais Recalibrados")
    st.markdown("**1️⃣ Entrada Lenta (Lençol Freático – Estado $S_0$)**")
    st.markdown("- $Q_{\text{saída}} = 0$ (Bomba desligada)")
    st.latex(r"\frac{dd}{dt} = -r_{\text{freático}} \approx -1.44\text{ cm/h}")
    st.markdown("**2️⃣ Entrada por Chuva ($S_1$)**")
    st.markdown("- Relacionada com a precipitação em mm pela taxa de amplificação do pátio:")
    st.latex(r"\text{Taxa de Entrada (cm/h)} = \frac{\text{Chuva (mm)}}{\text{Horas}} \times 2.83")
    st.markdown("**3️⃣ Esvaziamento Forçado (Bomba Ativa – Estado $S_2$)**")
    st.latex(r"Q_{\text{saída}} = 14.000\text{ L/h} (14\text{ m}^3/h) \Rightarrow r_{\text{bomba}} \approx 76.6\text{ cm/h}")

with tab3:
    st.subheader("🔮 Simulador de Eventos Pluviométricos e Saturação da Bomba")
    st.write("Insira a estimativa de volume de chuva (mm) e a duração prevista (horas) para avaliar se a bomba suportará o volume ou se haverá transbordamento.")
    
    col_input1, col_input2, col_input3 = st.columns(3)
    with col_input1:
        chuva_mm = st.number_input("Precipitação Prevista (mm)", min_value=1.0, max_value=300.0, value=60.0, step=5.0)
    with col_input2:
        chuva_horas = st.number_input("Duração da Chuva (Horas)", min_value=0.5, max_value=48.0, value=2.0, step=0.5)
    with col_input3:
        bomba_status = st.selectbox("Status da Automação da Bomba", ["Operacional (Ligada)", "Falha / Sem Energia (Desligada)"])
        
    # Calculated metrics
    intensidade_mm_h = chuva_mm / chuva_horas
    taxa_entrada_cm_h = intensidade_mm_h * factor_mm_cm
    
    # Critical intensity to saturate pump
    intensidade_saturacao_mm_h = r_pump_param / factor_mm_cm
    
    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("Intensidade da Chuva", f"{intensidade_mm_h:.1f} mm/h")
    col_m2.metric("Entrada Equivalente no Poço", f"{taxa_entrada_cm_h:.1f} cm/h")
    col_m3.metric("Capacidade da Bomba", f"{r_pump_param:.1f} cm/h")
    col_m4.metric("Limite de Saturação da Bomba", f"{intensidade_saturacao_mm_h:.1f} mm/h")
    
    st.markdown("---")
    
    # Dynamic Simulation over time
    sim_time_steps = np.linspace(0, max(chuva_horas * 1.5, 6.0), 360)  # mins to hours
    dt_h = sim_time_steps[1] - sim_time_steps[0]

    levels = []
    curr_d = d_off  # Start at bottom level (dry state)
    p_state = False
    overflow_occurred = False
    overflow_time = None

    for t in sim_time_steps:
        # Determine rain inflow rate at time t
        if t <= chuva_horas:
            r_in = taxa_entrada_cm_h
        else:
            r_in = 0.0
        # Pump operation logic
        if bomba_status == "Falha / Sem Energia (Desligada)":
            curr_d -= r_in * dt_h
        else:
            if p_state:
                # Pump active: decrease level
                curr_d -= (r_pump_param - r_in) * dt_h
                if curr_d <= d_on:
                    p_state = False  # pump turns off when level reaches activation threshold
            else:
                # Pump inactive: level rises due to inflow
                curr_d += r_in * dt_h
                if curr_d >= d_off:
                    p_state = True   # pump turns on when level reaches deactivation threshold
        # Record level and check overflow
        levels.append(curr_d)
        if not overflow_occurred and curr_d <= d_overflow:
            overflow_occurred = True
            overflow_time = t

    # Plot water level over simulation time
    fig_sim = go.Figure()
    fig_sim.update_layout(
        title=f"Simulação Dinâmica para Chuva de {chuva_mm}mm em {chuva_horas}h ({intensidade_mm_h:.1f} mm/h)",
        xaxis_title="Tempo Decorrido (Horas)",
        yaxis=dict(autorange="reversed", title="Distância até o Sensor (cm)"),
        height=450
    )
    st.plotly_chart(fig_sim, use_container_width=True)
    
    # Theoretical Analysis Box
    st.subheader("📢 Análise de Capacidade e Risco")
    
    if intensidade_mm_h > intensidade_saturacao_mm_h and bomba_status == "Operacional (Ligada)":
        st.warning(f"⚠️ **SATURAÇÃO DA BOMBA DETECTADA:** A intensidade da chuva ({intensidade_mm_h:.1f} mm/h) supera a capacidade máxima de esvaziamento da bomba ({intensidade_saturacao_mm_h:.1f} mm/h). Mesmo com a bomba ligada 100% do tempo, o poço acumulará água.")
    elif bomba_status == "Operacional (Ligada)":
        st.success(f"✅ **BOMBA DÁ CONTA DA CHUVA:** A intensidade ({intensidade_mm_h:.1f} mm/h) é inferior ao limite de saturação ({intensidade_saturacao_mm_h:.1f} mm/h). A bomba conseguirá ciclar e esvaziar o poço.")
        
    if overflow_occurred:
        st.error(f"🚨 **ALERTA DE TRANSBORDAMENTO:** O poço atingirá o nível crítico de transbordo ({d_overflow} cm) em **{overflow_time*60:.0f} minutos** ({overflow_time:.2f} horas) após o início da chuva!")
    else:
        st.info("ℹ️ Nenhum transbordamento previsto para este cenário especifico.")
