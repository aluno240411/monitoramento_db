"""Visão geral: todos os domínios da instância em uma tela só.

Grade de cartões no estilo dos consoles de monitoramento comerciais, para
leitura de relance. Cada cartão responde a uma pergunta, e o detalhe continua
nas abas seguintes do painel.

Recebe os dataframes já carregados e já filtrados pela janela de tempo — não
lê arquivo nem consulta instância.
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ALTURA = 170          # altura dos gráficos da grade
COR_LINHA = "#4c78a8"
COR_ALERTA = "#e45756"
COR_OK = "#54a24b"

# Esperas que não indicam contenção: são de ocioso, de manutenção interna ou de
# espera por trabalho. Somadas, dominam qualquer gráfico e escondem o resto.
ESPERAS_IGNORADAS = {
    "SOS_WORK_DISPATCHER", "DIRTY_PAGE_POLL", "ONDEMAND_TASK_QUEUE", "LAZYWRITER_SLEEP",
    "CHECKPOINT_QUEUE", "REQUEST_FOR_DEADLOCK_SEARCH", "XE_TIMER_EVENT", "XE_DISPATCHER_WAIT",
    "FT_IFTS_SCHEDULER_IDLE_WAIT", "LOGMGR_QUEUE", "BROKER_TO_FLUSH", "BROKER_TASK_STOP",
    "BROKER_EVENTHANDLER", "BROKER_TRANSMITTER", "SLEEP_TASK", "SLEEP_SYSTEMTASK",
    "SQLTRACE_BUFFER_FLUSH", "SQLTRACE_INCREMENTAL_FLUSH_SLEEP", "WAITFOR",
    "HADR_FILESTREAM_IOMGR_IOCOMPLETION", "HADR_WORK_QUEUE", "HADR_CLUSAPI_CALL",
    "DISPATCHER_QUEUE_SEMAPHORE", "QDS_PERSIST_TASK_MAIN_LOOP_SLEEP", "QDS_ASYNC_QUEUE",
    "QDS_SHUTDOWN_QUEUE", "CLR_AUTO_EVENT", "CLR_MANUAL_EVENT", "SP_SERVER_DIAGNOSTICS_SLEEP",
    "DBMIRROR_EVENTS_QUEUE", "DBMIRRORING_CMD", "PREEMPTIVE_XE_GETTARGETSTATE",
    "MEMORY_ALLOCATION_EXT", "PWAIT_ALL_COMPONENTS_INITIALIZED", "PARALLEL_REDO_WORKER_WAIT",
}

# Prefixo do tipo de espera -> categoria exibida
CATEGORIAS_ESPERA = [
    ("LCK_", "Bloqueio"),
    ("PAGEIOLATCH_", "E/S"),
    ("IO_COMPLETION", "E/S"),
    ("ASYNC_IO_COMPLETION", "E/S"),
    ("BACKUPIO", "E/S"),
    ("WRITELOG", "Log"),
    ("LOGBUFFER", "Log"),
    ("PAGELATCH_", "Memória"),
    ("RESOURCE_SEMAPHORE", "Memória"),
    ("CMEMTHREAD", "Memória"),
    ("SOS_SCHEDULER_YIELD", "Processador"),
    ("THREADPOOL", "Processador"),
    ("CXPACKET", "Paralelismo"),
    ("CXCONSUMER", "Paralelismo"),
    ("CXSYNC_", "Paralelismo"),
    ("ASYNC_NETWORK_IO", "Rede/cliente"),
]

CORES_CATEGORIA = {
    "E/S": "#f58518", "Bloqueio": "#e45756", "Memória": "#4c78a8", "Log": "#b279a2",
    "Processador": "#54a24b", "Paralelismo": "#72b7b2", "Rede/cliente": "#eeca3b",
    "Outras": "#9d9d9d",
}


def _categoria_espera(tipo):
    for prefixo, rotulo in CATEGORIAS_ESPERA:
        if str(tipo).startswith(prefixo):
            return rotulo
    return "Outras"


def _base(fig, titulo=None):
    fig.update_layout(
        height=ALTURA, margin=dict(l=8, r=8, t=24 if titulo else 8, b=8),
        title=dict(text=titulo, font=dict(size=12)) if titulo else None,
        showlegend=False, xaxis_title=None, yaxis_title=None,
        hovermode="x unified",
    )
    fig.update_xaxes(showgrid=False)
    return fig


def _sem_dados(msg="Sem dados no período."):
    st.caption(msg)


def _amostrar(df, coluna_data="DataColeta", maximo=1500):
    """Reduz a série para caber no gráfico sem custo de renderização."""
    if len(df) <= maximo:
        return df
    passo = len(df) // maximo + 1
    return df.iloc[::passo]


# ---------------------------------------------------------------- cartões

def _cartao_cpu(df_cpu, df_ativ):
    st.markdown("**🖥️ Processador**")
    if df_cpu.empty:
        _sem_dados(); return
    d = _amostrar(df_cpu)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=d["DataColeta"], y=d["SQLServerCPU"], name="SQL Server",
                             fill="tozeroy", line=dict(width=1, color=COR_LINHA)))
    if "OtherProcessCPU" in d.columns:
        fig.add_trace(go.Scatter(x=d["DataColeta"], y=d["OtherProcessCPU"], name="Outros processos",
                                 fill="tozeroy", line=dict(width=1, color="#9d9d9d")))
    fig.update_yaxes(range=[0, 100], ticksuffix="%")
    st.plotly_chart(_base(fig, "Uso de CPU — SQL Server e outros processos"),
                    use_container_width=True, key="vg_cpu")

    if not df_ativ.empty and "SqlCompilationsPeriodo" in df_ativ.columns:
        a = _amostrar(df_ativ)
        fig2 = go.Figure()
        for col, nome, cor in [("BatchRequestsPeriodo", "Requisições de lote", "#f58518"),
                               ("SqlCompilationsPeriodo", "Compilações", "#4c78a8"),
                               ("SqlReCompilationsPeriodo", "Recompilações", "#e45756")]:
            if col in a.columns:
                fig2.add_trace(go.Scatter(x=a["DataColeta"], y=a[col], name=nome,
                                          line=dict(width=1, color=cor)))
        fig2.update_layout(showlegend=True, legend=dict(orientation="h", y=1.15, font=dict(size=9)))
        st.plotly_chart(_base(fig2, "Lotes, compilações e recompilações por minuto"),
                        use_container_width=True, key="vg_compila")


def _cartao_esperas(df_waits):
    st.markdown("**⏳ Esperas do servidor**")
    if df_waits.empty or "DeltaWaitTimeMs" not in df_waits.columns:
        _sem_dados(); return
    d = df_waits[~df_waits["WaitType"].isin(ESPERAS_IGNORADAS)].copy()
    if d.empty:
        _sem_dados("Apenas esperas ociosas no período."); return
    d["Categoria"] = d["WaitType"].map(_categoria_espera)
    serie = d.groupby(["DataColeta", "Categoria"])["DeltaWaitTimeMs"].sum().reset_index()
    fig = go.Figure()
    for cat in serie["Categoria"].value_counts().index:
        parte = serie[serie["Categoria"] == cat]
        fig.add_trace(go.Scatter(x=parte["DataColeta"], y=parte["DeltaWaitTimeMs"], name=cat,
                                 stackgroup="um", line=dict(width=0.5),
                                 fillcolor=CORES_CATEGORIA.get(cat, "#9d9d9d")))
    fig.update_layout(showlegend=True, legend=dict(orientation="h", y=1.15, font=dict(size=9)))
    st.plotly_chart(_base(fig, "Tempo de espera por categoria (ms no intervalo)"),
                    use_container_width=True, key="vg_waits")

    topo = d.groupby("WaitType")["DeltaWaitTimeMs"].sum().sort_values(ascending=False).head(5)
    if not topo.empty:
        total = topo.sum() or 1
        st.caption("Maiores esperas: " + " · ".join(
            f"{k} ({v / total:.0%})" for k, v in topo.items()))


def _cartao_sessoes(df_ativ):
    st.markdown("**👥 Sessões**")
    if df_ativ.empty:
        _sem_dados(); return
    a = _amostrar(df_ativ)
    fig = go.Figure()
    if "ConexoesAtivas" in a.columns:
        fig.add_trace(go.Scatter(x=a["DataColeta"], y=a["ConexoesAtivas"], name="Conexões ativas",
                                 fill="tozeroy", line=dict(width=1, color=COR_LINHA)))
    if "TransacoesAtivas" in a.columns:
        fig.add_trace(go.Scatter(x=a["DataColeta"], y=a["TransacoesAtivas"], name="Transações ativas",
                                 line=dict(width=1, color="#54a24b")))
    fig.update_layout(showlegend=True, legend=dict(orientation="h", y=1.15, font=dict(size=9)))
    st.plotly_chart(_base(fig, "Conexões e transações"), use_container_width=True, key="vg_sessoes")

    fig2 = go.Figure()
    for col, nome, cor in [("SessoesBloqueadas", "Sessões bloqueadas", COR_ALERTA),
                           ("DeadlocksPeriodo", "Deadlocks", "#b279a2")]:
        if col in a.columns:
            fig2.add_trace(go.Bar(x=a["DataColeta"], y=a[col], name=nome, marker_color=cor))
    fig2.update_layout(barmode="overlay", showlegend=True,
                       legend=dict(orientation="h", y=1.15, font=dict(size=9)))
    st.plotly_chart(_base(fig2, "Bloqueios e deadlocks"), use_container_width=True, key="vg_bloqueio")


def _cartao_memoria(df_cpu, df_mem):
    st.markdown("**🧠 Memória**")
    if df_mem.empty:
        _sem_dados()
    else:
        total = df_mem.groupby("DataColeta")["MemoriaMB"].sum().reset_index()
        maiores = (df_mem.groupby("ClerkType")["MemoriaMB"].sum()
                   .sort_values(ascending=False).head(4).index)
        fig = go.Figure()
        for clerk in maiores:
            parte = df_mem[df_mem["ClerkType"] == clerk]
            fig.add_trace(go.Scatter(x=parte["DataColeta"], y=parte["MemoriaMB"], name=clerk,
                                     stackgroup="um", line=dict(width=0.5)))
        fig.update_layout(showlegend=True, legend=dict(orientation="h", y=1.15, font=dict(size=8)))
        st.plotly_chart(_base(fig, "Principais consumidores de memória (MB)"),
                        use_container_width=True, key="vg_mem")
        if not total.empty:
            st.caption(f"Total alocado na última coleta: {total['MemoriaMB'].iloc[-1]:,.0f} MB"
                       .replace(",", "."))

    if not df_cpu.empty and "LazyWritesPeriodo" in df_cpu.columns:
        d = _amostrar(df_cpu)
        fig2 = go.Figure(go.Scatter(x=d["DataColeta"], y=d["LazyWritesPeriodo"],
                                    fill="tozeroy", line=dict(width=1, color="#b279a2")))
        st.plotly_chart(_base(fig2, "Escritas preguiçosas por minuto"),
                        use_container_width=True, key="vg_lazy")


def _cartao_cache(df_cpu, limites):
    st.markdown("**🗃️ Cache**")
    if df_cpu.empty:
        _sem_dados(); return
    d = _amostrar(df_cpu)
    fig = go.Figure(go.Scatter(x=d["DataColeta"], y=d["PageLifeExpectancy"],
                               fill="tozeroy", line=dict(width=1, color=COR_LINHA)))
    aviso = limites["ple_segundos"]["aviso"]
    fig.add_hline(y=aviso, line_dash="dash", line_color="orange")
    st.plotly_chart(_base(fig, f"Expectativa de vida de página (s) — aviso em {aviso}"),
                    use_container_width=True, key="vg_ple")

    atual = df_cpu["BufferCacheHitRatio"].iloc[-1] if "BufferCacheHitRatio" in df_cpu.columns else None
    if atual is not None and pd.notna(atual):
        atual = min(float(atual), 100.0)
        critico = limites["buffer_cache_hit_pct"]["critico"]
        g = go.Figure(go.Indicator(
            mode="gauge+number", value=atual,
            number={"suffix": "%", "font": {"size": 22}},
            gauge={
                "axis": {"range": [90, 100], "tickfont": {"size": 9}},
                "bar": {"color": COR_OK if atual >= limites["buffer_cache_hit_pct"]["aviso"] else COR_ALERTA},
                "steps": [{"range": [90, critico], "color": "#f4d4d4"},
                          {"range": [critico, 100], "color": "#eef5ec"}],
            },
        ))
        g.update_layout(height=150, margin=dict(l=20, r=20, t=26, b=8),
                        title=dict(text="Taxa de acerto do buffer cache", font=dict(size=12)))
        st.plotly_chart(g, use_container_width=True, key="vg_bchr")


def _cartao_discos(df_disks, limites):
    st.markdown("**💽 Armazenamento**")
    if df_disks.empty:
        _sem_dados(); return
    ultimo = df_disks[df_disks["DataColeta"] == df_disks["DataColeta"].max()]
    ultimo = ultimo.sort_values("FreePercent")
    cores = [COR_ALERTA if p < limites["espaco_livre_pct"]["critico"]
             else ("#f58518" if p < limites["espaco_livre_pct"]["aviso"] else COR_OK)
             for p in ultimo["FreePercent"]]
    fig = go.Figure(go.Bar(
        x=ultimo["FreePercent"], y=ultimo["VolumeMountPoint"], orientation="h",
        marker_color=cores, text=[f"{p:.0f}%" for p in ultimo["FreePercent"]],
        textposition="outside",
    ))
    fig.update_xaxes(range=[0, 100], ticksuffix="%")
    st.plotly_chart(_base(fig, "Espaço livre por volume"), use_container_width=True, key="vg_disco")
    for _, linha in ultimo.iterrows():
        if linha["FreePercent"] < limites["espaco_livre_pct"]["aviso"]:
            st.caption(f"⚠️ {linha['VolumeMountPoint']} — {linha['AvailableGB']:.0f} GB livres "
                       f"de {linha['TotalGB']:.0f} GB")


def _cartao_io(df_io):
    st.markdown("**📀 Entrada e saída por base**")
    if df_io.empty:
        _sem_dados(); return
    tem_periodo = "MBReadPeriodo" in df_io.columns
    col_r = "MBReadPeriodo" if tem_periodo else "MBRead"
    col_w = "MBWrittenPeriodo" if tem_periodo else "MBWritten"
    agg = (df_io.groupby("DatabaseName")[[col_r, col_w]].sum()
           .assign(total=lambda x: x[col_r] + x[col_w])
           .sort_values("total", ascending=False).head(8).iloc[::-1])
    fig = go.Figure()
    fig.add_trace(go.Bar(x=agg[col_r], y=agg.index, orientation="h", name="Leitura",
                         marker_color="#4c78a8"))
    fig.add_trace(go.Bar(x=agg[col_w], y=agg.index, orientation="h", name="Escrita",
                         marker_color="#f58518"))
    fig.update_layout(barmode="stack", showlegend=True,
                      legend=dict(orientation="h", y=1.15, font=dict(size=9)))
    st.plotly_chart(_base(fig, "MB no período" + ("" if tem_periodo else " — acumulado")),
                    use_container_width=True, key="vg_io")

    if "Volume" in df_io.columns and df_io["Volume"].notna().any():
        col_lat = "ReadLatencyPeriodoMs" if tem_periodo else "ReadLatencyMs"
        porvol = df_io.groupby("Volume")[col_lat].max().sort_values(ascending=False)
        st.caption("Maior latência de leitura por volume: " + " · ".join(
            f"{v} {x:.0f} ms" for v, x in porvol.items() if pd.notna(x)))
    else:
        st.caption("Latência por volume indisponível — execute `sql/06_coleta_io_por_volume.sql` "
                   "para passar a gravar o ponto de montagem de cada arquivo.")


def _cartao_operacao(df_jobs, df_queries):
    st.markdown("**🛡️ Operação**")
    if df_jobs.empty:
        st.success("Nenhuma falha de job no período.", icon="✅")
    else:
        st.error(f"{len(df_jobs)} falha(s) de job no período.", icon="🚨")
        colunas = [c for c in ("JobName", "DataExecucao", "MensagemErro") if c in df_jobs.columns]
        st.dataframe(df_jobs[colunas].sort_values("DataExecucao", ascending=False).head(5),
                     use_container_width=True, hide_index=True)

    if not df_queries.empty and "ClasseABC" in df_queries.columns:
        ult = df_queries[df_queries["DataColeta"] == df_queries["DataColeta"].max()]
        contagem = ult["ClasseABC"].value_counts().sort_index()
        if not contagem.empty:
            st.caption("Consultas na última coleta por classe ABC: " + " · ".join(
                f"{k}: {v}" for k, v in contagem.items()))


# ---------------------------------------------------------------- grade

def render_visao_geral(df_cpu, df_ativ, df_mem, df_waits, df_io, df_disks, df_jobs,
                       df_queries, limites):
    st.caption(
        "Todos os domínios da instância na janela de tempo selecionada. "
        "O detalhe de cada um está nas abas ao lado."
    )

    l1c1, l1c2, l1c3 = st.columns(3)
    with l1c1.container(border=True):
        _cartao_cpu(df_cpu, df_ativ)
    with l1c2.container(border=True):
        _cartao_esperas(df_waits)
    with l1c3.container(border=True):
        _cartao_sessoes(df_ativ)

    l2c1, l2c2, l2c3 = st.columns(3)
    with l2c1.container(border=True):
        _cartao_memoria(df_cpu, df_mem)
    with l2c2.container(border=True):
        _cartao_cache(df_cpu, limites)
    with l2c3.container(border=True):
        _cartao_discos(df_disks, limites)

    l3c1, l3c2 = st.columns([1, 1])
    with l3c1.container(border=True):
        _cartao_io(df_io)
    with l3c2.container(border=True):
        _cartao_operacao(df_jobs, df_queries)
