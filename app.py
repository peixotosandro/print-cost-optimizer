import streamlit as st
import pandas as pd
import requests
import logging
from datetime import datetime
from typing import List, Dict, Any
import time

# === CONFIGURAÇÃO DA PÁGINA ===
st.set_page_config(
    page_title="Print Fleet Optimizer Agent",
    layout="wide",
    initial_sidebar_state="expanded"
)

# === ESTILO PERSONALIZADO ===
st.markdown("""
<style>
    .big-font { font-size: 28px !important; font-weight: bold; color: #ffffff; }
    .stButton>button { border-radius: 8px; height: 3rem; font-weight: bold; }
    .stButton>button[kind="primary"] { background-color: #28a745; }
    .stButton>button[kind="secondary"] { background-color: #dc3545; color: white; }
    .policy-x { text-align: center; font-weight: bold; font-size: 18px; color: #10b981; }
    .policy-empty { text-align: center; font-size: 18px; color: #6b7280; }
</style>
""", unsafe_allow_html=True)

# === TÍTULO ===
st.markdown('<p class="big-font">Print Fleet Optimizer Agent</p>', unsafe_allow_html=True)
st.markdown("**Análise única com API Lexmark Cloud Fleet Management**")

# === PLACEHOLDERS ===
status_ph = st.empty()
metrics_ph = st.empty()
table_ph = st.empty()
policies_ph = st.empty()

# === LOGGING ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === CLIENTE LEXMARK CFM ===
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
        return {
            'Authorization': f'Bearer {self._get_token()}',
            'Accept': 'application/json'
        }

    def get_all_assets(self) -> List[Dict[str, Any]]:
        try:
            with st.spinner("Buscando todas as impressoras..."):
                response = requests.get(
                    f"{self.base_url}/v1.0/assets",
                    headers=self._get_headers(),
                    params={"pageSize": 1000},
                    timeout=30
                )
                response.raise_for_status()
                data = response.json()
                return data.get('content', [])
        except Exception as e:
            st.error(f"Erro na API: {e}")
            return []

# === AGENTE DE ANÁLISE ===
class PrintFleetOptimizerAgent:
    def __init__(self, printers: List[Dict[str, Any]]):
        self.printers = printers
        self.reports = []

    def analyze(self):
        for printer in self.printers:
            try:
                report = self._analyze_single_printer(printer)
                self.reports.append(report)
            except Exception as e:
                logger.warning(f"Erro ao analisar impressora: {e}")

    def _analyze_single_printer(self, printer: Dict[str, Any]) -> Dict[str, Any]:
        report = {
            "id": printer.get('serialNumber', 'N/A'),
            "model": printer.get('modelName', 'N/A'),
            "insights": [],
            "pb_padrao": False,
            "duplex": False,
            "reposicao": False,
            "manutencao": False
        }

        counters = printer.get('counters', {})
        supplies = printer.get('supplies', [])
        alerts = printer.get('alerts', [])

        # 1. Alta cor → P&B padrão
        color = counters.get('colorPrintSideCount', 0)
        total = counters.get('printSideCount', 1)
        color_ratio = color / total if total > 0 else 0
        if color_ratio > 0.7:
            report["insights"].append(f"Cor: {color_ratio:.0%}")
            report["pb_padrao"] = True

        # 2. Baixo duplex → Ativar duplex
        duplex = counters.get('duplexSheetCount', 0)
        total_sheets = counters.get('printSheetCount', 1)
        duplex_ratio = duplex / total_sheets if total_sheets > 0 else 0
        if duplex_ratio < 0.5:
            report["insights"].append(f"Duplex: {duplex_ratio:.0%}")
            report["duplex"] = True

        # 3. Toner baixo → Reposição Suprimento
        low_toner = [s for s in supplies if s.get('percentRemaining', 100) < 20 and s['type'] == 'Toner']
        if low_toner:
            colors = ", ".join([s['color'] for s in low_toner])
            report["insights"].append(f"Toner: {colors}")
            report["reposicao"] = True

        # 4. Alertas críticos → Manutenção
        critical = [a['issue'] for a in alerts if a.get('status') in ['ERROR', 'CRITICAL']]
        if critical:
            report["insights"].append(f"Erro: {len(critical)}")
            report["manutencao"] = True

        return report

