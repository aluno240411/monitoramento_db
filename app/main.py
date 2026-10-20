import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import streamlit as st
from app.auth import get_authenticator
from app.views.dashboard import render_dashboard
from app.views.server_mgmt import render_server_mgmt
from app.views.dba_scripts import render_dba_scripts
from app.views.experimento import render_experimento
from app.views.experimento_scripts import render_experimento_scripts

st.set_page_config(
    page_title="DBA Monitor - SQL Server Observability",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Compacta a faixa superior: o titulo da pagina sobe para a mesma linha
# da barra de ferramentas do Streamlit (onde fica o botao Deploy).
st.markdown(
    """
    <style>
      header[data-testid="stHeader"] { background: transparent; height: 0rem; }
      [data-testid="stAppViewContainer"] .block-container {
          padding-top: 1.1rem;
          padding-bottom: 2rem;
      }
      .titulo-painel {
          margin: 0 0 0.6rem 0;
          padding: 0;
          font-size: 1.45rem;
          font-weight: 700;
          line-height: 1.9rem;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

authenticator = get_authenticator()
authenticator.login()

if st.session_state.get("authentication_status"):
    with st.sidebar:
        st.title("⚡ DBA Monitor")
        st.write(f"Bem-vindo(a), **{st.session_state.get('name')}**!")
        authenticator.logout("Sair", "sidebar")
        st.divider()

        menu = st.radio(
            "Navegação",
            [
                "📊 Painel de Observabilidade",
                "⚙️ Gerenciar Servidores",
                "👥 Gerenciar Usuários",
                "📜 Onboarding DBA",
                "🧪 Onboarding do Experimento",
                "🧪 Resultado do Experimento",
                "🤖 Detecção de Anomalias (ML)"
            ],
            index=0,
        )

    if menu == "📊 Painel de Observabilidade":
        render_dashboard()
    elif menu == "⚙️ Gerenciar Servidores":
        render_server_mgmt()
    elif menu == "📜 Onboarding DBA":
        render_dba_scripts()
    elif menu == "🧪 Onboarding do Experimento":
        render_experimento_scripts()
    elif menu == "🧪 Resultado do Experimento":
        render_experimento()
    elif menu == "👥 Gerenciar Usuários":
        from app.views.user_mgmt import render_user_mgmt
        render_user_mgmt()
    elif menu == "🤖 Detecção de Anomalias (ML)":
        from app.views.anomalies import render_anomalies
        render_anomalies()

elif st.session_state.get("authentication_status") is False:
    st.error("Usuário ou senha incorretos.")
elif st.session_state.get("authentication_status") is None:
    st.warning("Por favor, insira suas credenciais para acessar o painel.")
