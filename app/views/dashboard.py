import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import timedelta
from app.db_connector import (load_config, load_local_metric_data, carregar_janela,
                              extract_metrics_to_csv)

# ----------------------------------------------------------------
# Seletor de janela de tempo (estilo "Real Time: <data> ▾" com
# navegação ◄ ► entre fragmentos temporais)
# ----------------------------------------------------------------
JANELAS_PRESET = [
    ("Últimos 15 minutos", timedelta(minutes=15)),
    ("Últimos 30 minutos", timedelta(minutes=30)),
    ("Última hora", timedelta(hours=1)),
    ("Últimas 2 horas", timedelta(hours=2)),
    ("Últimas 4 horas", timedelta(hours=4)),
    ("Últimas 6 horas", timedelta(hours=6)),
    ("Últimas 8 horas", timedelta(hours=8)),
    ("Último dia", timedelta(days=1)),
    ("Últimos 5 dias", timedelta(days=5)),
]

MESES_PT = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
            "agosto", "setembro", "outubro", "novembro", "dezembro"]
DIAS_PT = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
           "sexta-feira", "sábado", "domingo"]


def _fmt_data_extenso(ts):
    return f"{DIAS_PT[ts.weekday()]}, {ts.day} de {MESES_PT[ts.month - 1]} de {ts.year} {ts.strftime('%H:%M')}"