# === SIDEBAR ===
with st.sidebar:
    st.header("Lexmark CFM API")
    client_id = st.text_input("Client ID", type="password")
    client_secret = st.text_input("Client Secret", type="password")
    region = st.selectbox("Região", ["us", "eu"])

    st.markdown("---")
    start_btn = st.button("Analisar Frota", type="primary", use_container_width=True)

# === EXECUÇÃO ===
if start_btn:
    if not client_id or not client_secret:
        st.error("Preencha Client ID e Secret")
        st.stop()

    for key in list(st.session_state.keys()):
        del st.session_state[key]

    cfm = LexmarkCFMClient(client_id, client_secret, region)
    printers = cfm.get_all_assets()

    if not printers:
        st.warning("Nenhuma impressora encontrada ou erro na API.")
        st.stop()

    with st.spinner("Analisando todas as impressoras..."):
        agent = PrintFleetOptimizerAgent(printers)
        agent.analyze()
        st.session_state.reports = agent.reports

    st.success(f"**Análise concluída!** {len(printers)} impressoras analisadas.")
    st.rerun()

# === RESULTADOS ===
all_reports = st.session_state.get("reports", [])
df = pd.DataFrame(all_reports)
high_impact = df[df['pb_padrao'] | df['duplex'] | df['reposicao'] | df['manutencao']] if not df.empty else pd.DataFrame()

# === MÉTRICAS ===
with metrics_ph.container():
    c1, c2, c3 = st.columns(3)
    c1.metric("Impressoras", len(all_reports))
    c2.metric("Com Recomendações", len(high_impact))
    c3.metric("Políticas Ativas", 
              sum(1 for r in all_reports if any(r.get(k, False) for k in ['pb_padrao', 'duplex', 'reposicao', 'manutencao'])))

# === TABELA INTERATIVA COM DATATABLES ===
with table_ph.container():
    if not high_impact.empty:
        df_display = high_impact[['id', 'model', 'insights', 'pb_padrao', 'duplex', 'reposicao', 'manutencao']].copy()
        df_display.columns = ['Serial Number', 'Modelo', 'Insights', 'P&B padrão', 'Ativar duplex', 'Reposição Suprimento', 'Manutenção']
        df_display['Insights'] = df_display['Insights'].apply(lambda x: " | ".join(x) if x else "Nenhum")

        policy_cols = ['P&B padrão', 'Ativar duplex', 'Reposição Suprimento', 'Manutenção']
        for col in policy_cols:
            df_display[col] = df_display[col].apply(
                lambda x: '<span class="policy-x">X</span>' if x else '<span class="policy-empty"> </span>'
            )

        html_table = df_display.to_html(escape=False, index=False, table_id="fleetTable")

        st.markdown("""
            <link rel="stylesheet" href="https://cdn.datatables.net/1.13.6/css/jquery.dataTables.min.css">
            <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
            <script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
            <script>
            $(document).ready(function() {
                $('#fleetTable').DataTable({
                    "pageLength": 15,
                    "order": [],
                    "language": {
                        "search": "Pesquisar:",
                        "lengthMenu": "Mostrar _MENU_ registros",
                        "info": "Mostrando _START_ a _END_ de _TOTAL_",
                        "paginate": {"next": "Próximo", "previous": "Anterior"}
                    }
                });
            });
            </script>
        """, unsafe_allow_html=True)

        st.markdown(html_table, unsafe_allow_html=True)

    elif all_reports:
        st.info("Nenhuma impressora com recomendações.")
    else:
        st.info("Clique em 'Analisar Frota' para começar.")

# === POLÍTICAS ATIVAS ===
with policies_ph.container():
    active = []
    if any(r.get('pb_padrao', False) for r in all_reports): active.append("P&B padrão")
    if any(r.get('duplex', False) for r in all_reports): active.append("Ativar duplex")
    if any(r.get('reposicao', False) for r in all_reports): active.append("Reposição Suprimento")
    if any(r.get('manutencao', False) for r in all_reports): active.append("Manutenção")

    if active:
        st.markdown("**Políticas Ativas:** " + " • ".join(active))
    elif all_reports:
        st.caption("Nenhuma política detectada.")

# === RELATÓRIO FINAL ===
if st.session_state.get("reports"):
    csv = df.to_csv(index=False).encode()
    st.download_button(
        "Baixar Relatório Completo (CSV)",
        csv,
        "relatorio_otimizacao.csv",
        "text/csv",
        use_container_width=True
    )
    st.caption(f"Atualizado: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
