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
st.markdown("**Análise com API Lexmark Cloud Fleet Management**")

# === PLACEHOLDERS ===
status_ph = st.empty()
metrics_ph = st.empty()
table_ph = st.empty()
policies_ph = st.empty()

# === LOGGING ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === CLIENTE LEXMARK CFM (COM PAGINAÇÃO ROBUSTA) ===
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

    def get_all_assets(self) -> List[Dict[str, Any]]:
        """
        Busca todos os ativos paginando automaticamente.
        Tenta suportar estruturas comuns de resposta:
         - {"content": [...], "totalPages": N}
         - {"assets": [...], "totalPages": N}
         - {"items": [...], "totalPages": N}
         - fallback: continua enquanto cada página retorna `pageSize` items
        """
        all_assets: List[Dict[str, Any]] = []
        page = 0
        page_size = 100

        try:
            with st.spinner("Buscando todas as impressoras (paginação)..."):
                while True:
                    params = {"page": page, "size": page_size}
                    response = requests.get(
                        f"{self.base_url}/v1.0/assets",
                        headers=self._get_headers(),
                        params=params,
                        timeout=30
                    )
                    response.raise_for_status()
                    data = response.json()

                    # Tentar localizar a lista de assets em campos comuns
                    page_items = None
                    if isinstance(data, dict):
                        if 'content' in data and isinstance(data['content'], list):
                            page_items = data['content']
                        elif 'assets' in data and isinstance(data['assets'], list):
                            page_items = data['assets']
                        elif 'items' in data and isinstance(data['items'], list):
                            page_items = data['items']
                        # Alguns endpoints retornam diretamente uma lista (pouco comum aqui)
                        elif isinstance(data.get('data', None), list):
                            page_items = data.get('data')
                        # Caso a própria resposta seja uma lista
                        elif isinstance(data, list):
                            page_items = data

                    # Se não encontramos via chaves, tentamos extrair heurística
                    if page_items is None:
                        # tenta interpretar como dict com uma única lista
                        candidates = [v for v in (data.values() if isinstance(data, dict) else []) if isinstance(v, list)]
                        page_items = candidates[0] if candidates else []

                    # Adiciona os itens encontrados (pode ser vazio)
                    all_assets.extend(page_items)

                    # Determina se deve continuar: verifica totalPages ou compara tamanho
                    total_pages = None
                    if isinstance(data, dict):
                        # checar chaves comuns
                        total_pages = data.get('totalPages') or data.get('total_pages') or None
                        # em algumas APIs há totalElements/totalCount
                        total_count = data.get('totalElements') or data.get('totalCount') or data.get('total') or None
                        if total_pages is not None:
                            # totalPages costuma ser inteiro
                            try:
                                total_pages_int = int(total_pages)
                                if page >= (total_pages_int - 1):
                                    break
                            except Exception:
                                pass
                        elif total_count is not None:
                            try:
                                total_count_int = int(total_count)
                                # se já pegamos todos
                                if len(all_assets) >= total_count_int:
                                    break
                            except Exception:
                                pass

                    # fallback por tamanho da página: se retornou menos que page_size, acabou
                    if not page_items or len(page_items) < page_size:
                        break

                    # aumenta página (padrão: page começa em 0)
                    page += 1

                return all_assets
        except Exception as e:
            st.error(f"Erro na API durante paginação: {e}")
            return all_assets

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

