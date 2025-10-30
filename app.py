import streamlit as st
import pandas as pd
import requests
import logging
from datetime import datetime
from typing import List, Dict, Any, Iterator, Tuple
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
st.markdown("**Análise com API Lexmark Cloud Fleet Management**")

# === PLACEHOLDERS ===
status_ph = st.empty()
metrics_ph = st.empty()
table_ph = st.empty()
policies_ph = st.empty()

# === LOGGING ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === CLIENTE LEXMARK CFM (PAGINAÇÃO INCREMENTAL) ===
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
        if self.access_token and time.time() < self.token_expiry - 60:
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

    def iterate_assets(self, page_size: int = 100, start_page: int = 0) -> Iterator[Tuple[List[Dict[str, Any]], Dict[str, Any]]]:
        """
        Itera por páginas e devolve (page_items, metadata) para cada página.
        metadata pode conter totalPages/totalCount se a API retornar isso.
        """
        page = start_page
        while True:
            params_variants = [
                {"page": page, "pageSize": page_size},  # some APIs
                {"page": page, "size": page_size},      # others
            ]
            # Try params variants until one works
            data = None
            last_exc = None
            for params in params_variants:
                try:
                    response = requests.get(
                        f"{self.base_url}/v1.0/assets",
                        headers=self._get_headers(),
                        params=params,
                        timeout=30
                    )
                    response.raise_for_status()
                    data = response.json()
                    break
                except Exception as e:
                    last_exc = e
                    # try next param variant

            if data is None:
                # both attempts failed
                st.error(f"Erro na requisição de página {page}: {last_exc}")
                return

            # Extrai itens da página com heurística
            page_items = None
            if isinstance(data, dict):
                if 'content' in data and isinstance(data['content'], list):
                    page_items = data['content']
                elif 'assets' in data and isinstance(data['assets'], list):
                    page_items = data['assets']
                elif 'items' in data and isinstance(data['items'], list):
                    page_items = data['items']
                elif isinstance(data.get('data', None), list):
                    page_items = data.get('data')
            elif isinstance(data, list):
                page_items = data

            if page_items is None:
                candidates = [v for v in (data.values() if isinstance(data, dict) else []) if isinstance(v, list)]
                page_items = candidates[0] if candidates else []

            # Metadata useful to decide stop
            metadata = {}
            if isinstance(data, dict):
                metadata['totalPages'] = data.get('totalPages') or data.get('total_pages')
                metadata['totalCount'] = data.get('totalElements') or data.get('totalCount') or data.get('total')

            yield page_items or [], metadata

            # decide if continue
            # if totalPages present and we've reached last one -> stop
            tp = metadata.get('totalPages')
            tc = metadata.get('totalCount')
            if tp is not None:
                try:
                    if page >= int(tp) - 1:
                        break
                except Exception:
                    pass
            if tc is not None:
                # cannot know how many we've fetched here (caller maintains seen set)
                # we'll rely on fallback check below too
                pass

            # fallback: if returned less than page_size, probably last page
            if not page_items or len(page_items) < page_size:
                break

            page += 1

# === AGENTE DE ANÁLISE ===
class PrintFleetOptimizerAgent:
    def __init__(self):
        # não passa printers na inicialização para análise incremental
        pass

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

        counters = printer.get('counters', {}) or {}
        supplies = printer.get('supplies', []) or []
        alerts = printer.get('alerts', []) or []

        # 1. Alta cor → P&B padrão
        color = counters.get('colorPrintSideCount', 0)
        total = counters.get('printSideCount', 1)
        try:
            color_ratio = color / total if total > 0 else 0
        except Exception:
            color_ratio = 0
        if color_ratio > 0.7:
            report["insights"].append(f"Cor: {color_ratio:.0%}")
            report["pb_padrao"] = True

        # 2. Baixo duplex → Ativar duplex
        duplex = counters.get('duplexSheetCount', 0)
        total_sheets = counters.get('printSheetCount', 1)
        try:
            duplex_ratio = duplex / total_sheets if total_sheets > 0 else 0
        except Exception:
            duplex_ratio = 0
        if duplex_ratio < 0.5:
            report["insights"].append(f"Duplex: {duplex_ratio:.0%}")
            report["duplex"] = True

        # 3. Toner baixo → Reposição Suprimento
        low_toner = [s for s in supplies if s.get('percentRemaining', 100) < 20 and s.get('type') == 'Toner']
        if low_toner:
            colors = ", ".join([s.get('color', 'Unknown') for s in low_toner])
            report["insights"].append(f"Toner: {colors}")
            report["reposicao"] = True

        # 4. Alertas críticos → Manutenção
        critical = [a.get('issue') for a in alerts if a.get('status') in ['ERROR', 'CRITICAL']]
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
    start_btn = st.button("Analisar Parque", type="primary", use_container_width=True)

