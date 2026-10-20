"""Tela de resultado do experimento de injeção controlada de falhas.

Mostra a telemetria coletada com as janelas de injeção marcadas sobre a série,
de modo que se possa verificar visualmente se a coleta captura o efeito das
falhas provocadas. Esta tela apenas lê os CSVs já extraídos em data/<alias>/;
não abre conexão com nenhuma instância SQL Server.
"""
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.db_connector import DATA_DIR, load_config, load_local_metric_data
from app.views.dashboard import LIMITES

# Métrica exibida -> (arquivo de origem, coluna, unidade, chave em LIMITES)
METRICAS = {
    "CPU do SQL Server (%)": ("cpu_metrics.csv", "SQLServerCPU", "%", "cpu_sql_pct"),
    "Expectativa de vida de página (s)": ("cpu_metrics.csv", "PageLifeExpectancy", "s", "ple_segundos"),
    "Taxa de acerto do buffer cache (%)": ("cpu_metrics.csv", "BufferCacheHitRatio", "%", "buffer_cache_hit_pct"),
    "Sessões bloqueadas": ("activity_metrics.csv", "SessoesBloqueadas", "", "sessoes_bloqueadas"),
}

# Cada modo de falha ataca um grupo de métricas; a cor ajuda a ler o gráfico.
CORES_MODO = {
    "CPU": "#e45756",
    "MEMORIA": "#4c78a8",
    "IO": "#f58518",
    "BLOQUEIO": "#72b7b2",
}

MODO_ROTULO = {
    "CPU": "Saturação de processador",
    "MEMORIA": "Pressão de memória",
    "IO": "Saturação de entrada e saída",
    "BLOQUEIO": "Contenção de bloqueio",
}


def listar_logs_injecao(alias):
    """Devolve os arquivos injections*.csv disponíveis para a instância."""
    pasta = os.path.join(DATA_DIR, alias)
    if not os.path.isdir(pasta):
        return []
    achados = [f for f in os.listdir(pasta)
               if f.lower().startswith("injections") and f.lower().endswith(".csv")]
    return sorted(achados)


