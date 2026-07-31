# 🌊 Monitoramento de Poço de Drenagem e Previsão Meteorológica

## 🏗️ Estrutura do Projeto

```text
Monitor_poco/
├── streamlit_app.py             # Aplicativo principal e orquestrador Streamlit
├── src/                         # Pacote modular do sistema
│   ├── config.py                # Constantes físicas e parâmetros padrão
│   ├── data/
│   │   └── adolfo_konder.py     # Loader e cache dinâmico da Estação Ponte Adolfo Konder
│   └── models/
│       └── gumbel.py            # Distribuição de Gumbel e Curvas IDF
├── Ponte Adolfo Konder_pluv_*.txt # Dados históricos de 15 minutos da estação física
├── README.md                    # Documentação do projeto
└── requirements.txt             # Dependências Python
```

Uma aplicação web desenvolvida em **Streamlit** para monitoramento em tempo real de nível de poço de drenagem, análise estatística de pluviometria histórica (curvas IDF / Gumbel), calibração geométrica de reservatórios e emissão automatizada de **Relatório de Adequação Hidráulica** em conformidade com a norma **ABNT NBR 10844**.

---

## 📌 Principais Funcionalidades

O aplicativo está estruturado em 9 abas principais:

1. **📊 Nível & Simulação em Tempo Real**
   - Leitura contínua dos dados de telemetria do sensor de nível (armazenados no banco de dados Turso).
   - Visualização gráfica de nível d'água, volume acumulado e status operacional da bomba.
   - Simulação da dinâmica de acionamento (nível LIGA/DESLIGA) e taxa de esvaziamento.

2. **📐 Modelo Matemático**
   - Formulação do balanço de massa/continuidade hidráulica do poço.
   - **Descrição do Fator de Conversão ($\text{m}^3/\text{h} \rightarrow \text{cm}/\text{h}$):** Explicação detalhada da geometria teórica do poço ($D = 1,20\text{ m}$), efeito de remanso e volume em rede (*pipe storage*, área efetiva de $18,28\text{ m}^2$) e metodologia empírica de calibração pelos ciclos de desce/subida medidos pelo sensor ultrassônico.
   - **📈 Comparativo no Momento LIGA $\rightarrow$ DESLIGA:** Alinhamento preciso do instante exato de atracamento da bomba ($d \le d_{\text{on}}$, $t = 0\text{ min}$) para comparar a rampa de esvaziamento teórica com a curva real gravada pelo sensor ultrassônico, exibindo métricas de taxa real medida ($\text{cm/h}$) e tempo decorrido.

3. **🌧️ Simulador Pluviométrico (Sintético & Reconstrução Histórica)**
   - **Simulação Sintética:** Avaliação dinâmica de volumes hipotéticos de chuva ($mm$) e duração ($h$), calculando taxas de enchimento, atuação da bomba e instante exato de transbordamento.
   - **⏮️ Reconstrução de Eventos Históricos Passados:** Emulação e reconstituição de tempestades reais passadas (ex: 12 a 14 de Fevereiro de 2025 ou qualquer data desde 2015).
   - **Escolha da Fonte de Dados:** Alterna livremente entre **medições reais de 15 minutos da Estação Pluviométrica Ponte Adolfo Konder**, o **Modelo ERA5 Corrigido** ou o **Modelo ERA5 Bruto**.
   - Simulação contínua passo a passo (5 min) do nível d'água no poço e acionamento por bóias (1ª bomba e 2ª bomba em paralelo se ativada).

4. **🛑 Defesa Civil de Blumenau & Estação Ponte Adolfo Konder**
   - Web scraping aos dados oficiais de chuva e nível de rios da Defesa Civil de Blumenau.
   - **Seção Exclusiva Gumbel — Estação Ponte Adolfo Konder (2021–2025):** Análise de extremos com ajuste da Distribuição de Gumbel sobre as séries de 15min a 24h e curvas IDF locais.
   - **Rankings Top 10 por Duração Específica:** Abas dedicadas para os 10 maiores eventos históricos de cada duração (**15min, 30min, 1h, 2h, 4h, 12h e 24h**), com desduplicação de tempestades, acumulado ($\text{mm}$), intensidade ($\text{mm/h}$), afluxo no poço ($\text{cm/h}$) e status da bomba.

