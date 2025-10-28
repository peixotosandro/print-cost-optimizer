# app.py
import streamlit as st
import pandas as pd
import requests
import logging
from datetime import datetime
from typing import List, Dict, Any
import time

# === Configuração da Página ===
st.set_page_config(page_title="Print Cost Optimizer", layout="wide", initial_sidebar_state="expanded")
st.title("Print Cost Optimizer Agent")
st.markdown("**Análise em tempo real com API Lexmark Cloud Fleet Management**")

# === Logging ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Cliente Lexmark CFM ===
class LexmarkCFMClient:
    def __init__(self, client_id: str, client_secret: str, region: str = 'us'):
        self.client_id = client_id
        self.client_secret = client_secret
        self.region = region.lower()
        self.base_url = f"https://apis.{self.region}.iss.lexmark.com/cfm/fleetmgmt-integration-service"
        self.token_url = f"https://idp.{self.region}.iss.lexmark.com/oauth/token"
        self.access_token = None
        self.token_expiry = 0

    def _get_token(self) -> str:
        if time.time() < self.token_expiry - 60:
            return self.access_token

        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }
        try:
            response = requests.post(self.token_url, json=payload)
            response.raise_for_status()
            data = response.json()
            self.access_token = data['access_token']
            self.token_expiry = int(time.time()) + 3500  # ~1h
            return self.access_token
        except Exception as e:
            st.error(f"Erro ao obter token: {e}")
            raise

    def get_all_printers(self) -> List[Dict[str, Any]]:
        token = self._get_token()
        headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
        all_printers = []
        page = 0
        progress = st.progress(0)

        while True:
            params = {'pageNumber': page, 'pageSize': 200}
            try:
                response = requests.get(f"{self.base_url}/v1.0/assets", headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                content = data.get('content', [])
                all_printers.extend(content)
                progress.progress((page + 1) / (page + 2) if content else 1.0)
                if data.get('last', True): break
                page += 1
            except Exception as e:
                st.error(f"Erro na página {page}: {e}")
                break
        progress.empty()
        return all_printers

# === Agente de Análise ===
class PrintCostOptimizerAgent:
    def __init__(self, printers: List[Dict[str, Any]]):
        self.printers = printers
        self.reports = []

    def analyze(self):
        progress_bar = st.progress(0)
        for i, printer in enumerate(self.printers):
            try:
                report = self._analyze_single_printer(printer)
                self.reports.append(report)
            except Exception as e:
                logger.warning(f"Erro ao analisar {printer.get('serialNumber')}: {e}")
            progress_bar.progress((i + 1) / len(self.printers))
        progress_bar.empty()

    def _analyze_single_printer(self, printer: Dict[str, Any]) -> Dict[str, Any]:
        report = {
            "id": printer.get('serialNumber', 'N/A'),
            "model": printer.get('modelName', 'N/A'),
            "insights": [],
            "savings_potential": 0.0,
            "policies": []
        }

        counters = printer.get('counters', {})
        supplies = printer.get('supplies', [])
        alerts = printer.get('alerts', [])

        # 1. Cor
        color = counters.get('colorPrintSideCount', 0)
        total = counters.get('printSideCount', 1)
        color_ratio = color / total if total > 0 else 0
        if color_ratio > 0.7:
            report["insights"].append(f"Cor: {color_ratio:.1%}")
            report["policies"].append("P&B padrão para não-críticos")
            report["savings_potential"] += 120

        # 2. Duplex
        duplex = counters.get('duplexSheetCount', 0)
        total_sheets = counters.get('printSheetCount', 1)
        duplex_ratio = duplex / total_sheets if total_sheets > 0 else 0
        if duplex_ratio < 0.5:
            report["insights"].append(f"Duplex: {duplex_ratio:.1%}")
            report["policies"].append("Ativar duplex padrão")
            report["savings_potential"] += 80

        # 3. Toner Baixo
        low_toner = [s for s in supplies if s.get('percentRemaining', 100) < 20 and s['type'] == 'Toner']
        if low_toner:
            colors = ", ".join([s['color'] for s in low_toner])
            report["insights"].append(f"Toner baixo: {colors}")
            report["policies"].append("Reposição automática")
            report["savings_potential"] += 50 * len(low_toner)

        # 4. Alertas
        critical = [a['issue'] for a in alerts if 'ERROR' in a.get('status', '')]
        if critical:
            report["insights"].append(f"Erro: {len(critical)}")
            report["policies"].append("Manutenção urgente")

        return report

    def get_summary(self):
        if not self.reports: return {}
        df = pd.DataFrame(self.reports)
        return {
            "total_printers": len(self.reports),
            "total_savings": df['savings_potential'].sum(),
            "high_impact": df[df['savings_potential'] > 100],
            "policies": list(set(p for r in self.reports for p in r['policies']))
        }

# === Interface Streamlit ===
with st.sidebar:
    st.header("Lexmark CFM API")
    client_id = st.text_input("Client ID", type="password")
    client_secret = st.text_input("Client Secret", type="password")
    region = st.selectbox("Região", ["us", "eu"])
    
    if st.button("Conectar e Analisar", type="primary"):
        if client_id and client_secret:
            st.session_state.connected = True
            st.session_state.client_id = client_id
            st.session_state.client_secret = client_secret
            st.session_state.region = region
        else:
            st.error("Preencha as credenciais")

if not st.session_state.get("connected"):
    st.info("Configure as credenciais da API Lexmark CFM no menu lateral.")
    st.stop()

# === Execução ===
cfm = LexmarkCFMClient(st.session_state.client_id, st.session_state.client_secret, st.session_state.region)

with st.spinner("Conectando à API Lexmark CFM..."):
    printers = cfm.get_all_printers()

st.success(f"{len(printers)} impressoras carregadas com sucesso!")

with st.spinner("Analisando com IA..."):
    agent = PrintCostOptimizerAgent(printers)
    agent.analyze()

summary = agent.get_summary()

# === Dashboard ===
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Impressoras", summary["total_printers"])
with col2:
    st.metric("Economia Potencial", f"R$ {summary['total_savings']:,.0f}/mês")
with col3:
    st.metric("Alta Prioridade", len(summary["high_impact"]))
with col4:
    st.metric("Políticas Únicas", len(summary["policies"]))

# === Tabela de Alta Prioridade ===
if not summary["high_impact"].empty:
    st.subheader("Impressoras com Maior Potencial de Economia")
    df_high = summary["high_impact"][['id', 'model', 'savings_potential', 'insights', 'policies']].copy()
    df_high['savings_potential'] = df_high['savings_potential'].apply(lambda x: f"R$ {x:,.0f}")
    df_high['insights'] = df_high['insights'].apply(lambda x: " | ".join(x))
    df_high['policies'] = df_high['policies'].apply(lambda x: " | ".join(x))
    st.dataframe(df_high, use_container_width=True)
    
    csv = df_high.to_csv(index=False).encode()
    st.download_button("Baixar Relatório CSV", csv, "economia_alta.csv", "text/csv")

# === Políticas Globais ===
st.subheader("Políticas Recomendadas (Globais)")
for policy in summary["policies"]:
    st.markdown(f"- {policy}")

st.caption(f"Última análise: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
