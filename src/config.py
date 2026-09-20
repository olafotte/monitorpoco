"""
Configurações globais e constantes físicas do sistema de monitoramento de poço.
"""

FUNDODOPOCO = 250.0  # cm, profundidade total do poço a partir do topo (borda superior) 

# Coordenadas geográficas padrão (Blumenau / SC)
DEFAULT_LATITUDE = -26.9166
DEFAULT_LONGITUDE = -49.0717

# Níveis operacionais padrão da bomba (cm a partir do topo do poço)
DEFAULT_D_ON = 151.0     # cm abaixo do topo (Bomba 1 Liga)
DEFAULT_D_OFF = 173.0    # cm abaixo do topo (Bomba 1 Desliga)
DEFAULT_DIST_BORDA = 69.0 # cm profundidade padrão do sensor no poço

DEFAULT_D_ON2 = 123.0     # cm abaixo do topo (Bomba 2 Liga)
DEFAULT_D_OFF2 = 145.0    # cm abaixo do topo (Bomba 2 Desliga)

# Calibração do modelo climático ERA5
RECOMMENDED_ERA5_FACTOR = 2.60

# Modelo Hidrológico de Dois Fatores (Dual-Factor Model)
# 1. Regime de Bombeamento Rápido (Poço Cilíndrico + Galerias Imediatas: 55.0 L/cm = 5.50 m²)
DEFAULT_AREA_POCO_M2 = 5.50           # m² área de seção transversal efetiva durante esvaziamento por bomba
DEFAULT_VOLUME_POR_CM_L = 55.0        # Litros por cm de nível no poço durante bombeamento
DEFAULT_FATOR_M3H_PARA_CMH = 18.182   # cm/h por m³/h (100 / 5.50)

# 2. Regime de Infiltração Lenta / Chuva (Remanso A Montante + Solo Úmido: 187.5 L/cm = 18.75 m²)
DEFAULT_AREA_INFILTRACAO_M2 = 18.75    # m² área de infiltração / retenção do pátio em chuva
DEFAULT_VOLUME_INFILTRACAO_L = 187.5   # Litros por cm em acúmulo de chuva de longa duração