def _fmt_duracao(td):
    """Formata um timedelta como '2 dias e 3 horas', '1 hora', '15 minutos' etc."""
    total_min = int(td.total_seconds() // 60)
    dias, resto_min = divmod(total_min, 24 * 60)
    horas, minutos = divmod(resto_min, 60)

    partes = []
    if dias:
        partes.append(f"{dias} dia" + ("s" if dias != 1 else ""))
    if horas:
        partes.append(f"{horas} hora" + ("s" if horas != 1 else ""))
    if minutos and not dias:  # em janelas de vários dias, omite os minutos residuais
        partes.append(f"{minutos} minuto" + ("s" if minutos != 1 else ""))

    if not partes:
        return "menos de 1 minuto"
    if len(partes) == 1:
        return partes[0]
    return " e ".join([", ".join(partes[:-1]), partes[-1]])


def render_seletor_janela_tempo(max_date_available, min_date_available, key_prefix, colunas=None):
    """Renderiza o controle ◄ [Tempo Real: <data> ▾] ► e devolve (min_filter, max_filter)."""
    state_key = f"janela_tempo__{key_prefix}"
    if state_key not in st.session_state:
        st.session_state[state_key] = {
            "modo": "realtime",       # "realtime" | "preset" | "custom" | "custom_edit"
            "duracao": timedelta(hours=1),
            "momento_ref": max_date_available,
            "custom_ini": min_date_available,
            "custom_fim": max_date_available,
        }
    state = st.session_state[state_key]

    if colunas is not None:
        col_prev, col_label, col_next = colunas
        espacador = False
    else:
        col_prev, col_label, col_next = st.columns([0.5, 5, 0.5])
        espacador = True

    with col_prev:
        if espacador:
            st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
        if st.button("◄", key=f"{key_prefix}_btn_prev", use_container_width=True,
                     help="Fragmento de tempo anterior"):
            if state["modo"] == "custom":
                duracao = state["custom_fim"] - state["custom_ini"]
                state["custom_fim"] = state["custom_fim"] - duracao
                state["custom_ini"] = state["custom_ini"] - duracao
            else:
                base = state["momento_ref"] if state["modo"] == "preset" else max_date_available
                state["momento_ref"] = base - state["duracao"]
                state["modo"] = "preset"

    with col_next:
        if espacador:
            st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
        no_futuro = state["modo"] == "realtime"
        if st.button("►", key=f"{key_prefix}_btn_next", use_container_width=True,
                     disabled=no_futuro, help="Próximo fragmento de tempo"):
            if state["modo"] == "custom":
                duracao = state["custom_fim"] - state["custom_ini"]
                novo_fim = state["custom_fim"] + duracao
                if novo_fim >= max_date_available:
                    state["custom_fim"] = max_date_available
                    state["custom_ini"] = max_date_available - duracao
                else:
                    state["custom_fim"] = novo_fim
                    state["custom_ini"] = state["custom_ini"] + duracao
            else:
                novo_momento = state["momento_ref"] + state["duracao"]
                if novo_momento >= max_date_available:
                    state["modo"] = "realtime"
                    state["momento_ref"] = max_date_available
                else:
                    state["momento_ref"] = novo_momento

    with col_label:
        if state["modo"] == "realtime":
            rotulo = f"🔴 Tempo Real: {_fmt_data_extenso(max_date_available)}"
        elif state["modo"] == "custom":
            rotulo = (f"🗓️ {state['custom_ini'].strftime('%d/%m/%Y %H:%M')} — "
                      f"{state['custom_fim'].strftime('%d/%m/%Y %H:%M')}")
        else:
            rotulo = f"🕘 {_fmt_data_extenso(state['momento_ref'])}"

        with st.popover(rotulo, use_container_width=True):
            for nome, duracao in JANELAS_PRESET:
                if st.button(f"Mostrar {nome.lower()}", key=f"{key_prefix}_preset_{nome}", use_container_width=True):
                    state["modo"] = "preset"
                    state["duracao"] = duracao
                    state["momento_ref"] = max_date_available
                    st.rerun()
            st.divider()
            if st.button("Inserir intervalo personalizado", key=f"{key_prefix}_custom_open", use_container_width=True):
                state["modo"] = "custom_edit"
                st.rerun()
            st.divider()
            if st.button("🔴 Tempo Real", key=f"{key_prefix}_realtime", use_container_width=True):
                state["modo"] = "realtime"
                state["momento_ref"] = max_date_available
                st.rerun()

    if state["modo"] == "custom_edit":
        c_d1, c_t1, c_d2, c_t2, c_ok = st.columns([1.5, 1, 1.5, 1, 1])
        with c_d1:
            start_date = st.date_input("Data Inicial", value=state["custom_ini"].date(), key=f"{key_prefix}_dt_ini")
        with c_t1:
            start_time = st.time_input("Hora Inicial", value=state["custom_ini"].time().replace(second=0, microsecond=0), key=f"{key_prefix}_tm_ini")
        with c_d2:
            end_date = st.date_input("Data Final", value=state["custom_fim"].date(), key=f"{key_prefix}_dt_fim")
        with c_t2:
            end_time = st.time_input("Hora Final", value=state["custom_fim"].time().replace(second=59, microsecond=0), key=f"{key_prefix}_tm_fim")
        with c_ok:
            st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
            if st.button("Aplicar", key=f"{key_prefix}_custom_apply", use_container_width=True):
                state["custom_ini"] = pd.to_datetime(f"{start_date} {start_time}")
                state["custom_fim"] = pd.to_datetime(f"{end_date} {end_time}")
                state["modo"] = "custom"
                st.rerun()

    if state["modo"] == "custom":
        janela_ini, janela_fim = state["custom_ini"], state["custom_fim"]
    else:
        momento_ref = max_date_available if state["modo"] == "realtime" else state["momento_ref"]
        janela_ini, janela_fim = momento_ref - state["duracao"], momento_ref

    st.caption(
        f"⏱️ Fragmento selecionado: **{_fmt_duracao(janela_fim - janela_ini)}** "
        f"({janela_ini.strftime('%d/%m/%Y %H:%M')} — {janela_fim.strftime('%d/%m/%Y %H:%M')})"
    )

    return janela_ini, janela_fim

# ----------------------------------------------------------------
# Limites de alerta e orientações (Camada 1 — regras fixas)
# ----------------------------------------------------------------
LIMITES = {
    "ple_segundos":         {"aviso": 1000, "critico": 300,  "direcao": "abaixo"},
    "buffer_cache_hit_pct": {"aviso": 97,   "critico": 95,   "direcao": "abaixo"},
    "cpu_sql_pct":          {"aviso": 70,   "critico": 90,   "direcao": "acima"},
    "cpu_total_pct":        {"aviso": 75,   "critico": 90,   "direcao": "acima"},
    "latencia_leitura_ms":  {"aviso": 15,   "critico": 25,   "direcao": "acima"},
    "latencia_escrita_ms":  {"aviso": 15,   "critico": 25,   "direcao": "acima"},
    "sessoes_bloqueadas":   {"aviso": 1,    "critico": 5,    "direcao": "acima"},
    "espaco_livre_pct":     {"aviso": 20,   "critico": 10,   "direcao": "abaixo"},
}

ORIENTACOES = {
    "ple_segundos": {
        "critico": "PLE crítico: o servidor está descartando páginas da memória com muita frequência. "
                   "Verifique queries com alto consumo de memória ou se o servidor precisa de mais RAM.",
        "aviso":   "PLE abaixo do ideal. Monitore o crescimento da carga de trabalho."
    },
    "buffer_cache_hit_pct": {
        "critico": "Taxa de acerto do cache crítica: muitas leituras estão indo ao disco. "
                   "Avalie o consumo de memória e otimize os índices mais acessados.",
        "aviso":   "Taxa de acerto do cache levemente abaixo do ideal."
    },
    "cpu_sql_pct": {
        "critico": "CPU do SQL Server em nível crítico. Identifique as queries no topo da aba "
                   "'Top Queries' (classe A) e verifique planos de execução.",
        "aviso":   "CPU do SQL Server elevada. Monitore se o pico é passageiro ou persistente."
    },
    "cpu_total_pct": {
        "critico": "CPU total do servidor em nível crítico.",
        "aviso":   "CPU total do servidor elevada."
    },
    "latencia_leitura_ms": {
        "critico": "Latência de leitura crítica (>25ms). Verifique a saúde do disco e I/O do servidor.",
        "aviso":   "Latência de leitura acima do recomendado (>15ms)."
    },
    "latencia_escrita_ms": {
        "critico": "Latência de escrita crítica (>25ms). Verifique a saúde do disco, VLFs do log "
                   "e se o log de transações precisa de crescimento.",
        "aviso":   "Latência de escrita acima do recomendado (>15ms)."
    },
    "sessoes_bloqueadas": {
        "critico": "Múltiplas sessões bloqueadas. Verifique a aba 'Atividade SQL' e identifique "
                   "a sessão cabeça do bloqueio.",
        "aviso":   "Há sessões bloqueadas no momento."
    },
    "espaco_livre_pct": {
        "critico": "Espaço livre em disco crítico (<10%). Tome ação imediata: expanda o arquivo, "
                   "libere espaço ou adicione disco.",
        "aviso":   "Espaço livre em disco abaixo de 20%. Planeje expansão."
    },
}


def cor_metrica(valor, metrica_key):
    if pd.isna(valor) or metrica_key not in LIMITES:
        return "normal"
    limite = LIMITES[metrica_key]
    if limite["direcao"] == "abaixo":
        if valor <= limite["critico"]:
            return "critico"
        if valor <= limite["aviso"]:
            return "aviso"
    else:
        if valor >= limite["critico"]:
            return "critico"
        if valor >= limite["aviso"]:
            return "aviso"
    return "normal"


def card_metrica(label, valor, unidade, metrica_key, fmt="{:.0f}"):
    if pd.isna(valor):
        st.metric(label, "N/A")
        return
    status = cor_metrica(valor, metrica_key)
    cor = {"normal": "#28a745", "aviso": "#ffc107", "critico": "#dc3545"}.get(status, "#28a745")
    texto = fmt.format(valor) + " " + unidade
    st.markdown(
        f"""<div style='border-left: 5px solid {cor}; padding: 8px 12px; margin-bottom: 8px;
                        background:rgba(128,128,128,0.08); border-radius:4px;'>
            <span style='opacity:0.7; font-size:0.85em'>{label}</span><br>
            <span style='color:{cor}; font-size:1.4em; font-weight:bold'>{texto}</span>
        </div>""",
        unsafe_allow_html=True
    )
    if status != "normal" and metrica_key in ORIENTACOES:
        st.warning(ORIENTACOES[metrica_key][status])


def render_dashboard():
    st.markdown(
        "<div class='titulo-painel'>📊 Painel de Observabilidade SQL Server</div>",
        unsafe_allow_html=True,
    )
    config = load_config()
    servers = config.get("servers", [])

    if not servers:
        st.warning("Nenhum servidor cadastrado. Acesse o menu 'Gerenciar Servidores' para adicionar.")
        return

    # Faixa de controles: servidor | navegacao da janela de tempo | sincronizar
    col_srv, col_prev, col_label, col_next, col_sync = st.columns(
        [1.7, 0.32, 3.1, 0.32, 1.25], vertical_alignment="center"
    )

    with col_srv:
        server_aliases = [s.get("alias") for s in servers]
        selected_alias = st.selectbox(
            "Servidor", server_aliases, label_visibility="collapsed",
            format_func=lambda a: f"🖥️  {a}",
        )
        selected_server_config = next((s for s in servers if s.get("alias") == selected_alias), None)

    with col_sync:
        with st.popover("🔄 Sincronizar", use_container_width=True):
            st.caption("Traz do servidor apenas as coletas novas desde a última vez.")
            if st.button("🔄 Sincronizar novidades", key="sync_incremental",
                         use_container_width=True, type="primary"):
                if selected_server_config:
                    with st.spinner(f"Buscando coletas novas de {selected_alias}..."):
                        resumo = extract_metrics_to_csv(selected_server_config, selected_alias)
                    st.session_state["resumo_sincronizacao"] = resumo
                    st.rerun()
            st.divider()
            st.caption(
                "A reextração completa descarta os CSVs locais e baixa todo o "
                "histórico de novo. Use apenas para conferência — no Prod pode "
                "levar vários minutos."
            )
            if st.button("⚠️ Reextrair tudo", key="sync_completo",
                         use_container_width=True):
                if selected_server_config:
                    with st.spinner(f"Reextraindo todo o histórico de {selected_alias}..."):
                        resumo = extract_metrics_to_csv(selected_server_config, selected_alias,
                                                        incremental=False)
                    st.session_state["resumo_sincronizacao"] = resumo
                    st.rerun()

    resumo = st.session_state.pop("resumo_sincronizacao", None)
    if resumo:
        erros = [f"{k}: {v}" for k, v in resumo.items() if str(v).startswith("Erro")]
        if erros:
            st.error("Falhas na sincronização — " + " | ".join(erros))
        sucessos = [f"{k.replace('.csv', '')} {v}" for k, v in resumo.items()
                    if not str(v).startswith("Erro")]
        if sucessos:
            st.success("Sincronização concluída: " + ", ".join(sucessos))

    # Arquivos pequenos: carregados por inteiro (df_cpu define a janela disponível).
    df_cpu = load_local_metric_data(selected_alias, "cpu_metrics.csv")
    df_ativ = load_local_metric_data(selected_alias, "activity_metrics.csv")
    df_disks = load_local_metric_data(selected_alias, "disk_metrics.csv")
    df_jobs = load_local_metric_data(selected_alias, "failed_jobs.csv")

    if df_cpu.empty and df_disks.empty and df_jobs.empty:
        st.info(f"Nenhum dado local encontrado para o servidor '{selected_alias}'. Clique no botão '🔄 Sincronizar' acima para extrair.")
        return

    if not df_cpu.empty:
        max_date_available = df_cpu["DataColeta"].max()
        min_date_available = df_cpu["DataColeta"].min()
    else:
        max_date_available = pd.Timestamp.now()
        min_date_available = max_date_available - timedelta(days=7)

    min_filter_date, max_filter_date = render_seletor_janela_tempo(
        max_date_available, min_date_available, selected_alias,
        colunas=(col_prev, col_label, col_next),
    )

    # Arquivos grandes: lidos apenas no trecho correspondente à janela.
    df_mem = carregar_janela(selected_alias, "memory_metrics.csv", min_filter_date, max_filter_date)
    df_waits = carregar_janela(selected_alias, "wait_stats.csv", min_filter_date, max_filter_date)
    df_io = carregar_janela(selected_alias, "io_stats.csv", min_filter_date, max_filter_date)
    df_queries = carregar_janela(selected_alias, "top_queries.csv", min_filter_date, max_filter_date)

    def filter_by_date(df, col_name="DataColeta"):
        if df.empty or min_filter_date is None or max_filter_date is None:
            return df
        if col_name in df.columns:
            if not pd.api.types.is_datetime64_any_dtype(df[col_name]):
                df[col_name] = pd.to_datetime(df[col_name], errors="coerce")
            return df[(df[col_name] >= min_filter_date) & (df[col_name] <= max_filter_date)]
        return df

    df_cpu = filter_by_date(df_cpu)
    df_ativ = filter_by_date(df_ativ)
    df_mem = filter_by_date(df_mem)
    df_waits = filter_by_date(df_waits)
    df_io = filter_by_date(df_io)
    df_queries = filter_by_date(df_queries)
    df_disks = filter_by_date(df_disks)
    df_jobs = filter_by_date(df_jobs, "DataExecucao")

    # ---- KPIs e cartões de alerta ----
    st.subheader("⚡ Visão Geral e Indicadores Críticos")
    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    with kpi1:
        if not df_cpu.empty:
            ult = df_cpu.iloc[-1]
            card_metrica("CPU SQL Server", ult["SQLServerCPU"], "%", "cpu_sql_pct", "{:.1f}")
        else:
            st.metric("CPU SQL Server", "N/A")
    with kpi2:
        if not df_cpu.empty:
            ult = df_cpu.iloc[-1]
            card_metrica("PLE", ult["PageLifeExpectancy"], "s", "ple_segundos", "{:.0f}")
        else:
            st.metric("PLE", "N/A")
    with kpi3:
        if not df_ativ.empty:
            ult = df_ativ.iloc[-1]
            card_metrica("Sessões Bloqueadas", ult["SessoesBloqueadas"], "", "sessoes_bloqueadas", "{:.0f}")
        else:
            st.metric("Sessões Bloqueadas", "N/A")
    with kpi4:
        failed_count = len(df_jobs) if not df_jobs.empty else 0
        st.metric("Falhas de Jobs (no período)", failed_count, delta_color="inverse")

    if not df_disks.empty:
        ult_disk = df_disks[df_disks["DataColeta"] == df_disks["DataColeta"].max()]
        criticos = ult_disk[ult_disk["FreePercent"] < LIMITES["espaco_livre_pct"]["critico"]]
        if not criticos.empty:
            st.error(f"Espaço crítico nos volumes: {', '.join(criticos['VolumeMountPoint'].unique())}")

    if not df_jobs.empty:
        st.error(f"{len(df_jobs)} falha(s) de job registrada(s) no período.")

    st.divider()

    tab_visao, tab_perf, tab_ativ, tab_storage, tab_waits, tab_queries, tab_ops = st.tabs([
        "🗂️ Visão Geral",
        "📈 CPU & Performance", "🧵 Atividade SQL", "💾 Memória & Discos",
        "⏳ Wait Statistics", "🔥 Top Queries (ABC)", "🛡️ Falhas de Jobs"
    ])

    with tab_visao:
        from app.views.visao_geral import render_visao_geral
        render_visao_geral(df_cpu, df_ativ, df_mem, df_waits, df_io, df_disks,
                           df_jobs, df_queries, LIMITES)

    with tab_perf:
        if not df_cpu.empty:
            fig_cpu = px.line(
                df_cpu, x="DataColeta", y="SQLServerCPU",
                title="Histórico de Utilização de CPU do SQL Server (%)",
                labels={"SQLServerCPU": "% CPU", "DataColeta": "Data/Hora"},
            )
            fig_cpu.update_yaxes(rangemode="tozero")
            fig_cpu.add_hline(y=90, line_dash="dash", line_color="red", annotation_text="Crítico 90%")
            fig_cpu.add_hline(y=70, line_dash="dash", line_color="orange", annotation_text="Aviso 70%")
            st.plotly_chart(fig_cpu, use_container_width=True)

            c_ple, c_bchr = st.columns(2)
            with c_ple:
                fig_ple = px.line(df_cpu, x="DataColeta", y="PageLifeExpectancy", title="Page Life Expectancy (s)")
                fig_ple.add_hline(y=300, line_dash="dash", line_color="red")
                fig_ple.add_hline(y=1000, line_dash="dash", line_color="orange")
                st.plotly_chart(fig_ple, use_container_width=True)
            with c_bchr:
                fig_bchr = px.line(df_cpu, x="DataColeta", y="BufferCacheHitRatio", title="Buffer Cache Hit Ratio (%)")
                fig_bchr.add_hline(y=95, line_dash="dash", line_color="red")
                fig_bchr.add_hline(y=97, line_dash="dash", line_color="orange")
                st.plotly_chart(fig_bchr, use_container_width=True)

            if "LazyWritesPeriodo" in df_cpu.columns:
                fig_lazy = px.line(df_cpu, x="DataColeta", y="LazyWritesPeriodo", title="Lazy Writes no Período (entre coletas)")
                st.plotly_chart(fig_lazy, use_container_width=True)
        else:
            st.info("Sem dados de CPU para o período selecionado.")

    with tab_ativ:
        if not df_ativ.empty:
            fig_batch = px.line(df_ativ, x="DataColeta", y="BatchRequestsPeriodo", title="Batch Requests no Período (entre coletas)")
            st.plotly_chart(fig_batch, use_container_width=True)

            c1, c2 = st.columns(2)
            with c1:
                fig_conn = px.line(
                    df_ativ, x="DataColeta", y=["ConexoesAtivas", "SessoesBloqueadas"],
                    title="Conexões Ativas e Sessões Bloqueadas",
                )
                fig_conn.add_hline(y=5, line_dash="dash", line_color="red", annotation_text="Bloqueios crítico")
                st.plotly_chart(fig_conn, use_container_width=True)
            with c2:
                fig_comp = px.line(
                    df_ativ, x="DataColeta", y=["SqlCompilationsPeriodo", "SqlReCompilationsPeriodo", "DeadlocksPeriodo"],
                    title="Compilações, Recompilações e Deadlocks no Período",
                )
                st.plotly_chart(fig_comp, use_container_width=True)
        else:
            st.info("Sem dados de atividade SQL para o período selecionado.")

    with tab_storage:
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            st.write("##### Alocação de Memória (Memory Clerks)")
            if not df_mem.empty:
                latest_mem = df_mem[df_mem["DataColeta"] == df_mem["DataColeta"].max()]
                fig_mem = px.pie(latest_mem, values="MemoriaMB", names="ClerkType", title="Distribuição de Memória (MB)")
                st.plotly_chart(fig_mem, use_container_width=True)
            else:
                st.info("Sem dados de memória para o período selecionado.")

        with col_m2:
            st.write("##### Capacidade de Discos")
            if not df_disks.empty:
                latest_disks = df_disks[df_disks["DataColeta"] == df_disks["DataColeta"].max()]
                fig_disk = px.bar(
                    latest_disks, x="VolumeMountPoint", y=["AvailableGB", "TotalGB"],
                    barmode="group", title="Espaço em Disco (GB)",
                    labels={"value": "GB", "VolumeMountPoint": "Ponto de Montagem"}
                )
                st.plotly_chart(fig_disk, use_container_width=True)
                for _, row in latest_disks.iterrows():
                    status = cor_metrica(row["FreePercent"], "espaco_livre_pct")
                    if status != "normal":
                        st.warning(f"{row['VolumeMountPoint']}: {row['FreePercent']:.1f}% livre — {ORIENTACOES['espaco_livre_pct'][status]}")
            else:
                st.info("Sem dados de disco para o período selecionado.")

    with tab_waits:
        if not df_waits.empty:
            top_waits = df_waits.groupby("WaitType")["DeltaWaitTimeMs"].sum().sort_values(ascending=False).head(8).index
            df_waits_filtered = df_waits[df_waits["WaitType"].isin(top_waits)]
            fig_waits = px.bar(
                df_waits_filtered, x="DataColeta", y="DeltaWaitTimeMs", color="WaitType",
                title="Top Waits no Período (Delta ms)", labels={"DeltaWaitTimeMs": "Tempo de Espera (ms)"}
            )
            st.plotly_chart(fig_waits, use_container_width=True)
        else:
            st.info("Sem dados de Wait Statistics para o período selecionado.")

        if not df_io.empty:
            # sys.dm_io_virtual_file_stats e cumulativa desde a inicializacao da
            # instancia: ReadLatencyMs/WriteLatencyMs sao a latencia MEDIA desde
            # que o servidor subiu, e nao a do intervalo. Comparar essa media
            # contra um limiar instantaneo faz o alerta disparar permanentemente.
            # As colunas _Periodo vem da coleta corrigida (sql/05); quando
            # existirem, sao elas que valem para grafico e para alerta.
            tem_periodo = "ReadLatencyPeriodoMs" in df_io.columns
            col_read = "ReadLatencyPeriodoMs" if tem_periodo else "ReadLatencyMs"
            col_write = "WriteLatencyPeriodoMs" if tem_periodo else "WriteLatencyMs"

            st.write("##### Latência de I/O por Arquivo")
            if not tem_periodo:
                st.caption(
                    "Os registros deste período são anteriores à correção da coleta "
                    "e trazem a latência **média acumulada desde a inicialização da "
                    "instância**, não a do intervalo. Os limiares não se aplicam a "
                    "ela e os alertas foram suprimidos. Execute sql/05 para passar "
                    "a coletar a latência por intervalo."
                )
            fig_io = px.line(
                df_io, x="DataColeta", y=[col_read, col_write], color="DatabaseName",
                title="Latência de Leitura/Escrita (ms)"
                      + ("" if tem_periodo else " — média acumulada, não do intervalo"),
            )
            st.plotly_chart(fig_io, use_container_width=True)

            if tem_periodo:
                latest_io = df_io[df_io["DataColeta"] == df_io["DataColeta"].max()]
                for _, row in latest_io.iterrows():
                    if pd.isna(row[col_read]) and pd.isna(row[col_write]):
                        continue   # sem E/S no intervalo: latencia indefinida
                    if pd.notna(row[col_read]):
                        s_read = cor_metrica(row[col_read], "latencia_leitura_ms")
                        if s_read != "normal":
                            st.warning(f"{row['DatabaseName']} ({row['FileType']}): leitura {row[col_read]:.0f}ms — {ORIENTACOES['latencia_leitura_ms'][s_read]}")
                    if pd.notna(row[col_write]):
                        s_write = cor_metrica(row[col_write], "latencia_escrita_ms")
                        if s_write != "normal":
                            st.warning(f"{row['DatabaseName']} ({row['FileType']}): escrita {row[col_write]:.0f}ms — {ORIENTACOES['latencia_escrita_ms'][s_write]}")

    with tab_queries:
        if not df_queries.empty:
            st.caption(
                "Classificação ABC por volume de execução: A = concentra até 80% das execuções; "
                "B = até 95%; C = cauda longa. Ordenação pelas métricas da ÚLTIMA execução."
            )
            ult_coleta = df_queries["DataColeta"].max()
            df_u = df_queries[df_queries["DataColeta"] == ult_coleta]

            classes = sorted(df_u["ClasseABC"].dropna().unique().tolist())
            grupo = st.radio("Grupo (classe ABC)", ["Todas"] + classes, horizontal=True)
            if grupo != "Todas":
                df_u = df_u[df_u["ClasseABC"] == grupo]

            cols = ["ClasseABC", "QueryText", "ExecutionCount", "LastWorkerTimeMs", "LastLogicalReads",
                    "LastElapsedTimeMs", "AvgWorkerTimeMs", "AvgLogicalReads", "AvgElapsedTimeMs"]
            for cat, col_ord in [("CPU", "LastWorkerTimeMs"), ("IO", "LastLogicalReads"), ("DURACAO", "LastElapsedTimeMs")]:
                st.subheader(f"Top por {cat}")
                df_cat = df_u[df_u["Categoria"] == cat].sort_values(col_ord, ascending=False)
                if df_cat.empty:
                    st.caption("Sem queries nesta categoria/grupo na última coleta.")
                else:
                    st.dataframe(df_cat[cols], use_container_width=True)
        else:
            st.info("Sem registros de queries para o período selecionado.")

    with tab_ops:
        st.write("##### Histórico de Falhas em Jobs do Agent")
        if not df_jobs.empty:
            st.dataframe(df_jobs[[
                "JobName", "StepName", "DataExecucao", "DuracaoSegundos", "MensagemErro"
            ]].sort_values("DataExecucao", ascending=False), use_container_width=True)
        else:
            st.success("Nenhuma falha de Job registrada no período selecionado!")