# === INICIAR ANÁLISE (incremental por página) ===
if start_btn:
    if not client_id or not client_secret:
        st.error("Preencha Client ID e Secret")
        st.stop()

    cfm = LexmarkCFMClient(client_id, client_secret, region)
    page_size = 100  # ajuste se quiser (API pode impor limites)
    seen_serials = set()
    reports: List[Dict[str, Any]] = []
    agent = PrintFleetOptimizerAgent()

    status_ph.info("🔎 Buscando e processando páginas (iniciando)...")
    # iterate_assets yields (page_items, metadata)
    page_iter = cfm.iterate_assets(page_size=page_size, start_page=0)

    page_number = 0
    total_pages_estimate = None

    # iterate pages and process printers immediately
    for page_items, meta in page_iter:
        page_number += 1
        # meta may contain totalPages/totalCount
        if meta.get('totalPages'):
            try:
                total_pages_estimate = int(meta.get('totalPages'))
            except Exception:
                total_pages_estimate = None
        if meta.get('totalCount'):
            try:
                total_estimate = int(meta.get('totalCount'))
            except Exception:
                total_estimate = None
        # process each printer in page immediately (incremental)
        for printer in page_items:
            serial = printer.get('serialNumber') or printer.get('serial') or printer.get('id') or None
            # skip duplicates by serial
            if serial and serial in seen_serials:
                continue
            if serial:
                seen_serials.add(serial)

            try:
                report = agent._analyze_single_printer(printer)
            except Exception as e:
                logger.warning(f"Erro ao analisar impressora {serial}: {e}")
                report = {
                    "id": serial or 'N/A',
                    "model": printer.get('modelName', 'N/A'),
                    "insights": [f"Erro: {e}"],
                    "pb_padrao": False,
                    "duplex": False,
                    "reposicao": False,
                    "manutencao": False
                }

            reports.append(report)
            # save progress so page can be reloaded / inspected
            st.session_state["reports_temp"] = reports.copy()

            # rebuild partial high_impact for display
            high_impact_partial = [r for r in reports if any(r.get(k, False) for k in ['pb_padrao', 'duplex', 'reposicao', 'manutencao'])]

            # update metrics (incremental)
            with metrics_ph.container():
                c1, c2 = st.columns(2)
                c1.metric("Impressoras Analisadas", len(reports))
                c2.metric("Com Recomendações", len(high_impact_partial))

            # update table (incremental)
            with table_ph.container():
                if high_impact_partial:
                    df_display = pd.DataFrame(high_impact_partial)[['id', 'model', 'insights', 'pb_padrao', 'duplex', 'reposicao', 'manutencao']].copy()
                    df_display.columns = [
                        'Serial Number', 'Modelo', 'Insights',
                        'P&B padrão', 'Ativar duplex', 'Reposição Suprimento', 'Manutenção'
                    ]
                    df_display['Insights'] = df_display['Insights'].apply(lambda x: " | ".join(x) if x else "Nenhum")
                    for col in ['P&B padrão', 'Ativar duplex', 'Reposição Suprimento', 'Manutenção']:
                        df_display[col] = df_display[col].apply(lambda v: "✅" if v else "")
                    st.dataframe(df_display, use_container_width=True, hide_index=True)
                else:
                    st.info("Nenhuma impressora com recomendações até o momento.")

            # update status
            status_text = f"Página {page_number}"
            if total_pages_estimate:
                status_text += f" de ~{total_pages_estimate}"
            status_text += f" — {len(reports)} impressoras processadas"
            status_ph.info(status_text)

            # optional small sleep for smoother UI updates (commented by default)
            # time.sleep(0.01)

    # finished iterating pages
    st.session_state["reports"] = reports
    if "reports_temp" in st.session_state:
        del st.session_state["reports_temp"]

    status_ph.success(f"✅ Análise concluída. {len(reports)} impressoras processadas.")

# === RESULTADOS (sempre visível) ===
all_reports = st.session_state.get("reports", []) or []
df = pd.DataFrame(all_reports)
high_impact = df[df['pb_padrao'] | df['duplex'] | df['reposicao'] | df['manutencao']] if not df.empty else pd.DataFrame()

# === MÉTRICAS (estado final) ===
with metrics_ph.container():
    analyzed_printers = len(all_reports)
    recommendations = len(high_impact)
    c1, c2 = st.columns(2)
    c1.metric("Impressoras Analisadas", analyzed_printers)
    c2.metric("Com Recomendações", recommendations)

# === TABELA INTERATIVA (estado final) ===
with table_ph.container():
    if not high_impact.empty:
        df_display = high_impact[['id', 'model', 'insights', 'pb_padrao', 'duplex', 'reposicao', 'manutencao']].copy()
        df_display.columns = [
            'Serial Number', 'Modelo', 'Insights',
            'P&B padrão', 'Ativar duplex', 'Reposição Suprimento', 'Manutenção'
        ]
        df_display['Insights'] = df_display['Insights'].apply(lambda x: " | ".join(x) if x else "Nenhum")
        for col in ['P&B padrão', 'Ativar duplex', 'Reposição Suprimento', 'Manutenção']:
            df_display[col] = df_display[col].apply(lambda v: "✅" if v else "")
        st.dataframe(df_display, use_container_width=True, hide_index=True)
    elif all_reports:
        st.info("Nenhuma impressora com recomendações.")
    else:
        st.info("Clique em 'Analisar Parque' para começar.")

# === RECOMENDAÇÕES (resumo textual) ===
with policies_ph.container():
    active = []
    if any(r.get('pb_padrao', False) for r in all_reports): active.append("P&B padrão")
    if any(r.get('duplex', False) for r in all_reports): active.append("Ativar duplex")
    if any(r.get('reposicao', False) for r in all_reports): active.append("Reposição Suprimento")
    if any(r.get('manutencao', False) for r in all_reports): active.append("Manutenção")

    if active:
        st.markdown("**Recomendações:** " + " • ".join(active))
    elif all_reports:
        st.caption("Nenhuma recomendação detectada.")

# === RELATÓRIO FINAL ===
if st.session_state.get("reports"):
    df_final = pd.DataFrame(st.session_state.get("reports", []))
    csv = df_final.to_csv(index=False).encode()
    st.download_button(
        "Baixar Relatório Completo (CSV)",
        csv,
        "relatorio_otimizacao.csv",
        "text/csv",
        use_container_width=True
    )
    st.caption(f"Atualizado: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
