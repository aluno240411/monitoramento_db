import streamlit as st
import yaml
import os
import streamlit_authenticator as stauth

USERS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "users.yaml")


def load_users():
    if not os.path.exists(USERS_PATH):
        return {"credentials": {"usernames": {}}}
    with open(USERS_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"credentials": {"usernames": {}}}


def save_users(users_data):
    with open(USERS_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(users_data, f, default_flow_style=False)


def render_user_mgmt():
    st.header("👥 Gerenciamento de Usuários de Acesso")

    users_data = load_users()
    usernames = users_data.get("credentials", {}).get("usernames", {})

    st.subheader("Usuários Atuais")
    if usernames:
        for u_login, u_info in list(usernames.items()):
            col1, col2, col3 = st.columns([2, 2, 1])
            with col1:
                st.write(f"**Login:** `{u_login}`")
            with col2:
                st.write(f"**Nome:** {u_info.get('name', 'N/A')} | **Email:** {u_info.get('email', 'N/A')}")
            with col3:
                if u_login != st.session_state.get("username") and len(usernames) > 1:
                    if st.button("🗑️ Excluir", key=f"del_user_{u_login}"):
                        del usernames[u_login]
                        users_data["credentials"]["usernames"] = usernames
                        save_users(users_data)
                        st.success(f"Usuário '{u_login}' removido!")
                        st.rerun()
                else:
                    st.caption("(Ativo)")

    st.divider()

    st.subheader("➕ Cadastrar Novo Usuário")
    with st.form("form_add_user"):
        new_username = st.text_input("Login de Acesso (Username)", placeholder="ex: analista_db").strip().lower()
        new_name = st.text_input("Nome Completo", placeholder="ex: Fulano de Tal")
        new_email = st.text_input("E-mail", placeholder="ex: fulano@empresa.com")

        c1, c2 = st.columns(2)
        with c1:
            new_password = st.text_input("Senha", type="password")
        with c2:
            confirm_password = st.text_input("Confirmar Senha", type="password")

        submitted = st.form_submit_button("💾 Criar Usuário")

        if submitted:
            if not new_username or not new_password or not new_name:
                st.error("Preencha Login, Nome e Senha.")
            elif new_username in usernames:
                st.error(f"O login '{new_username}' já existe.")
            elif new_password != confirm_password:
                st.error("As senhas digitadas não coincidem.")
            else:
                try:
                    hashed_pw = stauth.Hasher.hash(new_password)
                except TypeError:
                    hasher = stauth.Hasher()
                    hashed_pw = hasher.hash(new_password)

                usernames[new_username] = {
                    "name": new_name,
                    "password": hashed_pw,
                    "email": new_email,
                }
                users_data["credentials"]["usernames"] = usernames
                save_users(users_data)
                st.success(f"Usuário '{new_username}' criado com sucesso!")
                st.rerun()
