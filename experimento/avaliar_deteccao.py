# -*- coding: utf-8 -*-
"""
avaliar_deteccao.py — Avaliacao quantitativa do detector de anomalias

Compara tres bracos de deteccao sobre a MESMA serie temporal e a MESMA
verdade de referencia:

    Camada 1  — regras de limiar fixo (dicionario LIMITES do painel)
    Camada 2  — Isolation Forest multivariado (app/ml_engine.py)
    Uniao     — alarme se qualquer uma das duas disparar

A verdade de referencia vem de obs.Log_Injecoes: um minuto e anomalo
por construcao se cai dentro do intervalo [DataInicio, DataFim] de uma
injecao concluida.

Este script NAO se conecta a nenhum SQL Server. Ele le apenas os CSVs
que o painel ja extrai para data/<alias>/, mais o log de injecoes.

USO
    python experimento/avaliar_deteccao.py --alias LAB-SQL
    python experimento/avaliar_deteccao.py --alias LAB-SQL --contaminacao 0.02
    python experimento/avaliar_deteccao.py --alias LAB-SQL --sem-varredura

O log de injecoes e procurado, nesta ordem, em:
    data/<alias>/injections.csv
    experimento/injections.csv
Exporte-o com experimento/04_exportar_log_injecoes.sql.
"""

import argparse
import os
import sys

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.ml_engine import montar_dataset, detectar_anomalias, GRADE    # noqa: E402

# Leitura dos CSVs feita aqui, e nao via app/db_connector.py, de proposito:
# db_connector importa sqlalchemy no topo do modulo, e este script nao deve
# ter nenhuma dependencia de driver de banco nem qualquer caminho de conexao
# com o SQL Server. Ele le exclusivamente arquivos ja extraidos.
DATA_DIR = os.path.join(BASE_DIR, "data")

COLUNAS_DATA = ("DataColeta", "DataExecucao", "DataSnapshot")


def load_local_metric_data(server_alias, metric_file):
    caminho = os.path.join(DATA_DIR, server_alias, metric_file)
    if not os.path.exists(caminho):
        return pd.DataFrame()
    df = pd.read_csv(caminho)
    for col in COLUNAS_DATA:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df

# Limiares da Camada 1. Importados do painel para garantir que a avaliacao
# use exatamente os mesmos valores que a interface mostra ao DBA.
try:
    from app.views.dashboard import LIMITES
except Exception:  # streamlit ausente no ambiente de avaliacao
    LIMITES = {
        "ple_segundos":        {"aviso": 1000, "critico": 300, "direcao": "abaixo"},
        "buffer_cache_hit_pct": {"aviso": 97,  "critico": 95,  "direcao": "abaixo"},
        "cpu_sql_pct":         {"aviso": 70,   "critico": 90,  "direcao": "acima"},
        "latencia_leitura_ms": {"aviso": 15,   "critico": 25,  "direcao": "acima"},
        "latencia_escrita_ms": {"aviso": 15,   "critico": 25,  "direcao": "acima"},
        "sessoes_bloqueadas":  {"aviso": 1,    "critico": 5,   "direcao": "acima"},
    }

# Indicador da Camada 1 -> coluna correspondente na grade do ml_engine.
# cpu_total_pct e espaco_livre_pct ficam de fora: nao existem na grade
# montada por montar_dataset(). Declarar essa limitacao na Metodologia.
MAPA_LIMIAR_COLUNA = {
    "ple_segundos":         "PageLifeExpectancy",
    "buffer_cache_hit_pct": "BufferCacheHitRatio",
    "cpu_sql_pct":          "SQLServerCPU",
    "latencia_leitura_ms":  "IO_ReadLatencyMs",
    "latencia_escrita_ms":  "IO_WriteLatencyMs",
    "sessoes_bloqueadas":   "SessoesBloqueadas",
}

