import os
import re
import streamlit as st

SQL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "sql")

DATABASE_PADRAO = "DB_Observability"

SCRIPTS = [
    ("01_criar_banco_schema_tabelas.sql", "Cria o banco, o schema obs e as tabelas de histórico."),
    ("02_stored_procedures_coleta.sql", "Cria as stored procedures de coleta (obs.stp_Coletar_*) e a de limpeza/retenção."),
    ("03_sql_agent_jobs.sql", "Cria os SQL Agent Jobs agendados que chamam as procedures de coleta."),
    ("05_correcao_coleta_io.sql", "Atualização: passa a gravar a E/S por intervalo. Sem ela, a latência registrada é a média acumulada desde que a instância subiu."),
    ("06_coleta_io_por_volume.sql", "Atualização: acrescenta o caminho físico e o ponto de montagem a cada arquivo, permitindo atribuir a latência de E/S a um volume."),
    ("simular_pico_cpu.sql", "Opcional: gera uma carga de CPU para testar o painel e os alertas."),
]

# Identificador de banco: letras, dígitos e underscore, começando por letra ou underscore.
NOME_VALIDO = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def render_dba_scripts():
    st.header("📜 Onboarding do DBA - Provisionamento de Coleta")
    st.markdown(
        """
        Baixe os scripts abaixo e execute-os **manualmente, nesta ordem**, via SSMS
        (ou ferramenta equivalente) na instância SQL Server que deseja monitorar.
        Este painel nunca executa esses scripts por você.
        """
    )

    db_name = st.text_input(
        "Nome do Database de monitoramento (usado nos scripts abaixo)",
        value=DATABASE_PADRAO,
        help="Substitui todas as referências a "
             f"'{DATABASE_PADRAO}' nos scripts pelo nome escolhido. "
             "Lembre-se de cadastrar o mesmo nome em 'Gerenciar Servidores'.",
    ).strip()

    if not db_name:
        st.warning(f"Nome vazio — usando o padrão '{DATABASE_PADRAO}'.")
        db_name = DATABASE_PADRAO
    elif not NOME_VALIDO.match(db_name):
        st.error(
            "Nome de database inválido. Use apenas letras, números e underscore, "
            f"começando por letra ou underscore. Usando o padrão '{DATABASE_PADRAO}' por enquanto."
        )
        db_name = DATABASE_PADRAO
    elif db_name != DATABASE_PADRAO:
        st.info(f"Os scripts abaixo serão gerados usando o database **{db_name}**.")

    for filename, descricao in SCRIPTS:
        path = os.path.join(SQL_DIR, filename)
        st.markdown(f"**{filename}** — {descricao}")

        sql_text = ""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                sql_text = f.read()
            if db_name != DATABASE_PADRAO:
                sql_text = sql_text.replace(DATABASE_PADRAO, db_name)
        else:
            sql_text = f"-- Arquivo {filename} não encontrado em sql/."

        st.download_button(
            label=f"📥 Baixar {filename}",
            data=sql_text,
            file_name=filename,
            mime="text/plain",
            key=f"download_{filename}",
        )
        with st.expander(f"👁️ Visualizar {filename}"):
            st.code(sql_text, language="sql")
        st.divider()
