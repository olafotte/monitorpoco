"""
Configurações globais e constantes físicas do sistema de monitoramento de poço.
"""

FUNDODOPOCO = 150.0  # cm, distância do sensor até a linha d'água quando o poço está seco

# Coordenadas geográficas padrão (Blumenau / SC)
DEFAULT_LATITUDE = -26.9166
DEFAULT_LONGITUDE = -49.0717

# Níveis operacionais padrão da bomba (cm a partir do sensor)
DEFAULT_D_ON = 73.0
DEFAULT_D_OFF = 90.0
DEFAULT_DIST_BORDA = 33.0

# Calibração do modelo climático ERA5
RECOMMENDED_ERA5_FACTOR = 2.60
