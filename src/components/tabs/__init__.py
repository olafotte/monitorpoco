"""Módulo de componentes de abas da aplicação Streamlit."""

from src.components.tabs.tab_timeseries import render_tab_timeseries
from src.components.tabs.tab_model import render_tab_model
from src.components.tabs.tab_simulator import render_tab_simulator
from src.components.tabs.tab_weather import render_tab_weather
from src.components.tabs.tab_forecast import render_tab_forecast
from src.components.tabs.tab_idf import render_tab_idf
from src.components.tabs.tab_defesa_civil import render_tab_defesa_civil
from src.components.tabs.tab_diagram import render_tab_diagram
from src.components.tabs.tab_report import render_tab_report

__all__ = [
    "render_tab_timeseries",
    "render_tab_model",
    "render_tab_simulator",
    "render_tab_weather",
    "render_tab_forecast",
    "render_tab_idf",
    "render_tab_defesa_civil",
    "render_tab_diagram",
    "render_tab_report",
]
