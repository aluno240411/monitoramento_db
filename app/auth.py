import os
import yaml
import streamlit_authenticator as stauth

USERS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "users.yaml")


def init_default_users():
    os.makedirs(os.path.dirname(USERS_PATH), exist_ok=True)
    if not os.path.exists(USERS_PATH):
        try:
            hashed_pw = stauth.Hasher.hash("admin123")
        except TypeError:
            try:
                hasher = stauth.Hasher()
                hashed_pw = hasher.hash("admin123")
            except Exception:
                hashed_pw = stauth.Hasher(["admin123"]).generate()[0]

        config = {
            "credentials": {
                "usernames": {
                    "dba_admin": {
                        "name": "Administrador DBA",
                        "password": hashed_pw,
                        "email": "dba@empresa.com",
                    }
                }
            },
            "cookie": {
                "name": "dba_monitor_cookie",
                "key": "chave_secreta_dba_monitor_2026",
                "expiry_days": 30,
            },
        }
        with open(USERS_PATH, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False)


def get_authenticator():
    init_default_users()
    with open(USERS_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    authenticator = stauth.Authenticate(
        config["credentials"],
        config["cookie"]["name"],
        config["cookie"]["key"],
        config["cookie"]["expiry_days"],
    )
    return authenticator
