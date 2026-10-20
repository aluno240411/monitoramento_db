import csv
import io
import json
import os
import re
import urllib
from datetime import datetime
import pandas as pd
import streamlit as st
import yaml
from sqlalchemy import create_engine, text

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "config.yaml")
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

# Grupos de métricas disponíveis para o Isolation Forest (app/ml_engine.py).
# Usado tanto na tela de cadastro de servidor quanto na tela de Anomalias.
GRUPOS_ML_DISPONIVEIS = ["cpu", "memoria", "io", "atividade_sql"]


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {"servers": []}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"servers": []}


def save_config(config_data):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(config_data, f, default_flow_style=False)


def get_connection_string(server_config):
    server = server_config.get("server")
    port = server_config.get("port", 1433)
    database = server_config.get("database", "DB_Observability")
    auth_type = server_config.get("auth_type", "SQL")
    driver = server_config.get("driver", "ODBC Driver 17 for SQL Server")

    server_address = f"{server},{port}" if port else server

    if auth_type == "Windows":
        conn_str = (
            f"DRIVER={{{driver}}};"
            f"SERVER={server_address};"
            f"DATABASE={database};"
            f"Trusted_Connection=yes;"
            f"TrustServerCertificate=yes;"
        )
    else:
        user = server_config.get("user", "")
        pwd = server_config.get("password", "")
        conn_str = (
            f"DRIVER={{{driver}}};"
            f"SERVER={server_address};"
            f"DATABASE={database};"
            f"UID={user};"
            f"PWD={pwd};"
            f"TrustServerCertificate=yes;"
        )
    params = urllib.parse.quote_plus(conn_str)
    return f"mssql+pyodbc:///?odbc_connect={params}"


def test_connection(server_config):
    try:
        engine = create_engine(get_connection_string(server_config), connect_args={"timeout": 5})
        with engine.connect() as conn:
            result = conn.execute(text("SELECT @@VERSION AS Version, GETDATE() AS CurrentDateTime")).mappings().fetchone()
            return True, f"Conexão bem-sucedida! Servidor ativo: {result['CurrentDateTime']}"
    except Exception as e:
        return False, str(e)


# Tabelas de origem de cada CSV. Todas possuem ID BIGINT IDENTITY(1,1) como
# chave primaria clusterizada, o que permite a extracao incremental: basta
# pedir as linhas com ID maior que o ultimo ja trazido.
TABELAS = {
    "cpu_metrics.csv": "obs.Historico_CPU_Counters",
    "activity_metrics.csv": "obs.Historico_AtividadeSQL",
    "memory_metrics.csv": "obs.Historico_Memoria",
    "wait_stats.csv": "obs.Historico_WaitStats",
    "io_stats.csv": "obs.Historico_IO_Stats",
    "top_queries.csv": "obs.Historico_TopQueries",
    "disk_metrics.csv": "obs.Historico_Discos",
    "failed_jobs.csv": "obs.Historico_FalhasJobs",
}

MARCADORES = "_ultima_sincronizacao.json"


def _caminho_marcadores(server_dir):
    return os.path.join(server_dir, MARCADORES)


def _ler_marcadores(server_dir):
    caminho = _caminho_marcadores(server_dir)
    if not os.path.exists(caminho):
        return {}
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (ValueError, OSError):
        return {}