def carregar_injecoes(alias, arquivo):
    """Lê o log de injeções.

    O export do SSMS em português sai separado por ponto e vírgula e com BOM;
    o export padrão sai por vírgula. Aceita os dois sem exigir conversão manual.
    """
    caminho = os.path.join(DATA_DIR, alias, arquivo)
    if not os.path.exists(caminho):
        return pd.DataFrame()

    df = pd.DataFrame()
    for sep in (";", ","):
        try:
            tentativa = pd.read_csv(caminho, sep=sep, encoding="utf-8-sig")
        except Exception:
            continue
        if len(tentativa.columns) > 1:
            df = tentativa
            break
    if df.empty:
        return df

    df.columns = [str(c).replace("﻿", "").strip() for c in df.columns]
    for col in ("DataInicio", "DataFim"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    if "Status" in df.columns:
        df["Status"] = df["Status"].astype(str).str.strip().str.upper()
    if "ModoFalha" in df.columns:
        df["ModoFalha"] = df["ModoFalha"].astype(str).str.strip().str.upper()

    return df.dropna(subset=[c for c in ("DataInicio", "DataFim") if c in df.columns])


def _minutos_rotulados(serie_index, injecoes):
    """Quantos instantes da série caem dentro de alguma injeção."""
    if injecoes.empty or serie_index.empty:
        return 0
    marcado = pd.Series(False, index=serie_index)
    for _, linha in injecoes.iterrows():
        marcado |= (serie_index >= linha["DataInicio"]) & (serie_index <= linha["DataFim"])
    return int(marcado.sum())


def _pontos_por_modo(serie_index, injecoes):
    """Mapeia cada instante coletado ao modo de falha que estava em curso.

    Devolve duas séries alinhadas ao índice: o modo e a intensidade. Instantes
    fora de qualquer injeção ficam nulos. Quando duas injeções se sobrepõem,
    vale a primeira em ordem de início.
    """
    modo = pd.Series(pd.NA, index=serie_index, dtype=object)
    intensidade = pd.Series(pd.NA, index=serie_index, dtype=object)
    if injecoes.empty or len(serie_index) == 0:
        return modo, intensidade

    tem_intensidade = "Intensidade" in injecoes.columns
    for _, linha in injecoes.sort_values("DataInicio").iterrows():
        dentro = (serie_index >= linha["DataInicio"]) & (serie_index <= linha["DataFim"])
        livre = modo.isna().to_numpy() & dentro
        modo[livre] = str(linha.get("ModoFalha", "?"))
        if tem_intensidade:
            intensidade[livre] = linha.get("Intensidade")
    return modo, intensidade


def _grafico(serie, coluna, rotulo, unidade, chave_limite, injecoes,
             mostrar_limiares, mostrar_faixas=True, mostrar_marcas=True):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=serie.index, y=serie[coluna], mode="lines", name=rotulo,
        line=dict(width=1.4, color="#4c78a8"),
        hovertemplate="%{x|%d/%m %H:%M}<br>%{y:.2f}" + (f" {unidade}" if unidade else "") + "<extra></extra>",
    ))

    if mostrar_faixas:
        for _, linha in injecoes.iterrows():
            modo = linha.get("ModoFalha", "?")
            fig.add_vrect(
                x0=linha["DataInicio"], x1=linha["DataFim"],
                fillcolor=CORES_MODO.get(modo, "#999999"), opacity=0.25,
                line_width=0, layer="below",
            )

    # Um "x" sobre cada ponto coletado durante uma injeção. Em janelas largas as
    # faixas de três minutos ficam finas demais para serem vistas; a marca, não.
    if mostrar_marcas:
        modo_ponto, inten_ponto = _pontos_por_modo(serie.index, injecoes)
        presentes = [m for m in CORES_MODO if (modo_ponto == m).any()]
        presentes += [m for m in modo_ponto.dropna().unique() if m not in CORES_MODO]
        for m in presentes:
            sel = (modo_ponto == m).to_numpy()
            marcados = serie[sel]
            if marcados.empty:
                continue
            intensidades = inten_ponto[sel]
            fig.add_trace(go.Scatter(
                x=marcados.index, y=marcados[coluna], mode="markers",
                name=MODO_ROTULO.get(m, m),
                marker=dict(symbol="x", size=8, color=CORES_MODO.get(m, "#999999"),
                            line=dict(width=1, color=CORES_MODO.get(m, "#999999"))),
                customdata=[["—" if pd.isna(i) else i] for i in intensidades],
                hovertemplate=("%{x|%d/%m %H:%M}<br>" + MODO_ROTULO.get(m, m)
                               + "<br>intensidade %{customdata[0]}"
                               + "<br>%{y:.2f}" + (f" {unidade}" if unidade else "")
                               + "<extra></extra>"),
            ))

    if mostrar_limiares and chave_limite in LIMITES:
        cfg = LIMITES[chave_limite]
        fig.add_hline(y=cfg["aviso"], line_dash="dash", line_color="orange",
                      annotation_text=f"aviso ({cfg['aviso']})", annotation_position="top left")
        fig.add_hline(y=cfg["critico"], line_dash="dash", line_color="red",
                      annotation_text=f"crítico ({cfg['critico']})", annotation_position="bottom left")

    fig.update_layout(
        height=430, margin=dict(l=10, r=10, t=30, b=10),
        xaxis_title="Data / hora", yaxis_title=rotulo,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="left", x=0, title_text=""),
    )
    return fig