# === INICIAR ANÁLISE ===
if start_btn:
    if not client_id or not client_secret:
        st.error("Preencha Client ID e Secret")
        st.stop()

    # NÃO apagar todo o session_state aqui — apenas sobrescreva o que for necessário
    cfm = LexmarkCFMClient(client_id, client_secret, region)
    # get_all_assets agora faz paginação completa
    printers = cfm.get_all_assets()

    # GUARDA o total retornado pela API (todas as páginas)
    st.session_state["printers_raw"] = printers

    if not printers:
        st.warning("Nenhuma impressora encontrada ou erro na API.")
        st.stop()

    # === ANÁLISE EM TEMPO REAL ===
    progress_ph = st.empty()
    table_ph = st.empty()
    metrics_ph = st.empty()

    agent = PrintFleetOptimizerAgent([])

    progress_text = progress_ph.text("🔍 Iniciando análise...")
    total = len(printers)

    reports = []
    for i, printer in enumerate(printers, 1):
        report = agent._analyze_single_printer(printer)
        reports.append(report)

        # Atualiza métricas parciais
        analyzed_printers = i
        high_impact = [r for r in reports if any(r.get(k, False) for k in ['pb_padrao', 'duplex', 'reposicao', 'manutencao'])]
        recommendations = len(high_impact)

        with metrics_ph.container():
            c1, c2 = st.columns(2)
            c1.metric("Impressoras Analisadas", analyzed_printers)
            c2.metric("Com Recomendações", recommendations)

        # Atualiza tabela parcial
        if high_impact:
            df_display = pd.DataFrame(high_impact)[['id', 'model', 'insights', 'pb_padrao', 'duplex', 'reposicao', 'manutencao']]
            df_display.columns = ['Serial Number', 'Modelo', 'Insights', 'P&B padrão', 'Ativar duplex', 'Reposição Suprimento', 'Manutenção']
            df_display['Insights'] = df_display['Insights'].apply(lambda x: " | ".join(x) if x else "Nenhum")
            for col in ['P&B padrão', 'Ativar duplex', 'Reposição Suprimento', 'Manutenção']:
                df_display[col] = df_display[col].apply(lambda v: "✅" if v else "")
            table_ph.dataframe(df_display, use_container_width=True, hide_index=True)
        else:
            table_ph.info("Nenhuma impressora com recomendações até o momento.")

        progress_text.text(f"🔎 Analisando impressora {i}/{total}...")

    st.session_state.reports = reports
    progress_text.text("✅ Análise concluída!")




    

    st.success(f"**Análise concluída!** {len(printers)} impressoras verificadas, {len(agent.reports)} analisadas.")
    st.rerun()


# === RESULTADOS ===
all_reports = st.session_state.get("reports", [])
df = pd.DataFrame(all_reports)
high_impact = df[df['pb_padrao'] | df['duplex'] | df['reposicao'] | df['manutencao']] if not df.empty else pd.DataFrame()

# === MÉTRICAS ===
with metrics_ph.container():
    analyzed_printers = len(all_reports)
    recommendations = len(high_impact)
    active_policies = sum(
        1 for r in all_reports if any(r.get(k, False) for k in ['pb_padrao', 'duplex', 'reposicao', 'manutencao'])
    )

    c1, c2 = st.columns(2)
    c1.metric("Impressoras Analisadas", analyzed_printers)
    c2.metric("Com Recomendações", recommendations)

# === TABELA INTERATIVA NATIVE (st.dataframe) ===
with table_ph.container():
    if not high_impact.empty:
        df_display = high_impact[['id', 'model', 'insights', 'pb_padrao', 'duplex', 'reposicao', 'manutencao']].copy()
        df_display.columns = [
            'Serial Number', 'Modelo', 'Insights',
            'P&B padrão', 'Ativar duplex', 'Reposição Suprimento', 'Manutenção'
        ]

        # Formata insights
        df_display['Insights'] = df_display['Insights'].apply(lambda x: " | ".join(x) if x else "Nenhum")

        # Substitui boolean por símbolo unicode ✅ (ordenável)
        def mark_symbol(value):
            return "✅" if value else ""

        for col in ['P&B padrão', 'Ativar duplex', 'Reposição Suprimento', 'Manutenção']:
            df_display[col] = df_display[col].apply(mark_symbol)

        st.dataframe(
            df_display,
            use_container_width=True,
            hide_index=True
        )

    elif all_reports:
        st.info("Nenhuma impressora com recomendações.")
    else:
        st.info("Clique em 'Analisar Parque' para começar.")

# === POLÍTICAS ===
with policies_ph.container():
    active = []
    if any(r.get('pb_padrao', False) for r in all_reports): active.append("P&B padrão")
    if any(r.get('duplex', False) for r in all_reports): active.append("Ativar duplex")
    if any(r.get('reposicao', False) for r in all_reports): active.append("Reposição Suprimento")
    if any(r.get('manutencao', False) for r in all_reports): active.append("Manutenção")

    if active:
        st.markdown("**Políticas:** " + " • ".join(active))
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

