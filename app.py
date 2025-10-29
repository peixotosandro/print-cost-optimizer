# app.py
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
</style>
""", unsafe_allow_html=True)

# === TÍTULO ===
st.markdown('<p class="big-font">Print Optimizer Agent</p>', unsafe_allow_html=True)
st.markdown("**Análise em tempo real com API Lexmark Cloud Fleet Management**")

# === PLACEHOLDERS GLOBAIS ===
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

# === AGENTE DE ANÁLISE ===
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
                logger.warning(f"Erro ao analisar impressora: {e}")

    def _analyze_single_printer(self, printer: Dict[str, Any]) -> Dict[str, Any]:
        report = {
            "id": printer.get('serialNumber', 'N/A'),
            "model": printer.get('modelName', 'N/A'),
            "insights": [],
            "policies": []
        }

        counters = printer.get('counters', {})
        supplies = printer.get('supplies', [])
        alerts = printer.get('alerts', [])

        # 1. Alta cor
        color = counters.get('colorPrintSideCount', 0)
        total = counters.get('printSideCount', 1)
        color_ratio = color / total if total > 0 else 0
        if color_ratio > 0.7:
            report["insights"].append(f"Cor: {color_ratio:.0%}")
            report["policies"].append("P&B padrão")

        # 2. Baixo duplex
        duplex = counters.get('duplexSheetCount', 0)
        total_sheets = counters.get('printSheetCount', 1)
        duplex_ratio = duplex / total_sheets if total_sheets > 0 else 0
        if duplex_ratio < 0.5:
            report["insights"].append(f"Duplex: {duplex_ratio:.0%}")
            report["policies"].append("Ativar duplex")

        # 3. Toner baixo
        low_toner = [s for s in supplies if s.get('percentRemaining', 100) < 20 and s['type'] == 'Toner']
        if low_toner:
            colors = ", ".join([s['color'] for s in low_toner])
            report["insights"].append(f"Toner: {colors}")
            report["policies"].append("Reposição Suprimento")

        # 4. Alertas críticos
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

# === INICIAR ANÁLISE ===
if start_btn:
    if not client_id or not client_secret:
        st.error("Preencha Client ID e Secret")
        st.stop()

    for key in list(st.session_state.keys()):
        del st.session_state[key]

    st.session_state.reports = []
    st.session_state.page = 0
    st.session_state.is_running = True
    st.rerun()

# === PARAR ANÁLISE ===
if stop_btn and st.session_state.get("is_running"):
    st.session_state.is_running = False
    st.rerun()

# === ESTADO ATUAL ===
all_reports = st.session_state.get("reports", [])
page = st.session_state.get("page", 0)
is_running = st.session_state.get("is_running", False)

# === DASHBOARD ===
df = pd.DataFrame(all_reports)
high_impact = df[df['policies'].map(len) > 0] if not df.empty else pd.DataFrame()

# --- BARRA DE STATUS ---
if is_running:
    with status_ph.container():
        st.markdown(f"""
        <div style="background-color: #1e40af; padding: 12px; border-radius: 8px; text-align: center; color: white; font-weight: bold;">
            Buscando página {page + 1} • {len(all_reports)} impressoras analisadas
        </div>
        <div style="background-color: #374151; border-radius: 8px; height: 8px; margin-top: 8px;">
            <div style="background-color: #10b981; width: 100%; height: 100%; border-radius: 8px;"></div>
        </div>
        """, unsafe_allow_html=True)
else:
    status_ph.empty()

# --- MÉTRICAS ---
with metrics_ph.container():
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Impressoras", len(all_reports))
    c2.metric("Com Recomendações", len(high_impact))
    c3.metric("Políticas Ativas", len(set(p for r in all_reports for p in r['policies'])))
    c4.metric("Páginas", page)

# --- TABELA ---
with table_ph.container():
    if not high_impact.empty:
        df_display = high_impact[['id', 'model', 'insights', 'policies']].copy()
        df_display.columns = ['Serial Number', 'Modelo', 'Insights', 'Aplicar Políticas']
        df_display['Insights'] = df_display['Insights'].apply(lambda x: " | ".join(x))
        df_display['Aplicar Políticas'] = df_display['Aplicar Políticas'].apply(lambda x: " • ".join(x) if x else "Nenhuma")
        st.dataframe(df_display, use_container_width=True, hide_index=True)
    elif all_reports:
        st.info("Nenhuma impressora com recomendações ainda.")
    else:
        st.info("Aguardando dados...")

# --- POLÍTICAS ATIVAS ---
with policies_ph.container():
    policies = list(set(p for r in all_reports for p in r['policies']))
    if policies:
        st.markdown("**Políticas:** " + " • ".join(policies[:6]))
    elif all_reports:
        st.caption("Nenhuma política detectada ainda.")

# === EXECUÇÃO ===
if is_running:
    cfm = LexmarkCFMClient(client_id, client_secret, region)

    try:
        response = requests.get(
            f"{cfm.base_url}/v1.0/assets",
            headers=cfm._get_headers(),
            params={"pageNumber": page, "pageSize": 200},
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        printers_page = data.get('content', [])

        total_pages = data.get('totalPages')
        if total_pages is not None and page >= total_pages:
            st.session_state.is_running = False
            st.success(f"Análise concluída! {page} páginas processadas.")
            st.rerun()

        if not printers_page:
            st.session_state.is_running = False
            st.success(f"Análise concluída! {page} páginas • {len(all_reports)} impressoras.")
            st.rerun()

        if page >= 100:
            st.session_state.is_running = False
            st.warning("Parada de segurança: mais de 100 páginas.")
            st.rerun()

        agent = PrintCostOptimizerAgent(printers_page)
        agent.analyze()
        new_reports = agent.reports

        seen_ids = {r["id"] for r in all_reports}
        new_reports = [r for r in new_reports if r["id"] not in seen_ids]
        all_reports.extend(new_reports)

        st.session_state.reports = all_reports
        st.session_state.page = page + 1

        with status_ph.container():
            if total_pages:
                progress = (page + 1) / total_pages
                st.markdown(f"""
                <div style="background-color: #1e40af; padding: 12px; border-radius: 8px; text-align: center; color: white; font-weight: bold;">
                    Buscando página {page + 1} de {total_pages} • {len(all_reports)} impressoras
                </div>
                <div style="background-color: #374151; border-radius: 8px; height: 8px; margin-top: 8px;">
                    <div style="background-color: #10b981; width: {progress*100:.1f}%; height: 100%; border-radius: 8px;"></div>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div style="background-color: #1e40af; padding: 12px; border-radius: 8px; text-align: center; color: white; font-weight: bold;">
                    Buscando página {page + 1} • {len(all_reports)} impressoras analisadas
                </div>
                """, unsafe_allow_html=True)

        st.rerun()

    except Exception as e:
        st.error(f"Erro na API: {e}")
        st.session_state.is_running = False
        st.rerun()

# === RELATÓRIO FINAL ===
elif st.session_state.get("reports"):
    df = pd.DataFrame(st.session_state.reports)
    st.success(f"**Análise Completa!** {len(df)} impressoras analisadas.")
    csv = df.to_csv(index=False).encode()
    st.download_button(
        "Baixar Relatório Completo (CSV)",
        csv,
        "relatorio_otimizacao.csv",
        "text/csv",
        use_container_width=True
    )
    st.caption(f"Atualizado: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