def render_experimento():
    st.header("🧪 Resultado do Experimento de Injeção de Falhas")
    st.markdown(
        """
        As faixas coloridas marcam os intervalos em que uma **falha foi provocada de propósito**
        na instância. Se a série da métrica reage dentro dessas faixas, a coleta está capturando
        o efeito — é a verificação visual de que o monitor mede o que se propõe a medir.
        Esta tela apenas lê os CSVs já extraídos; não consulta nenhuma instância.
        """
    )

    config = load_config()
    servers = config.get("servers", [])
    if not servers:
        st.warning("Nenhum servidor cadastrado. Acesse 'Gerenciar Servidores' para adicionar.")
        return

    aliases = [s.get("alias") for s in servers]
    col1, col2, col3 = st.columns([2, 2, 2])
    with col1:
        alias = st.selectbox("Instância", aliases)
    with col2:
        logs = listar_logs_injecao(alias)
        arquivo = None
        if not logs:
            st.selectbox("Log de injeções", ["(nenhum encontrado)"], disabled=True)
        else:
            arquivo = st.selectbox("Log de injeções", logs,
                                   help="Exporte obs.Log_Injecoes para data/<alias>/injections.csv.")
    with col3:
        nome_metrica = st.selectbox("Métrica", list(METRICAS.keys()),
                                    help="Vale para as duas abas desta tela.")

    # As abas são sempre montadas. A calibração depende só da telemetria e
    # funciona em instâncias sem injeção, como as de produção; apenas a
    # marcação das janelas de falha exige o log.
    aba_serie, aba_calib = st.tabs(["📈 Telemetria e injeções", "🎚️ Calibração de limiares"])

    with aba_calib:
        # import tardio: calibracao.py importa deste módulo
        from app.views.calibracao import render_calibracao
        render_calibracao(alias, nome_metrica, arquivo)

    with aba_serie:
        if not logs:
            st.info(
                "Nenhum arquivo `injections*.csv` foi encontrado para esta instância, então não há "
                "janelas de falha para marcar sobre a série. Se você quiser produzir essa verdade de "
                "referência, use o script **04_exportar_log_injecoes.sql**, disponível na tela "
                "'Onboarding do Experimento', e salve o resultado em "
                f"`data/{alias}/injections.csv`.\n\n"
                "A aba **Calibração de limiares** não depende disso: ela deriva o limiar da própria "
                "telemetria e funciona normalmente nesta instância."
            )
        else:
            _render_serie(alias, arquivo, nome_metrica)


