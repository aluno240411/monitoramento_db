import streamlit as st
from app.db_connector import load_config, save_config, test_connection, extract_metrics_to_csv, GRUPOS_ML_DISPONIVEIS

LABELS_GRUPOS_ML = {
    "cpu": "CPU / Memória (PLE, buffer cache, lazy writes)",
    "memoria": "Memory Clerks",
    "io": "I/O de Disco",
    "atividade_sql": "Atividade SQL (batch requests, deadlocks, bloqueios)",
}


def render_server_mgmt():
    st.header("⚙️ Gerenciamento de Servidores e Extração de Métricas")
    config = load_config()
    servers = config.get("servers", [])

    st.subheader("Instâncias Cadastradas")
    if not servers:
        st.info("Nenhuma instância SQL Server cadastrada no momento.")
    else:
        for idx, s in enumerate(servers):
            with st.expander(f"🖥️ {s.get('alias', 'Sem Nome')} ({s.get('server')}:{s.get('port', 1433)})"):
                st.write(f"**Host/IP:** `{s.get('server')}` | **Porta:** `{s.get('port', 1433)}`")
                st.write(f"**Database de Coleta:** `{s.get('database', 'DB_Observability')}`")
                st.write(f"**Tipo de Autenticação:** `{s.get('auth_type')}`")
                grupos_atuais = s.get("metricas_ml", GRUPOS_ML_DISPONIVEIS)
                st.write(f"**Grupos de métricas no Isolation Forest:** {', '.join(grupos_atuais) if grupos_atuais else 'nenhum'}")

                col1, col2, col3 = st.columns([1, 1.5, 1])
                with col1:
                    if st.button(f"🔌 Testar Conexão", key=f"test_{idx}"):
                        ok, msg = test_connection(s)
                        if ok:
                            st.success(msg)
                        else:
                            st.error(f"Falha: {msg}")
                with col2:
                    if st.button(f"📥 Sincronizar novidades", key=f"sync_{idx}",
                                 help="Traz apenas as coletas novas desde a última sincronização"):
                        with st.spinner("Buscando coletas novas..."):
                            summary = extract_metrics_to_csv(s, s.get("alias"))
                        if "conexao" in summary:
                            st.error(summary["conexao"])
                        else:
                            st.success(f"Sincronização concluída. Arquivos em data/{s.get('alias')}/")
                            st.json(summary)
                    if st.button(f"⚠️ Reextrair tudo", key=f"sync_full_{idx}",
                                 help="Descarta os CSVs locais e baixa todo o histórico de novo"):
                        with st.spinner("Reextraindo todo o histórico..."):
                            summary = extract_metrics_to_csv(s, s.get("alias"), incremental=False)
                        if "conexao" in summary:
                            st.error(summary["conexao"])
                        else:
                            st.success(f"Reextração concluída. Arquivos em data/{s.get('alias')}/")
                            st.json(summary)
                with col3:
                    if st.button(f"🗑️ Remover Instância", key=f"del_{idx}"):
                        servers.pop(idx)
                        config["servers"] = servers
                        save_config(config)
                        st.rerun()

                novos_grupos = st.multiselect(
                    "Grupos de métricas usados na detecção de anomalias (ML)",
                    options=GRUPOS_ML_DISPONIVEIS,
                    default=grupos_atuais,
                    format_func=lambda g: LABELS_GRUPOS_ML.get(g, g),
                    key=f"ml_grupos_{idx}",
                )
                if st.button("💾 Salvar grupos de ML", key=f"save_ml_{idx}"):
                    servers[idx]["metricas_ml"] = novos_grupos
                    config["servers"] = servers
                    save_config(config)
                    st.success("Grupos de métricas atualizados.")
                    st.rerun()

    st.divider()
    st.subheader("➕ Cadastrar Nova Instância SQL Server")
    with st.form("form_add_server"):
        alias = st.text_input("Nome Amigável da Instância (Alias)", placeholder="Ex: Producao_OLTP")
        c1, c2 = st.columns(2)
        with c1:
            server = st.text_input("Host / IP", placeholder="127.0.0.1 ou SRV-SQL-01")
            port = st.number_input("Porta", value=1433, step=1)
            database = st.text_input("Database", value="DB_Observability")
        with c2:
            driver = st.selectbox("Driver ODBC", ["ODBC Driver 17 for SQL Server", "ODBC Driver 18 for SQL Server", "SQL Server"])
            auth_type = st.radio("Tipo de Autenticação", ["Windows", "SQL"])

        user = ""
        password = ""
        if auth_type == "SQL":
            u1, u2 = st.columns(2)
            with u1:
                user = st.text_input("Usuário SQL")
            with u2:
                password = st.text_input("Senha SQL", type="password")

        metricas_ml = st.multiselect(
            "Grupos de métricas para a detecção de anomalias (ML)",
            options=GRUPOS_ML_DISPONIVEIS,
            default=GRUPOS_ML_DISPONIVEIS,
            format_func=lambda g: LABELS_GRUPOS_ML.get(g, g),
        )

        submitted = st.form_submit_button("💾 Salvar Servidor")
        if submitted:
            if not alias or not server:
                st.error("Preencha o Nome Amigável e o Host/IP.")
            else:
                new_server = {
                    "alias": alias,
                    "server": server,
                    "port": int(port),
                    "database": database,
                    "driver": driver,
                    "auth_type": auth_type,
                    "user": user,
                    "password": password,
                    "metricas_ml": metricas_ml,
                }
                servers.append(new_server)
                config["servers"] = servers
                save_config(config)
                st.success(f"Servidor '{alias}' cadastrado com sucesso!")
                st.rerun()