GRUPOS = ["cpu", "memoria", "io", "atividade_sql"]
CONTAMINACOES = [0.01, 0.02, 0.05, 0.10, 0.15]


# ---------------------------------------------------------------- carga
def carregar_grade(alias):
    """Monta a grade de 1 min exatamente como o painel faz."""
    dfs = {
        "df_cpu":  load_local_metric_data(alias, "cpu_metrics.csv"),
        "df_mem":  load_local_metric_data(alias, "memory_metrics.csv"),
        "df_io":   load_local_metric_data(alias, "io_stats.csv"),
        "df_ativ": load_local_metric_data(alias, "activity_metrics.csv"),
    }
    return montar_dataset(GRUPOS, **dfs)


COLUNAS_OBRIGATORIAS = ("ModoFalha", "Intensidade", "DataInicio", "DataFim")


def carregar_injecoes(alias):
    candidatos = [
        os.path.join(DATA_DIR, alias, "injections.csv"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "injections.csv"),
    ]
    caminho = next((c for c in candidatos if os.path.exists(c)), None)
    if caminho is None:
        raise SystemExit(
            "Log de injecoes nao encontrado. Esperado em:\n  "
            + "\n  ".join(candidatos)
            + "\nExporte com experimento/04_exportar_log_injecoes.sql."
        )

    # sep=None faz o pandas detectar o separador: o SSMS em portugues salva
    # CSV com ponto e virgula, nao com virgula. utf-8-sig remove o BOM que o
    # SSMS grava no inicio do arquivo e que contaminaria o nome da 1a coluna.
    df = pd.read_csv(caminho, sep=None, engine="python", encoding="utf-8-sig")
    df.columns = [str(c).strip() for c in df.columns]

    faltando = [c for c in COLUNAS_OBRIGATORIAS if c not in df.columns]
    if faltando:
        raise SystemExit(
            f"O arquivo {caminho} nao tem a(s) coluna(s): {', '.join(faltando)}.\n"
            f"Colunas encontradas: {', '.join(df.columns)}\n"
            "Reexporte com experimento/04_exportar_log_injecoes.sql, garantindo "
            "que os cabecalhos sejam incluidos no CSV."
        )

    for col in ("DataInicio", "DataFim"):
        df[col] = pd.to_datetime(df[col], errors="coerce")

    print(f"Log de injecoes: {caminho} ({len(df)} registros)")
    return df


def injecoes_validas(injecoes):
    """Injecoes aproveitaveis: concluidas e com inicio e fim conhecidos.

    O filtro de Status usa 'in .columns' e nao DataFrame.get(): num DataFrame,
    .get('Status', 'CONCLUIDA') devolve a STRING quando a coluna nao existe, e
    a comparacao vira o booleano True, produzindo um KeyError incompreensivel
    em vez de dizer que a coluna esta faltando.
    """
    df = injecoes
    if "Status" in df.columns:
        df = df[df["Status"].astype(str).str.strip().str.upper() == "CONCLUIDA"]
    return df.dropna(subset=["DataInicio", "DataFim"]).copy()


# ------------------------------------------------------- rotulo e alarmes
def rotular(indice, injecoes, folga_min=0):
    """
    Verdade de referencia por minuto.
    Devolve (serie booleana, serie com o modo de falha).

    folga_min estende a janela rotulada para tras e para frente. O padrao e
    ZERO, e nao 1, de proposito: rotular minutos em que nao havia injecao
    ativa infla os falsos negativos e derruba a revocacao artificialmente.
    Numa serie de 14 dias com 48 injecoes de 3 min, folga_min=1 rotula ~287
    minutos onde apenas ~192 tinham carga de fato -- 50% de rotulos
    espurios. A tolerancia de borda deve ser tratada na analise de tempo
    ate deteccao (Tabela 3), nao no rotulo.
    """
    rotulo = pd.Series(False, index=indice)
    modo = pd.Series(None, index=indice, dtype=object)

    validas = injecoes_validas(injecoes)

    for _, inj in validas.iterrows():
        ini = inj["DataInicio"].floor("min") - pd.Timedelta(minutes=folga_min)
        fim = inj["DataFim"].ceil("min") + pd.Timedelta(minutes=folga_min)
        janela = (indice >= ini) & (indice <= fim)
        rotulo[janela] = True
        modo[janela] = inj["ModoFalha"]

    return rotulo, modo


