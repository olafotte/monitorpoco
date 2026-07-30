# 🌊 Monitoramento de Poço de Drenagem e Previsão Meteorológica

Uma aplicação web desenvolvida em **Streamlit** para monitoramento em tempo real de nível de poço de drenagem, análise estatística de pluviometria histórica (curvas IDF / Gumbel), calibração geométrica de reservatórios e emissão automatizada de **Relatório de Adequação Hidráulica** em conformidade com a norma **ABNT NBR 10844**.

---

## 📌 Principais Funcionalidades

O aplicativo está estruturado em 9 abas principais:

1. **📊 Nível & Simulação em Tempo Real**
   - Leitura contínua dos dados de telemetria do sensor de nível (armazenados no banco de dados Turso).
   - Visualização gráfica de nível d'água, volume acumulado e status operacional da bomba.
   - Simulação da dinâmica de acionamento (nível LIGA/DESLIGA) e taxa de esvaziamento.

2. **📈 Estatísticas de Nível**
   - Análise estatística descritiva (médias, mínimos, máximos e distribuição de frequência) das medições do sensor.

3. **🌤️ Previsão Meteorológica (Open-Meteo)**
   - Integração com a API da Open-Meteo para previsão de chuva acumulada e intensidade pluviométrica na região de Blumenau/SC.

4. **🚨 Defesa Civil de Blumenau**
   - Web scraping e consulta com cache local aos dados oficiais das estações pluviométricas e de nível de rios da Defesa Civil de Blumenau.

5. **🌧️ Histórico Pluviométrico & Curvas IDF**
   - Ingestão de dados históricos climáticos (ERA5 / Open-Meteo Archive API) com janela ajustável de anos (ex: 5 a 10+ anos).
   - Ajuste da **Distribuição de Extremos de Gumbel** para cálculo de curvas IDF (Intensidade-Duração-Frequência) para diversas durações (1h a 24h) e Períodos de Retorno ($Tr$ de 2 a 100 anos).
   - Aplicação de fator de correção calibrado (ERA5 vs. Defesa Civil).

6. **🔮 Previsão de Nível por Chuva**
   - Previsão da resposta do nível d'água no poço em função dos volumes de chuva previstos, considerando o fator de amplificação da área de drenagem.

7. **🗺️ Análise Espacial & Regiões**
   - Comparativo pluviométrico por bairros e regiões do município.

8. **📐 Diagrama Esquemático do Poço**
   - Representação visual interativa em escala real do poço utilizando **Plotly**.
   - Exibição da geometria completa: borda superior do poço, margem de segurança (buffer), posição do sensor, níveis de acionamento da bomba (LIGA/DESLIGA) e fundo do poço.

9. **📋 Relatório de Adequação Hidráulica (NBR 10844)**
   - Avaliação automatizada da capacidade da bomba instalada versus a demanda requerida para chuva de projeto de $Tr = 25$ anos (NBR 10844).
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
