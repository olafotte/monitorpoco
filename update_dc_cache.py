"""Gera/atualiza dc_rankings_cache.json com dados atuais da Defesa Civil de Blumenau e telemetria ao vivo da Ponte Adolfo Konder."""
import datetime
import json
import re
import warnings
import requests

try:
    from bs4 import BeautifulSoup
except ImportError:
    raise SystemExit("Instale beautifulsoup4: pip install beautifulsoup4")

from urllib3.exceptions import InsecureRequestWarning

warnings.filterwarnings("ignore", category=InsecureRequestWarning)

url = "https://defesacivil.blumenau.sc.gov.br/d/maiores-chuvas"
headers_req = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

resp = requests.get(url, headers=headers_req, timeout=30, verify=False)
resp.raise_for_status()
soup = BeautifulSoup(resp.text, "html.parser")

resultado = {}
page_headers = soup.find_all("div", class_="page-header")
for ph in page_headers:
    h3 = ph.find("h3")
    if not h3:
        continue
    titulo_raw = h3.get_text(separator=" ", strip=True)
    match = re.search(r"em\s+([\d]+h|[\d]+min)", titulo_raw, re.IGNORECASE)
    duracao = match.group(1) if match else titulo_raw

    small = h3.find("small")
    ultima_atualizacao = (
        small.get_text(strip=True).replace("Última atualização:", "").strip()
        if small else ""
    )

    table = ph.find_next_sibling("table")
    if not table:
        continue
    rows_data = []
    tbody = table.find("tbody")
    rows = tbody.find_all("tr") if tbody else table.find_all("tr")
    for tr in rows:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) == 4:
            acumulado_str = cells[3].replace(",", ".")
            try:
                acumulado_float = float(acumulado_str)
            except ValueError:
                acumulado_float = None
            rows_data.append({
                "Estação": cells[0],
                "Região": cells[1],
                "Data/Hora": cells[2],
                "Acumulado_mm": acumulado_float,
                "ultima_atualizacao": ultima_atualizacao,
            })
    if rows_data:
        resultado[duracao] = rows_data

# Buscar dados ao vivo específicos da Ponte Adolfo Konder
live_konder = {}
try:
    from src.data.adolfo_konder import fetch_live_adolfo_konder
    live_konder = fetch_live_adolfo_konder()
except Exception as e:
    print(f"Aviso ao buscar telemetria ao vivo da Ponte Adolfo Konder: {e}")

output = {
    "gerado_em": datetime.datetime.now().isoformat(),
    "fonte": url,
    "dados": resultado,
    "ponte_adolfo_konder_live": live_konder,
}

with open("dc_rankings_cache.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"Cache gerado com {sum(len(v) for v in resultado.values())} registros:")
for dur, rows in resultado.items():
    print(f"  {dur}: {len(rows)} registros | top: {rows[0]['Estação']} {rows[0]['Acumulado_mm']}mm")
if live_konder.get("sucesso"):
    print(f"  Ponte Adolfo Konder (Ao Vivo): Leitura {live_konder.get('hora_leitura')} | Chuva 1h: {live_konder.get('precip_1h_mm')}mm | Nível Rio: {live_konder.get('nivel_rio_m')}m")
print("Salvo em: dc_rankings_cache.json")