5. **📊 Histórico Pluviométrico & Curvas IDF (Análise de Extremos)**
   - Série histórica de precipitação horária (5 a 10 anos) via Open-Meteo Archive API.
   - **Calibração com Estação Real Ponte Adolfo Konder (2021-2025):** Tabela comparativa dos picos reais vs. ERA5 bruto e seleção de base de cálculo para as curvas IDF (ERA5 Corregido vs. Ponte Adolfo Konder).
   - Cálculo automatizado de curvas IDF (Intensidade-Duração-Frequência) usando a distribuição de Gumbel (método dos momentos).
   - Identificação de eventos saturantes e chuva de projeto para Período de Retorno $Tr = 25$ anos.

6. **🔮 Previsão de Nível por Chuva**
   - Simulação hidrológica que converte a previsão meteorológica em elevação do nível d'água, considerando a área de contribuição do poço, perdas de carga e a eficiência de bombeamento, emitindo alertas de risco de transbordo (buffer).

7. **🗺️ Análise Espacial & Regiões**
   - Comparativo pluviométrico por bairros e regiões do município.

8. **📐 Diagrama Esquemático do Poço**
   - Representação visual interativa em escala real do poço utilizando **Plotly**.
   - Exibição da geometria completa: borda superior do poço, margem de segurança (buffer), posição do sensor, níveis de acionamento da bomba (LIGA/DESLIGA) e fundo do poço.

9. **📋 Relatório de Adequação Hidráulica (NBR 10844)**
   - Avaliação automatizada da capacidade da bomba instalada versus a demanda requerida para chuva de projeto de $Tr = 25$ anos (NBR 10844).
   - **Fonte Pluviométrica Primária:** Curvas IDF de Gumbel calculadas diretamente sobre as medições reais de 15 minutos da **Estação Pluviométrica Ponte Adolfo Konder (Blumenau)**.
   - Diagnóstico automático para cenários de dados insuficientes.
   - Cálculo de margem de segurança física (tempo até transbordo no buffer acima do sensor).
   - Linguagem acessível e explicativa para leigos e parecer técnico final com recomendações de engenharia.
   - Opção de download do resumo executivo em formato texto (`.txt`).
---

## ⚙️ Parâmetros Configuráveis (Barra Lateral)

- **Capacidade da Bomba ($m^3/h$):** Vazão nominal da bomba instalada.
- **Nível Ativação / Desativação (cm):** Leitura do sensor para acionamento LIGA/DESLIGA.
- **Distância Sensor → Borda Superior (cm):** Distância física do sensor até o topo do poço (reserva de buffer).
- **Área do Poço ($m^2$):** Seção transversal do poço de drenagem.
- **Fator de Amplificação Poço/Chuva ($cm/mm$):** Relação entre mm de chuva e elevação do nível no poço.
- **Fator de Correção ERA5:** Multiplicador para ajuste da precipitação histórica de satélite em relação às estações locais.

---

## 🛠️ Tecnologias Utilizadas

- **Frontend / UI:** [Streamlit](https://streamlit.io/)
- **Visualização de Dados:** [Plotly](https://plotly.com/python/)
- **Processamento & Estatística:** `pandas`, `numpy`, `scipy.stats` (Distribuição de Gumbel)
- **Banco de Dados:** [Turso Database](https://turso.tech/) (HTTP REST API / `libSQL`)
- **Dados Pluviométricos & Clima:** Open-Meteo Archive & Forecast APIs
- **Web Scraping:** `requests`, `beautifulsoup4` (Defesa Civil Blumenau)

---

## 🚀 Como Executar Localmente

### Pré-requisitos

Certifique-se de ter o Python 3.10+ e o gerenciador de pacotes [`uv`](https://github.com/astral-sh/uv) instalados.

```bash
# Instalar o uv (Linux/macOS)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Ou no Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 1. Clonar o repositório e sincronizar dependências

```bash
git clone <url-do-repositorio>
cd Monitor_poco
uv sync
```

### 2. Configurar as credenciais do Turso

Crie o arquivo `.streamlit/secrets.toml` no diretório raiz do projeto com suas credenciais do Turso DB:

```toml
[turso]
url = "https://<seu-turso-url>/v1/execute"
token = "<seu-turso-token>"
```

*(Nota: O arquivo `.streamlit/secrets.toml` já se encontra ignorado no `.gitignore`)*

Alternativamente, é possível definir as variáveis de ambiente:

```bash
export TURSO_URL="https://<seu-turso-url>/v1/execute"
export TURSO_TOKEN="<seu-turso-token>"
```

### 3. Executar a aplicação

```bash
uv run streamlit run streamlit_app.py
```

Acesse a aplicação no navegador através do endereço local exibido no terminal (geralmente `http://localhost:8501`).

---

## 📜 Licença

Projeto desenvolvido para monitoramento e análise de drenagem predial e urbana.