def alarmes_camada1(df, nivel="critico"):
    """Alarme por regra de limiar fixo, minuto a minuto."""
    alarme = pd.Series(False, index=df.index)
    acionados = {}

    for chave, cfg in LIMITES.items():
        coluna = MAPA_LIMIAR_COLUNA.get(chave)
        if coluna is None or coluna not in df.columns:
            continue
        limite = cfg[nivel]
        if cfg["direcao"] == "acima":
            disparo = df[coluna] > limite
        else:
            disparo = df[coluna] < limite
        acionados[chave] = int(disparo.sum())
        alarme = alarme | disparo

    return alarme, acionados


def alarmes_camada2(df, contaminacao):
    resultado = detectar_anomalias(df, contamination=contaminacao)
    return resultado["is_anomaly"].astype(bool), resultado


# ------------------------------------------------------------- metricas
def metricas(rotulo, alarme):
    vp = int((rotulo & alarme).sum())
    fp = int((~rotulo & alarme).sum())
    fn = int((rotulo & ~alarme).sum())
    vn = int((~rotulo & ~alarme).sum())

    precisao = vp / (vp + fp) if (vp + fp) else 0.0
    revocacao = vp / (vp + fn) if (vp + fn) else 0.0
    f1 = 2 * precisao * revocacao / (precisao + revocacao) if (precisao + revocacao) else 0.0
    tfp = fp / (fp + vn) if (fp + vn) else 0.0

    return {
        "VP": vp, "FP": fp, "FN": fn, "VN": vn,
        "Precisao": round(precisao, 4),
        "Revocacao": round(revocacao, 4),
        "F1": round(f1, 4),
        "TaxaFalsoPositivo": round(tfp, 4),
    }


def latencia_deteccao(injecoes, alarme, folga_min=1):
    """Minutos entre o inicio da injecao e o primeiro alarme na janela."""
    linhas = []
    validas = injecoes_validas(injecoes)

    for _, inj in validas.iterrows():
        ini = inj["DataInicio"].floor("min")
        fim = inj["DataFim"].ceil("min") + pd.Timedelta(minutes=folga_min)
        janela = alarme[(alarme.index >= ini - pd.Timedelta(minutes=folga_min))
                        & (alarme.index <= fim)]
        disparos = janela[janela]
        linhas.append({
            "ModoFalha": inj["ModoFalha"],
            "Intensidade": inj["Intensidade"],
            "DataInicio": inj["DataInicio"],
            "Detectada": bool(len(disparos)),
            "LatenciaMin": (disparos.index[0] - ini).total_seconds() / 60.0 if len(disparos) else None,
        })
    return pd.DataFrame(linhas)


def recall_por_modo(rotulo, modo, alarme):
    linhas = []
    for m in sorted([x for x in modo.dropna().unique()]):
        sel = modo == m
        total = int(sel.sum())
        pegos = int((sel & alarme).sum())
        linhas.append({
            "ModoFalha": m,
            "MinutosAnomalos": total,
            "MinutosDetectados": pegos,
            "Revocacao": round(pegos / total, 4) if total else 0.0,
        })
    return pd.DataFrame(linhas)


