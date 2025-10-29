# app.py
import streamlit as st
import pandas as pd
import requests
import logging
from datetime import datetime
from typing import List, Dict, Any
import time

# === CONFIGURAÇÃO ===
st.set_page_config(page_title="Print Cost Optimizer", layout="wide", initial_sidebar_state="expanded")

# === ESTILO ===
st.markdown("""
<style>
    .big-font { font-size: 28px !important; font-weight: bold; color: #ffffff; }
    .stButton>button { border-radius: 8px; height: 3rem; font-weight: bold; }
    .stButton>button[kind="primary"] { background-color: #28a745; }  /* VERDE */
    .stButton>button[kind="secondary"] { background-color: #dc3545; color: white; }  /* VERMELHO */
</style>
""", unsafe_allow_html=True)

# === TÍTULO ===
st.markdown('<p class="big-font">Print Cost Optimizer Agent</p>', unsafe_allow_html=True)
st.markdown("**Análise em tempo real com API Lexmark Cloud Fleet Management**")

# === LOGGING ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === CLIENTE LEXMARK ===
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
        payload = {"grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self.client_secret}
        try:
            response = requests.post(self.token_url, json=payload, timeout=10)
            response.raise_for_status()
            data = response.json()
            self.access_token = data['access_token']
            self.token_expiry = int(time.time()) + 3500
            return self.access_token
        except Exception as e:
            st.error(f"Erro ao obter token: {e}")
            raise

    def _get_headers(self):
        return {'Authorization': f'Bearer {self._get_token()}', 'Accept': 'application/json'}

# === AGENTE ===
class PrintCostOptimizerAgent:
    def __init__(self, printers: List[Dict[str, Any]]):
        self.printers = printers
        self.reports = []

    def analyze(self):
        for printer in self.printers:
            try:
                report = self._analyze_single_printer(printer)
                self.reports.append(report)
            except Exception as e:
                logger.warning(f"Erro: {e}")

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

        # Cor
        color = counters.get('colorPrintSideCount', 0)
        total = counters.get('printSideCount', 1)
        color_ratio = color / total if total > 0 else 0
        if color_ratio > 0.7:
            report["insights"].append(f"Cor: {color_ratio:.0%}")
            report["policies"].append("P&B padrão")
            report["savings_potential"] += 120

        # Duplex
        duplex = counters.get('duplexSheetCount', 0)
        total_sheets = counters.get('printSheetCount', 1)
        duplex_ratio = duplex / total_sheets if total_sheets > 0 else 0
        if duplex_ratio < 0.5:
            report["insights"].append(f"Duplex: {duplex_ratio:.0%}")
            report["policies"].append("Ativar duplex")
            report["savings_potential"] += 80

        # Toner
        low_toner = [s for s in supplies if s.get('percentRemaining', 100) < 20 and s['type'] == 'Toner']
        if low_toner:
            colors = ", ".join([s['color'] for s in low_toner])
            report["insights"].append(f"Toner: {colors}")
            report["policies"].append("Reposição auto")
            report["savings_potential"] += 50 * len(low_toner)

        # Alertas
        critical = [a['issue'] for a in alerts if a.get('status') in ['ERROR', 'CRITICAL']]
        if critical:
            report["insights"].append(f"Erro: {len(critical)}")
            report["policies"].append("Manutenção")

        return report

# === SIDEBAR ===
with st.sidebar:
    st.header("Lexmark CFM API")
    client_id = st.text_input("Client ID", type="password")
    client_secret = st.text_input("Client Secret", type="password")
    region = st.selectbox("Região", ["us", "eu"])

    st.markdown("---")
    start_btn = st.button("Conectar e Analisar", type="primary", use_container_width=True)
    st.markdown("<br>", unsafe_allow_html=True)
    stop_btn = st.button("Parar Análise", type="secondary", use_container_width=True)

# === INICIAR ===
if start_btn:
    if not client_id or not client_secret:
        st.error("Preencha as credenciais")
        st.stop()

    # Reset seguro
    st.session_state.clear()
    st.session_state.reports = []
    st.session_state.page = 0
    st.session_state.is_running = True
    st.rerun()

# === PARAR ===
if stop_btn and st.session_state.get("is_running"):
    st.session_state.is_running = False
    st.rerun()

# === EXECUÇÃO (SÓ SE ESTIVER RODANDO) ===
if st.session_state.get("is_running", False):
    cfm = LexmarkCFMClient(client_id, client_secret, region)

    # Placeholders
    status_ph = st.empty()
    metrics_ph = st.empty()
    table_ph = st.empty()
    policies_ph = st.empty()

    all_reports = st.session_state.reports
    page = st.session_state.page

    try:
        with status_ph.container():
            st.info(f"Buscando página {page + 1}...")

        response = requests.get(
            f"{cfm.base_url}/v1.0/assets",
            headers=cfm._get_headers(),
            params={"pageNumber": page, "pageSize": 200},
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        printers_page = data.get('content', [])

        if not printers_page:
            st.session_state.is_running = False
            st.rerun()

        # Analisa
        agent = PrintCostOptimizerAgent(printers_page)
        agent.analyze()
        new_reports = agent.reports

        # Remove duplicatas por ID
        seen_ids = {r["id"] for r in all_reports}
        new_reports = [r for r in new_reports if r["id"] not in seen_ids]
        all_reports.extend(new_reports)

        # Atualiza estado
        st.session_state.reports = all_reports
        st.session_state.page = page + 1

        # Dashboard
        df = pd.DataFrame(all_reports)
        total_savings = df['savings_potential'].sum()
        high_impact = df[df['savings_potential'] > 100]

        with status_ph.container():
            st.success(f"Página {page + 1} • {len(all_reports)} impressoras")

        with metrics_ph.container():
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Impressoras", len(all_reports))
            c2.metric("Economia Atual", f"R$ {total_savings:,.0f}")
            c3.metric("Alta Prioridade", len(high_impact))
            c4.metric("Páginas", page + 1)

        with table_ph.container():
            if not high_impact.empty:
                df_display = high_impact[['id', 'model', 'savings_potential', 'insights']].copy()
                df_display['savings_potential'] = df_display['savings_potential'].apply(lambda x: f"R$ {x:,.0f}")
                df_display['insights'] = df_display['insights'].apply(lambda x: " | ".join(x))
                st.dataframe(df_display, use_container_width=True, hide_index=True)

        with policies_ph.container():
            policies = list(set(p for r in all_reports for p in r['policies']))
            if policies:
                st.markdown("**Políticas:** " + " • ".join(policies[:6]))

        # Próxima página
        if data.get('last', False):
            st.session_state.is_running = False

        st.rerun()  # Só aqui!

    except Exception as e:
        st.error(f"Erro: {e}")
        st.session_state.is_running = False
        st.rerun()

# === FINAL ===
elif st.session_state.get("reports"):
    df = pd.DataFrame(st.session_state.reports)
    total_savings = df['savings_potential'].sum()
    st.success(f"**Análise Completa!** {len(df)} impressoras • R$ {total_savings:,.0f}/mês")
    csv = df.to_csv(index=False).encode()
    st.download_button("Baixar Relatório", csv, "relatorio_completo.csv", "text/csv")
    st.caption(f"Atualizado: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
