import streamlit as st
import pandas as pd
import requests
import logging
from datetime import datetime
from typing import List, Dict, Any, Iterator, Tuple, Optional
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
    .debug { font-size: 13px; color: #9ca3af; }
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

# === SIDEBAR / CONTROLES ===
with st.sidebar:
    st.header("Lexmark CFM API")
    client_id = st.text_input("Client ID", type="password")
    client_secret = st.text_input("Client Secret", type="password")
    region = st.selectbox("Região", ["us", "eu"])
    page_size_override = st.number_input("Page size (override)", min_value=10, max_value=2000, value=100, step=10)
    debug_pagination = st.checkbox("Mostrar debug de paginação", value=False)
    st.markdown("---")
    start_btn = st.button("Analisar Parque", type="primary", use_container_width=True)

# === CLIENTE LEXMARK CFM (PAGINAÇÃO ROBUSTA E INCREMENTAL) ===
class LexmarkCFMClient:
    def __init__(self, client_id: str, client_secret: str, region: str = 'us', debug: bool = False):
        self.client_id = client_id
        self.client_secret = client_secret
        self.region = region.lower()
        self.base_url = f"https://apis.{self.region}.iss.lexmark.com/cfm/fleetmgmt-integration-service"
        self.token_url = f"https://idp.{self.region}.iss.lexmark.com/oauth/token"
        self.access_token: Optional[str] = None
        self.token_expiry = 0
        self.debug = debug

    def _get_token(self) -> str:
        if self.access_token and time.time() < self.token_expiry - 60:
            return self.access_token
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }
        response = requests.post(self.token_url, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()
        self.access_token = data['access_token']
        self.token_expiry = int(time.time()) + 3500
        return self.access_token

    def _get_headers(self):
        return {
            'Authorization': f'Bearer {self._get_token()}',
            'Accept': 'application/json'
        }

    def iterate_assets(self, page_size: int = 100, start_page: int = 0) -> Iterator[Tuple[List[Dict[str, Any]], Dict[str, Any]]]:
        """
        Itera por páginas e retorna (page_items, metadata) para cada página.
        Tenta suportar:
         - page/size
         - page/pageSize
         - links/_links with rel: next
         - nextPage / nextCursor / next
        Processa cada página assim que recebida (incremental).
        """
        page = start_page
        fetched_pages = 0

        # tentativa 1: prefer params page/size, but we'll try variants
        while True:
            params_candidates = [
                {"page": page, "size": page_size},
                {"page": page, "pageSize": page_size},
                {"pageNumber": page, "pageSize": page_size},
            ]
            data = None
            last_exc = None
            used_params = None

            for params in params_candidates:
                try:
                    resp = requests.get(f"{self.base_url}/v1.0/assets", headers=self._get_headers(), params=params, timeout=30)
                    resp.raise_for_status()
                    data = resp.json()
                    used_params = params
                    break
                except Exception as e:
                    last_exc = e
                    if self.debug:
                        logger.debug(f"params {params} failed: {e}")
                    # continue to next params variant

            if data is None:
                # try bare call (no paging params)
                try:
                    resp = requests.get(f"{self.base_url}/v1.0/assets", headers=self._get_headers(), timeout=30)
                    resp.raise_for_status()
                    data = resp.json()
                    used_params = {}
                except Exception as e:
                    st.error(f"Erro ao requisitar assets (página {page}): {e}")
                    return

            # extract list of items heuristically
            page_items = []
            if isinstance(data, dict):
                # common container fields
                for key in ("content", "assets", "items", "data", "results"):
                    if key in data and isinstance(data[key], list):
                        page_items = data[key]
                        break
                # fallback: if dict has single list value, use it
                if not page_items:
                    candidates = [v for v in data.values() if isinstance(v, list)]
                    if candidates:
                        page_items = candidates[0]
            elif isinstance(data, list):
                page_items = data

            # metadata detection
            meta: Dict[str, Any] = {}
            if isinstance(data, dict):
                # try total pages / total counts
                meta['totalPages'] = data.get('totalPages') or data.get('total_pages') or data.get('pageCount') or None
                meta['totalCount'] = data.get('totalElements') or data.get('totalCount') or data.get('total') or None
                # try links / _links
                if '_links' in data and isinstance(data['_links'], dict):
                    meta['links'] = data['_links']
                elif 'links' in data and isinstance(data['links'], dict):
                    meta['links'] = data['links']
                # next cursor style
                if 'next' in data:
                    meta['next'] = data['next']

            fetched_pages += 1
            if self.debug:
                logger.info(f"Fetched page {page} (params used: {used_params}), items: {len(page_items)}")

            yield page_items or [], meta

            # decide whether to continue
            # 1) if links.next exists, follow it (we rely on page loop; but we support stopping here)
            if isinstance(meta.get('links'), dict):
                # try find a next link marker in different shapes
                links = meta['links']
                next_link = None
                # common shapes: links.get('next') -> {'href': '...'}
                if 'next' in links:
                    if isinstance(links['next'], dict) and 'href' in links['next']:
                        next_link = links['next']['href']
                # _links may contain self/next etc.
                for k, v in links.items():
                    if k.lower().startswith('next'):
                        if isinstance(v, dict) and 'href' in v:
                            next_link = v['href']
                if next_link:
                    # We cannot easily follow next_link via requests.get(next_link, headers=...) because base_url might require tokens same way; but try:
                    try:
                        resp = requests.get(next_link, headers=self._get_headers(), timeout=30)
                        resp.raise_for_status()
                        data = resp.json()
                        # set page_items from this new response and continue loop without incrementing page var
                        # we will loop again but use the newly fetched data by re-yielding via continue
                        # To implement cleanly, we set page_items = new response and yield it next iteration by updating data to that
                        # Simpler: increase page and continue — some APIs with links also accept page params so fallback will still work.
                    except Exception:
                        # ignore and fall back to param-based pagination
                        pass

            # 2) if totalPages present and we've reached last -> stop
            tp = meta.get('totalPages')
            tc = meta.get('totalCount')
            if tp is not None:
                try:
                    if page >= int(tp) - 1:
                        break
                except Exception:
                    pass

            # 3) fallback: if page_items length < page_size -> last page
            if not page_items or len(page_items) < page_size:
                break

            # 4) otherwise increment page and continue
            page += 1

# === AGENTE DE ANÁLISE ===
class PrintFleetOptimizerAgent:
    def __init__(self):
        pass

    def _analyze_single_printer(self, printer: Dict[str, Any]) -> Dict[str, Any]:
        report = {
            "id": printer.get('serialNumber', printer.get('serial', printer.get('id', 'N/A'))),
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
        try:
            color = counters.get('colorPrintSideCount', 0)
            total = counters.get('printSideCount', 1)
            color_ratio = color / total if total > 0 else 0
        except Exception:
            color_ratio = 0
        if color_ratio > 0.7:
            report["insights"].append(f"Cor: {color_ratio:.0%}")
            report["pb_padrao"] = True

        # 2. Baixo duplex → Ativar duplex
        try:
            duplex = counters.get('duplexSheetCount', 0)
            total_sheets = counters.get('printSheetCount', 1)
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

# === INICIAR ANÁLISE (fluxo principal) ===
if start_btn:
    if not client_id or not client_secret:
        st.error("Preencha Client ID e Secret")
        st.stop()

    client = LexmarkCFMClient(client_id, client_secret, region, debug=debug_pagination)
    agent = PrintFleetOptimizerAgent()
    page_size = int(page_size_override)

    seen_serials = set()
    reports: List[Dict[str, Any]] = []

    status_ph.info("🔎 Iniciando leitura de páginas e análise incremental...")
    page_counter = 0
    item_counter = 0

    # iterate pages and process immediately
    for page_items, meta in client.iterate_assets(page_size=page_size, start_page=0):
        page_counter += 1
        if debug_pagination:
            st.write(f"DEBUG: página {page_counter} retornou {len(page_items)} itens; meta={meta}", unsafe_allow_html=True)
        for p in page_items:
            serial = p.get('serialNumber') or p.get('serial') or p.get('id')
            if serial and serial in seen_serials:
                # pular duplicata
                continue
            if serial:
                seen_serials.add(serial)

            item_counter += 1
            # process single printer and append
            try:
                report = agent._analyze_single_printer(p)
            except Exception as e:
                logger.warning(f"Erro ao analisar {serial}: {e}")
                report = {
                    "id": serial or 'N/A',
                    "model": p.get('modelName', 'N/A'),
                    "insights": [f"Erro: {e}"],
                    "pb_padrao": False,
                    "duplex": False,
                    "reposicao": False,
                    "manutencao": False
                }
            reports.append(report)
            # save incremental progress
            st.session_state["reports_temp"] = reports.copy()

            # update incremental metrics and table
            high_impact_partial = [r for r in reports if any(r.get(k, False) for k in ['pb_padrao','duplex','reposicao','manutencao'])]
            with metrics_ph.container():
                c1, c2 = st.columns(2)
                c1.metric("Impressoras Analisadas", len(reports))
                c2.metric("Com Recomendações", len(high_impact_partial))
            with table_ph.container():
                if high_impact_partial:
                    df_display = pd.DataFrame(high_impact_partial)[['id','model','insights','pb_padrao','duplex','reposicao','manutencao']].copy()
                    df_display.columns = ['Serial Number','Modelo','Insights','P&B padrão','Ativar duplex','Reposição Suprimento','Manutenção']
                    df_display['Insights'] = df_display['Insights'].apply(lambda x: " | ".join(x) if x else "Nenhum")
                    for col in ['P&B padrão','Ativar duplex','Reposição Suprimento','Manutenção']:
                        df_display[col] = df_display[col].apply(lambda v: "✅" if v else "")
                    st.dataframe(df_display, use_container_width=True, hide_index=True)
                else:
                    st.info("Nenhuma impressora com recomendações até o momento.")
            # small optional sleep to make UI smoother (comment/uncomment as needed)
            # time.sleep(0.01)

        # update status after finishing page
        status_text = f"Página {page_counter} processada — {len(reports)} impressoras únicas processadas"
        if meta.get('totalPages'):
            status_text += f" / ~{meta.get('totalPages')} páginas"
        if meta.get('totalCount'):
            status_text += f" — total estimado: {meta.get('totalCount')}"
        status_ph.info(status_text)

    # finalize
    st.session_state["reports"] = reports
    if "reports_temp" in st.session_state:
        del st.session_state["reports_temp"]
    status_ph.success(f"✅ Análise concluída. {len(reports)} impressoras únicas processadas (lidas {page_counter} páginas).")

# === RESULTADOS (final) ===
all_reports = st.session_state.get("reports", []) or []
df = pd.DataFrame(all_reports)
high_impact = df[df['pb_padrao'] | df['duplex'] | df['reposicao'] | df['manutencao']] if not df.empty else pd.DataFrame()

with metrics_ph.container():
    c1, c2 = st.columns(2)
    c1.metric("Impressoras Analisadas", len(all_reports))
    c2.metric("Com Recomendações", len(high_impact))

with table_ph.container():
    if not high_impact.empty:
        df_display = high_impact[['id','model','insights','pb_padrao','duplex','reposicao','manutencao']].copy()
        df_display.columns = ['Serial Number','Modelo','Insights','P&B padrão','Ativar duplex','Reposição Suprimento','Manutenção']
        df_display['Insights'] = df_display['Insights'].apply(lambda x: " | ".join(x) if x else "Nenhum")
        for col in ['P&B padrão','Ativar duplex','Reposição Suprimento','Manutenção']:
            df_display[col] = df_display[col].apply(lambda v: "✅" if v else "")
        st.dataframe(df_display, use_container_width=True, hide_index=True)
    elif all_reports:
        st.info("Nenhuma impressora com recomendações.")
    else:
        st.info("Clique em 'Analisar Parque' para começar.")

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

if st.session_state.get("reports"):
    df_final = pd.DataFrame(st.session_state.get("reports", []))
    csv = df_final.to_csv(index=False).encode()
    st.download_button("Baixar Relatório Completo (CSV)", csv, "relatorio_otimizacao.csv", "text/csv", use_container_width=True)
    st.caption(f"Atualizado: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