# ----------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Avaliacao do detector de anomalias do dba-monitor")
    ap.add_argument("--alias", required=True, help="alias do servidor, ex: LAB-SQL")
    ap.add_argument("--contaminacao", type=float, default=0.05,
                    help="contamination do Isolation Forest para o braco principal")
    ap.add_argument("--nivel-limiar", default="aviso", choices=["aviso", "critico"],
                    help="nivel da Camada 1 que compoe o braco de uniao "
                         "(os dois niveis sao avaliados isoladamente de todo modo)")
    ap.add_argument("--folga-min", type=int, default=0,
                    help="folga em minutos ao rotular as janelas de injecao")
    ap.add_argument("--sem-varredura", action="store_true",
                    help="nao roda a varredura de contamination")
    ap.add_argument("--de", default=None,
                    help="inicio da janela a avaliar, ex: 2026-09-17 09:00. "
                         "Recorta a serie E o log de injecoes, para que a "
                         "verdade de referencia corresponda a janela analisada")
    ap.add_argument("--ate", default=None,
                    help="fim da janela a avaliar, ex: 2026-09-21 00:00")
    ap.add_argument("--saida", default=None, help="pasta de saida dos CSVs")
    args = ap.parse_args()

    saida = args.saida or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "resultados", args.alias)
    os.makedirs(saida, exist_ok=True)

    print("=" * 72)
    print(f"AVALIACAO DO DETECTOR — {args.alias}")
    print("=" * 72)

    df = carregar_grade(args.alias)
    if df.empty:
        raise SystemExit("Grade vazia. Extraia os CSVs pelo painel antes de avaliar.")

    # Recorte da janela. Necessario quando a serie contem mais de uma campanha
    # de injecoes: a verdade de referencia precisa corresponder exatamente ao
    # periodo analisado, e o modelo deve ser treinado sobre um unico regime de
    # coleta. Sem isso, injecoes de janelas anteriores entram como rotulo sem
    # que suas metricas estejam no mesmo padrao.
    de = pd.to_datetime(args.de) if args.de else None
    ate = pd.to_datetime(args.ate) if args.ate else None
    if de is not None:
        df = df[df.index >= de]
    if ate is not None:
        df = df[df.index <= ate]
    if df.empty:
        raise SystemExit("Nenhuma amostra na janela pedida. Confira --de e --ate.")
    if de is not None or ate is not None:
        print(f"Janela analisada: {df.index.min()} -> {df.index.max()}")

    print(f"Grade de {GRADE}: {len(df)} amostras | "
          f"{df.index.min()} -> {df.index.max()}")
    print(f"Atributos ({len(df.columns)}): {', '.join(df.columns)}")

    injecoes = carregar_injecoes(args.alias)
    antes = len(injecoes)
    if de is not None:
        injecoes = injecoes[injecoes["DataInicio"] >= de]
    if ate is not None:
        injecoes = injecoes[injecoes["DataInicio"] <= ate]
    if len(injecoes) != antes:
        print(f"Injecoes na janela: {len(injecoes)} de {antes} registradas")
    if injecoes.empty:
        raise SystemExit("Nenhuma injecao na janela pedida. Confira --de e --ate.")

    rotulo, modo = rotular(df.index, injecoes, folga_min=args.folga_min)
    n_anom = int(rotulo.sum())
    print(f"Verdade de referencia: {n_anom} minutos anomalos "
          f"({n_anom / len(df) * 100:.2f}% da serie)")

    if n_anom == 0:
        raise SystemExit(
            "Nenhum minuto anomalo na serie. Verifique se a janela de injecoes "
            "se sobrepoe a janela de metricas extraidas."
        )

    # ---- bracos de deteccao
    a1_aviso, acionados_aviso = alarmes_camada1(df, nivel="aviso")
    a1_crit, acionados_crit = alarmes_camada1(df, nivel="critico")
    a2, resultado_if = alarmes_camada2(df, args.contaminacao)

    a1_uniao = a1_aviso if args.nivel_limiar == "aviso" else a1_crit
    au = a1_uniao | a2

    bracos = {
        "Camada 1 (limiar aviso)": a1_aviso,
        "Camada 1 (limiar critico)": a1_crit,
        f"Camada 2 (IF contamination={args.contaminacao})": a2,
        f"Uniao (C1 {args.nivel_limiar} + C2)": au,
    }
    acionados = {"aviso": acionados_aviso, "critico": acionados_crit}

    print("\n" + "-" * 72)
    print("TABELA 1 — Desempenho comparado dos bracos de deteccao")
    print("-" * 72)
    tab1 = pd.DataFrame({nome: metricas(rotulo, al) for nome, al in bracos.items()}).T
    print(tab1.to_string())
    tab1.to_csv(os.path.join(saida, "tabela1_bracos.csv"), encoding="utf-8")

    print("\n" + "-" * 72)
    print("TABELA 2 — Revocacao por modo de falha")
    print("-" * 72)
    partes = []
    for nome, al in bracos.items():
        t = recall_por_modo(rotulo, modo, al)
        t.insert(0, "Braco", nome)
        partes.append(t)
    tab2 = pd.concat(partes, ignore_index=True)
    print(tab2.to_string(index=False))
    tab2.to_csv(os.path.join(saida, "tabela2_por_modo.csv"), index=False, encoding="utf-8")

    print("\n" + "-" * 72)
    print("TABELA 3 — Tempo ate deteccao (minutos)")
    print("-" * 72)
    partes = []
    for nome, al in bracos.items():
        t = latencia_deteccao(injecoes, al, folga_min=args.folga_min)
        t.insert(0, "Braco", nome)
        partes.append(t)
    tab3 = pd.concat(partes, ignore_index=True)
    resumo3 = (tab3.groupby("Braco")
                   .agg(Injecoes=("Detectada", "size"),
                        Detectadas=("Detectada", "sum"),
                        LatenciaMedia=("LatenciaMin", "mean"),
                        LatenciaMediana=("LatenciaMin", "median"),
                        LatenciaMax=("LatenciaMin", "max"))
                   .round(2))
    print(resumo3.to_string())
    tab3.to_csv(os.path.join(saida, "tabela3_latencia_detalhe.csv"), index=False, encoding="utf-8")
    resumo3.to_csv(os.path.join(saida, "tabela3_latencia_resumo.csv"), encoding="utf-8")

    print("\n" + "-" * 72)
    print("Indicadores da Camada 1 que dispararam (minutos)")
    print("-" * 72)
    chaves = sorted(set(acionados["aviso"]) | set(acionados["critico"]))
    print(f"  {'indicador':24s} {'aviso':>8s} {'critico':>8s}")
    for k in chaves:
        print(f"  {k:24s} {acionados['aviso'].get(k, 0):>8d} {acionados['critico'].get(k, 0):>8d}")

    # ---- varredura de contamination
    if not args.sem_varredura:
        print("\n" + "-" * 72)
        print("TABELA 4 — Varredura de contamination (Camada 2 isolada)")
        print("-" * 72)
        linhas = []
        for c in CONTAMINACOES:
            al, _ = alarmes_camada2(df, c)
            m = metricas(rotulo, al)
            m["contamination"] = c
            linhas.append(m)
        tab4 = pd.DataFrame(linhas).set_index("contamination")
        print(tab4.to_string())
        tab4.to_csv(os.path.join(saida, "tabela4_varredura_contamination.csv"), encoding="utf-8")

    # ---- serie anotada, para gerar as figuras depois
    anotada = resultado_if.copy()
    anotada["rotulo_verdade"] = rotulo
    anotada["modo_falha"] = modo
    anotada["alarme_c1_aviso"] = a1_aviso
    anotada["alarme_c1_critico"] = a1_crit
    anotada["alarme_camada2"] = a2
    anotada.to_csv(os.path.join(saida, "serie_anotada.csv"), encoding="utf-8")

    print("\n" + "=" * 72)
    print(f"CSVs gravados em: {saida}")
    print("=" * 72)


if __name__ == "__main__":
    main()