def _render_serie(alias, arquivo, nome_metrica):
    arquivo_metrica, coluna, unidade, chave_limite = METRICAS[nome_metrica]
    dados = load_local_metric_data(alias, arquivo_metrica)
    if dados.empty or coluna not in dados.columns:
        st.error(
            f"Não foi possível ler a coluna `{coluna}` em `{arquivo_metrica}`. "
            "Extraia os CSVs desta instância na tela 'Gerenciar Servidores'."
        )
        return

    injecoes = carregar_injecoes(alias, arquivo)
    if injecoes.empty:
        st.error(f"O arquivo `{arquivo}` não pôde ser lido ou não tem linhas válidas.")
        return

    serie = (dados[["DataColeta", coluna]]
             .dropna(subset=["DataColeta"])
             .set_index("DataColeta")
             .sort_index())

    # ---------------- filtros
    modos = sorted(injecoes["ModoFalha"].dropna().unique()) if "ModoFalha" in injecoes.columns else []
    f1, f2, f3 = st.columns([3, 2, 2])
    with f1:
        modos_sel = st.multiselect(
            "Modos de falha", modos, default=modos,
            format_func=lambda m: MODO_ROTULO.get(m, m),
        )
    with f2:
        status_disp = sorted(injecoes["Status"].dropna().unique()) if "Status" in injecoes.columns else []
        padrao = [s for s in status_disp if s == "CONCLUIDA"] or status_disp
        status_sel = st.multiselect("Status", status_disp, default=padrao)
    with f3:
        mostrar_limiares = st.checkbox("Limiares do painel", value=True)
        mostrar_marcas = st.checkbox("Marcar os pontos coletados durante as injeções", value=True)
        mostrar_faixas = st.checkbox("Sombrear as janelas de injeção", value=True,
                                     help="Em períodos longos as faixas de três minutos ficam quase "
                                          "invisíveis; as marcas continuam legíveis.")

    filtradas = injecoes.copy()
    if modos_sel and "ModoFalha" in filtradas.columns:
        filtradas = filtradas[filtradas["ModoFalha"].isin(modos_sel)]
    if status_sel and "Status" in filtradas.columns:
        filtradas = filtradas[filtradas["Status"].isin(status_sel)]
    if filtradas.empty:
        st.warning("Nenhuma injeção atende aos filtros escolhidos.")
        return

    # ---------------- janela exibida
    ini_padrao = filtradas["DataInicio"].min() - pd.Timedelta(hours=2)
    fim_padrao = filtradas["DataFim"].max() + pd.Timedelta(hours=2)
    escopo = st.radio(
        "Período exibido",
        ["Janela das injeções", "Série completa", "Uma injeção por vez"],
        horizontal=True,
    )

    if escopo == "Série completa":
        recorte, titulo = serie, "Série completa"
    elif escopo == "Janela das injeções":
        recorte = serie[(serie.index >= ini_padrao) & (serie.index <= fim_padrao)]
        titulo = "Janela das injeções"
    else:
        opcoes = filtradas.sort_values("DataInicio")
        rotulos = [
            f"{r['DataInicio']:%d/%m %H:%M} — {MODO_ROTULO.get(r.get('ModoFalha'), r.get('ModoFalha'))}"
            + (f" (intensidade {r['Intensidade']})" if "Intensidade" in opcoes.columns else "")
            for _, r in opcoes.iterrows()
        ]
        escolha = st.selectbox("Injeção", range(len(rotulos)), format_func=lambda i: rotulos[i])
        linha = opcoes.iloc[escolha]
        folga = st.slider("Minutos exibidos antes e depois", 5, 60, 15)
        recorte = serie[(serie.index >= linha["DataInicio"] - pd.Timedelta(minutes=folga)) &
                        (serie.index <= linha["DataFim"] + pd.Timedelta(minutes=folga))]
        filtradas = opcoes.iloc[[escolha]]
        titulo = rotulos[escolha]

    if recorte.empty:
        st.warning("Não há telemetria coletada no período selecionado.")
        return

    # ---------------- resumo
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Injeções exibidas", len(filtradas))
    m2.metric("Minutos de série", len(recorte))
    m3.metric("Minutos sob injeção", _minutos_rotulados(recorte.index, filtradas))
    pico = recorte[coluna].max()
    m4.metric(f"Máximo de {nome_metrica.split('(')[0].strip().lower()}",
              f"{pico:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

    st.subheader(f"📈 {nome_metrica} — {titulo}")
    st.plotly_chart(
        _grafico(recorte, coluna, nome_metrica, unidade, chave_limite, filtradas,
                 mostrar_limiares, mostrar_faixas, mostrar_marcas),
        use_container_width=True,
    )

    st.caption(
        "Cada **x** é um instante de coleta que caiu dentro de uma injeção. Se a série reage "
        "onde as marcas estão, a telemetria capturou o efeito da falha provocada."
    )

    with st.expander(f"📋 Injeções exibidas ({len(filtradas)})"):
        colunas = [c for c in ("ID", "ModoFalha", "Intensidade", "DataInicio", "DataFim",
                               "DuracaoRealSeg", "Status", "MensagemErro") if c in filtradas.columns]
        st.dataframe(filtradas[colunas].sort_values("DataInicio"),
                     use_container_width=True, hide_index=True)

    if "ModoFalha" in injecoes.columns and "Status" in injecoes.columns:
        with st.expander("📊 Injeções registradas por modo e status"):
            st.dataframe(
                pd.crosstab(injecoes["ModoFalha"], injecoes["Status"]),
                use_container_width=True,
            )
