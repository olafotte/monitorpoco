"""
Módulo de cálculo da Distribuição de Gumbel e Curvas IDF.
"""
import numpy as np


def fit_gumbel(annual_maxima: np.ndarray):
    """Ajusta distribuição de Gumbel pelo método dos momentos.
    Retorna (alpha, u) – parâmetros de escala e localização."""
    mu = np.mean(annual_maxima)
    sigma = np.std(annual_maxima, ddof=1)
    alpha = sigma * np.sqrt(6) / np.pi
    u = mu - 0.5772 * alpha
    return alpha, u


def gumbel_quantile(alpha: float, u: float, tr: float) -> float:
    """Calcula o quantil de probabilidade da distribuição de Gumbel para um Período de Retorno Tr.
    y_p = -ln(-ln(1 - 1/Tr))
    x_p = u + alpha * y_p
    """
    p = 1.0 - 1.0 / tr
    y_p = -np.log(-np.log(p))
    return u + alpha * y_p
