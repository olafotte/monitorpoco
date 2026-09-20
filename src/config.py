"""
Configurações globais e constantes físicas do sistema de monitoramento de poço.
"""

# Profundidade total do poço a partir do topo (borda superior) em cm
FUNDODOPOCO: float = 250.0

# Coordenadas geográficas padrão (Blumenau / SC)
DEFAULT_LATITUDE: float = -26.9265068
DEFAULT_LONGITUDE: float = -49.0687619

# Níveis operacionais padrão da bomba (cm a partir do topo do poço)
DEFAULT_D_ON: float = 151.0     # cm abaixo do topo (Bomba 1 Liga)
DEFAULT_D_OFF: float = 173.0    # cm abaixo do topo (Bomba 1 Desliga)
DEFAULT_DIST_BORDA: float = 69.0 # cm profundidade padrão do sensor no poço

DEFAULT_D_ON2: float = 123.0     # cm abaixo do topo (Bomba 2 Liga)
DEFAULT_D_OFF2: float = 145.0    # cm abaixo do topo (Bomba 2 Desliga)

# Capacidades nominais padrão das bombas
DEFAULT_VAZAO_BOMBA_M3H: float = 37.00

# Parâmetros hidrológicos e climáticos
DEFAULT_R_GND: float = 1.44       # cm/h taxa de infiltração do lençol freático
DEFAULT_FACTOR_MM_CM: float = 2.83 # Fator amplificação (cm poço / mm chuva)
RECOMMENDED_ERA5_FACTOR: float = 2.60 # Calibração do modelo climático ERA5

# Modelo Hidrológico de Dois Fatores (Dual-Factor Model)
# 1. Regime de Bombeamento Rápido (Poço Cilíndrico + Galerias Imediatas: 55.0 L/cm = 5.50 m²)
DEFAULT_AREA_POCO_M2: float = 5.50           # m² área de seção transversal efetiva durante esvaziamento por bomba
DEFAULT_VOLUME_POR_CM_L: float = 55.0        # Litros por cm de nível no poço durante bombeamento
DEFAULT_FATOR_M3H_PARA_CMH: float = 18.182   # cm/h por m³/h (100 / 5.50)

# 2. Regime de Infiltração Lenta / Chuva (Remanso A Montante + Solo Úmido: 187.5 L/cm = 18.75 m²)
DEFAULT_AREA_INFILTRACAO_M2: float = 18.75    # m² área de infiltração / retenção do pátio em chuva
DEFAULT_VOLUME_INFILTRACAO_L: float = 187.5   # Litros por cm em acúmulo de chuva de longa duração
