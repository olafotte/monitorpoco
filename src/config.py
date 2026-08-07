"""
Configurações globais e constantes físicas do sistema de monitoramento de poço.
"""

FUNDODOPOCO = 210.0  # cm, profundidade total do poço a partir do topo (borda superior) 

# Coordenadas geográficas padrão (Blumenau / SC)
DEFAULT_LATITUDE = -26.9166
DEFAULT_LONGITUDE = -49.0717

# Níveis operacionais padrão da bomba (cm a partir do topo do poço)
DEFAULT_D_ON = 151.0     # cm abaixo do topo (Bomba 1 Liga)
DEFAULT_D_OFF = 173.0    # cm abaixo do topo (Bomba 1 Desliga)
DEFAULT_DIST_BORDA = 65.0 # cm profundidade padrão do sensor no poço

DEFAULT_D_ON2 = 123.0     # cm abaixo do topo (Bomba 2 Liga)
DEFAULT_D_OFF2 = 145.0    # cm abaixo do topo (Bomba 2 Desliga)

# Calibração do modelo climático ERA5
RECOMMENDED_ERA5_FACTOR = 2.60