def _gravar_marcadores(server_dir, marcadores):
    try:
        with open(_caminho_marcadores(server_dir), "w", encoding="utf-8") as f:
            json.dump(marcadores, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def _cabecalho_csv(file_path):
    """Colunas ja gravadas no CSV, na ordem em que estao no arquivo."""
    try:
        with open(file_path, "r", encoding="utf-8", newline="") as f:
            linha = f.readline()
    except OSError:
        return None
    if not linha.strip():
        return None
    return next(csv.reader([linha]))


def _ultimo_id_do_csv(file_path):
    """Maior ID presente no CSV. Le apenas a coluna ID.

    Usado uma unica vez, quando o arquivo de marcadores ainda nao existe.
    """
    try:
        col = pd.read_csv(file_path, usecols=["ID"])
    except (ValueError, OSError, pd.errors.EmptyDataError):
        return None
    if col.empty:
        return None
    try:
        return int(col["ID"].max())
    except (TypeError, ValueError):
        return None


def extract_metrics_to_csv(server_config, server_alias, start_date=None, end_date=None,
                           incremental=True):
    """Extrai as tabelas de historico do servidor para data/<alias>/.

    Com `incremental=True` (padrao) traz apenas as linhas inseridas desde a
    ultima sincronizacao, usando o ID IDENTITY de cada tabela como marca
    d'agua, e acrescenta ao final do CSV existente. Cai automaticamente para
    a extracao completa quando o CSV nao existe, quando nao ha marcador ou
    quando as colunas da tabela mudaram.

    Informar `start_date`/`end_date` forca a extracao completa do periodo.
    """
    server_dir = os.path.join(DATA_DIR, server_alias)
    os.makedirs(server_dir, exist_ok=True)

    periodo = bool(start_date and end_date)
    if periodo:
        incremental = False

    marcadores = _ler_marcadores(server_dir) if incremental else {}
    resumo = {}
    novos_marcadores = dict(marcadores)

    # Em ambientes sem acesso a rede interna (ex.: Streamlit Community Cloud)
    # o driver ODBC ou o servidor nao estao disponiveis. A extracao e abortada
    # e o erro volta no resumo, sem derrubar a tela.
    try:
        engine = create_engine(get_connection_string(server_config),
                               connect_args={"timeout": 5})
        conn = engine.connect()
    except Exception as e:
        return {"conexao": f"Erro: SQL Server indisponível neste ambiente ({e})"}

    with conn:
        for filename, tabela in TABELAS.items():
            file_path = os.path.join(server_dir, filename)
            try:
                cabecalho = _cabecalho_csv(file_path) if os.path.exists(file_path) else None

                ultimo_id = None
                if incremental and cabecalho:
                    registro = marcadores.get(filename) or {}
                    ultimo_id = registro.get("ultimo_id")
                    if ultimo_id is None:
                        ultimo_id = _ultimo_id_do_csv(file_path)

                if ultimo_id is not None:
                    consulta = (f"SELECT * FROM {tabela} WHERE ID > :ultimo_id "
                                f"ORDER BY ID ASC")
                    params = {"ultimo_id": int(ultimo_id)}
                elif periodo:
                    consulta = (f"SELECT * FROM {tabela} "
                                f"WHERE DataColeta BETWEEN :start_date AND :end_date "
                                f"ORDER BY DataColeta ASC")
                    params = {"start_date": start_date, "end_date": end_date}
                else:
                    consulta = f"SELECT * FROM {tabela} ORDER BY DataColeta ASC"
                    params = {}

                df = pd.read_sql_query(text(consulta), conn, params=params)

                if ultimo_id is not None:
                    # Estrutura da tabela mudou desde a ultima extracao:
                    # acrescentar corromperia o arquivo. Reextrai inteiro.
                    if list(df.columns) != list(cabecalho):
                        df = pd.read_sql_query(
                            text(f"SELECT * FROM {tabela} ORDER BY DataColeta ASC"), conn)
                        df.to_csv(file_path, index=False, encoding="utf-8")
                        resumo[filename] = f"{len(df)} linhas (estrutura mudou, reextraido)"
                    elif df.empty:
                        resumo[filename] = "sem novidades"
                    else:
                        df.to_csv(file_path, mode="a", header=False, index=False,
                                  encoding="utf-8")
                        resumo[filename] = f"+{len(df)} linhas"
                else:
                    df.to_csv(file_path, index=False, encoding="utf-8")
                    resumo[filename] = f"{len(df)} linhas (completo)"

                if "ID" in df.columns and not df.empty:
                    maior = int(df["ID"].max())
                    if ultimo_id is not None:
                        maior = max(maior, int(ultimo_id))
                elif ultimo_id is not None:
                    maior = int(ultimo_id)
                else:
                    maior = None

                if maior is not None and not periodo:
                    novos_marcadores[filename] = {
                        "ultimo_id": maior,
                        "atualizado_em": datetime.now().isoformat(timespec="seconds"),
                    }
            except Exception as e:
                resumo[filename] = f"Erro: {str(e)}"

    if not periodo:
        _gravar_marcadores(server_dir, novos_marcadores)

    limpar_cache_dados()
    return resumo


DATE_COLUMNS = ["DataColeta", "DataExecucao", "DataSnapshot"]


@st.cache_data(ttl=900, max_entries=48, show_spinner=False)
def _ler_csv(file_path, assinatura, colunas):
    """Lê e converte o CSV. O resultado fica em cache.

    `assinatura` é (mtime, tamanho) do arquivo e não é usada no corpo: entra na
    chave do cache para que uma nova extração invalide a entrada automaticamente.
    `colunas` restringe a leitura — essencial em io_stats.csv e top_queries.csv,
    que chegam a centenas de megabytes.
    """
    try:
        df = pd.read_csv(file_path, usecols=list(colunas) if colunas else None)
    except ValueError:
        # alguma coluna pedida não existe neste arquivo (coleta anterior a uma
        # correção, por exemplo): lê tudo e devolve o que houver
        df = pd.read_csv(file_path)

    for col in DATE_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def _resolver_arquivo(server_alias, metric_file):
    """Caminho do CSV em data/<alias>/, ou da versao compactada (.csv.gz).

    Arquivos grandes vao para o repositorio compactados; o pandas descompacta
    automaticamente pela extensao. Devolve None se nenhum dos dois existir.
    """
    file_path = os.path.join(DATA_DIR, server_alias, metric_file)
    if os.path.exists(file_path):
        return file_path
    if os.path.exists(file_path + ".gz"):
        return file_path + ".gz"
    return None


def load_local_metric_data(server_alias, metric_file, colunas=None):
    """Carrega um CSV extraído para data/<alias>/.

    `colunas` é opcional; quando informado, apenas essas colunas são lidas.
    """
    file_path = _resolver_arquivo(server_alias, metric_file)
    if file_path is None:
        return pd.DataFrame()

    try:
        info = os.stat(file_path)
        assinatura = (info.st_mtime_ns, info.st_size)
    except OSError:
        assinatura = None

    return _ler_csv(file_path, assinatura, tuple(colunas) if colunas else None)


def limpar_cache_dados():
    """Descarta o cache de leitura. Útil após uma extração manual."""
    try:
        _ler_csv.clear()
        _janela.clear()
    except Exception:
        pass


# ----------------------------------------------------------------
# Leitura por janela de tempo (arquivos grandes)
# ----------------------------------------------------------------
# Em instancias com historico longo, top_queries.csv e io_stats.csv chegam a
# centenas de megabytes. Como a coleta so acrescenta linhas no fim e a ordem
# e cronologica, para desenhar uma janela recente basta ler o final do
# arquivo em vez de percorre-lo inteiro.

LIMITE_LEITURA_INTEGRAL = 40 * 1024 * 1024      # abaixo disso, le tudo
BLOCOS_CAUDA = [8, 32, 128, 512]                # MB tentados, em ordem
_INICIO_REGISTRO = re.compile(r"^\d+,\d{4}-\d{2}-\d{2}[ T]")


def _bloco_final(file_path, tamanho_bytes, cabecalho):
    """Devolve um DataFrame com os registros do final do arquivo.

    Posiciona a leitura no primeiro inicio de registro valido dentro do
    bloco — ID seguido de data —, o que evita cortar no meio de um campo de
    texto com quebras de linha, como o QueryText.
    """
    tamanho_arquivo = os.path.getsize(file_path)
    inicio = max(0, tamanho_arquivo - tamanho_bytes)

    with open(file_path, "rb") as f:
        f.seek(inicio)
        bruto = f.read()

    texto = bruto.decode("utf-8", errors="replace")
    if inicio > 0:
        pos = 0
        while True:
            quebra = texto.find("\n", pos)
            if quebra == -1:
                return None
            candidato = quebra + 1
            resto = texto[candidato:candidato + 40]
            if _INICIO_REGISTRO.match(resto):
                texto = texto[candidato:]
                break
            pos = quebra + 1

    df = pd.read_csv(io.StringIO(texto), names=cabecalho, header=None, low_memory=False)
    for col in DATE_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


@st.cache_data(ttl=900, max_entries=32, show_spinner=False)
def _janela(file_path, assinatura, inicio, fim):
    """Le apenas o trecho do arquivo que cobre [inicio, fim]."""
    tamanho = os.path.getsize(file_path)

    # .csv.gz nao permite posicionar a leitura no fim do arquivo: le inteiro.
    if tamanho <= LIMITE_LEITURA_INTEGRAL or file_path.endswith(".gz"):
        df = pd.read_csv(file_path, low_memory=False)
        for col in DATE_COLUMNS:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
    else:
        cabecalho = _cabecalho_csv(file_path)
        df = None
        if cabecalho and "DataColeta" in cabecalho:
            for mb in BLOCOS_CAUDA:
                bloco = mb * 1024 * 1024
                if bloco >= tamanho:
                    break
                try:
                    parcial = _bloco_final(file_path, bloco, cabecalho)
                except Exception:
                    parcial = None
                if parcial is None or parcial.empty:
                    continue
                mais_antigo = parcial["DataColeta"].min()
                if pd.notna(mais_antigo) and mais_antigo <= inicio:
                    df = parcial
                    break
        if df is None:
            df = pd.read_csv(file_path)
            for col in DATE_COLUMNS:
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], errors="coerce")

    if "DataColeta" in df.columns:
        df = df[(df["DataColeta"] >= inicio) & (df["DataColeta"] <= fim)]
    return df


def carregar_janela(server_alias, metric_file, inicio, fim):
    """Carrega de data/<alias>/ apenas as linhas da janela de tempo pedida."""
    file_path = _resolver_arquivo(server_alias, metric_file)
    if file_path is None:
        return pd.DataFrame()
    if inicio is None or fim is None:
        return load_local_metric_data(server_alias, metric_file)

    try:
        info = os.stat(file_path)
        assinatura = (info.st_mtime_ns, info.st_size)
    except OSError:
        assinatura = None

    return _janela(file_path, assinatura, pd.Timestamp(inicio), pd.Timestamp(fim))
