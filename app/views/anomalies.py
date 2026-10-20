import streamlit as st
import plotly.graph_objects as go
from app.db_connector import load_config, load_local_metric_data, save_config, GRUPOS_ML_DISPONIVEIS
from app.ml_engine import montar_dataset, detectar_anomalias

LABELS_GRUPOS_ML = {
    "cpu": "CPU / Memória (PLE, buffer cache, lazy writes)",
    "memoria": "Memory Clerks",
    "io": "I/O de Disco",
    "atividade_sql": "Atividade SQL (batch requests, deadlocks, bloqueios)",
}


def render_anomalies():
    st.header("🤖 Detecção de Anomalias de Desempenho (Machine Learning)")
    st.markdown(
        """
        Este módulo usa o **Isolation Forest** (*scikit-learn*) treinado sobre os grupos de
        métricas selecionados para o servidor, combinados numa grade de 1 minuto. Ele sinaliza
        **combinações** atípicas — ex: PLE caindo + latência subindo + sessões bloqueadas ao
        mesmo tempo — mesmo quando cada métrica isolada ainda está dentro do limite.
        """
    )

    config = load_config()
    servers = config.get("servers", [])
    if not servers:
        st.warning("Nenhum servidor cadastrado. Acesse 'Gerenciar Servidores' para adicionar.")
        return

    col1, col2 = st.columns([2, 2])
    with col1:
        server_aliases = [s.get("alias") for s in servers]
        selected_alias = st.selectbox("Selecione o Servidor para Análise", server_aliases)
        selected_idx = server_aliases.index(selected_alias)
        selected_server = servers[selected_idx]
    with col2:
        contamination = st.slider(
            "Sensibilidade / Taxa de Contaminação (%)",
            min_value=1, max_value=15, value=5,
            help="Percentual estimado de anomalias esperadas na base histórica."
        ) / 100.0

    grupos_atuais = selected_server.get("metricas_ml", GRUPOS_ML_DISPONIVEIS)
    grupos_selecionados = st.multiselect(
        "Grupos de métricas considerados pelo modelo",
        options=GRUPOS_ML_DISPONIVEIS,
        default=grupos_atuais,
        format_func=lambda g: LABELS_GRUPOS_ML.get(g, g),
    )
    if st.button("💾 Salvar como padrão deste servidor"):
        servers[selected_idx]["metricas_ml"] = grupos_selecionados
        config["servers"] = servers
        save_config(config)
        st.success("Grupos de métricas salvos para este servidor.")

    if not grupos_selecionados:
        st.info("Selecione ao menos um grupo de métricas para treinar o modelo.")
        return

    df_cpu = load_local_metric_data(selected_alias, "cpu_metrics.csv")
    df_mem = load_local_metric_data(selected_alias, "memory_metrics.csv")
    df_io = load_local_metric_data(selected_alias, "io_stats.csv")
    df_ativ = load_local_metric_data(selected_alias, "activity_metrics.csv")

    dataset = montar_dataset(grupos_selecionados, df_cpu=df_cpu, df_mem=df_mem, df_io=df_io, df_ativ=df_ativ)

    if dataset.empty:
        st.info(f"Sem métricas suficientes para o servidor '{selected_alias}' nos grupos selecionados. "
                f"Faça a extração dos CSVs primeiro em 'Gerenciar Servidores'.")
        return

    if len(dataset) < 20:
        st.warning(f"Apenas {len(dataset)} amostras na grade de 1 min. São necessárias pelo menos 20 para calibrar o modelo.")
        return

    with st.spinner("Treinando modelo Isolation Forest e calculando desvios..."):
        df_result = detectar_anomalias(dataset, contamination=contamination)

    total_anomalies = int(df_result["is_anomaly"].sum())
    pct_anomalies = (total_anomalies / len(df_result)) * 100

    st.subheader("⚡ Resumo da Detecção")
    kpi1, kpi2, kpi3 = st.columns(3)
    with kpi1:
        st.metric("Total de Amostras Avaliadas", len(df_result))
    with kpi2:
        st.metric("Padrões Anômalos Detectados", total_anomalies, delta_color="inverse")
    with kpi3:
        st.metric("Percentual de Desvios", f"{pct_anomalies:.1f}%")

    st.divider()

    st.subheader("📈 Linha do Tempo com Marcação de Anomalias")
    metrica_plot = "SQLServerCPU" if "SQLServerCPU" in df_result.columns else df_result.columns[0]
    df_anom = df_result[df_result["is_anomaly"]]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df_result.index, y=df_result[metrica_plot],
        mode="lines", name=metrica_plot,
        line=dict(color="#1f77b4", width=2)
    ))
    if not df_anom.empty:
        fig.add_trace(go.Scatter(
            x=df_anom.index, y=df_anom[metrica_plot],
            mode="markers", name="Anomalia Detectada",
            marker=dict(color="#FF3366", size=10, symbol="x", line=dict(width=2, color="white")),
            hovertext=df_anom.apply(lambda r: f"Principal desvio: {r['metrica_destaque']}", axis=1),
            hoverinfo="text"
        ))
    fig.update_layout(
        title=f"Detecção de Anomalias de Performance - {selected_alias}",
        xaxis_title="Data / Hora", yaxis_title=metrica_plot, hovermode="closest"
    )
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("🚨 Detalhes das Anomalias Encontradas")
    if not df_anom.empty:
        cols_metrica = [c for c in df_anom.columns if c not in ("is_anomaly", "anomaly_score", "metrica_destaque")]
        tab = df_anom[["metrica_destaque"] + cols_metrica].copy()
        tab.insert(0, "momento", tab.index.strftime("%d/%m %H:%M"))
        st.dataframe(tab.reset_index(drop=True).round(2), use_container_width=True)
    else:
        st.success("Nenhuma anomalia crítica encontrada com os parâmetros atuais!")
