"""
ml_engine.py — Detecção de anomalias multivariada (Isolation Forest)

Combina os grupos de métricas escolhidos para o servidor (CPU/memória,
memória por clerk, I/O e atividade SQL) numa grade temporal comum (1 min)
e sinaliza COMBINAÇÕES atípicas — ex: PLE caindo + latência subindo +
sessões bloqueadas ao mesmo tempo — mesmo que cada métrica isolada ainda
esteja dentro do limite.

Os grupos considerados vêm de `metricas_ml` no cadastro do servidor
(config/config.yaml), o que permite ligar/desligar cada grupo por
instância monitorada.
"""

import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

GRADE = "1min"  # frequência da grade que alinha métricas de fontes diferentes


# Faixas validas por metrica. Valores fora da faixa sao artefato de
# instrumentacao, nao comportamento do servidor, e sao aparados antes da
# padronizacao — um unico valor absurdo domina a media e o desvio-padrao
# calculados pelo StandardScaler e achata toda a variacao legitima da coluna.
#
# BufferCacheHitRatio: o contador 'Buffer cache hit ratio' e do tipo
# PERF_LARGE_RAW_FRACTION e precisa ser dividido por sua base. Quando os dois
# sao lidos em instantes ligeiramente distintos, a razao ultrapassa 100%. Foram
# observados 4 registros assim em 22.670 (821,95% e 648,51% em SQLDEV, 904,91%
# em Prod). A coleta foi corrigida em sql/02_stored_procedures_coleta.sql para
# ler os contadores numa unica varredura, mas o historico ja gravado antes da
# correcao continua contendo esses valores.
FAIXAS_VALIDAS = {
    "BufferCacheHitRatio": (0.0, 100.0),
}


def _aparar_faixas(df):
    """Limita cada coluna a sua faixa valida (clip). Colunas sem faixa
    declarada passam intactas."""
    for coluna, (minimo, maximo) in FAIXAS_VALIDAS.items():
        if coluna in df.columns:
            df[coluna] = df[coluna].clip(lower=minimo, upper=maximo)
    return df


def _colunas_cpu(df_cpu):
    cols = ["SQLServerCPU", "PageLifeExpectancy", "BufferCacheHitRatio", "LazyWritesPeriodo"]
    cols = [c for c in cols if c in df_cpu.columns]
    if not cols:
        return None
    dados = df_cpu.set_index("DataColeta")[cols].copy()
    # Apara ANTES da reamostragem: a media de um minuto que contenha o
    # artefato ficaria contaminada se o clip viesse depois.
    dados = _aparar_faixas(dados)
    return dados.resample(GRADE).mean()


def _colunas_memoria(df_mem):
    if df_mem.empty or "MemoriaMB" not in df_mem.columns:
        return None
    total = df_mem.groupby("DataColeta")["MemoriaMB"].sum().rename("MemoriaTotalMB")
    return total.to_frame().resample(GRADE).mean()


def _colunas_io(df_io):
    """Metricas de E/S por intervalo.

    sys.dm_io_virtual_file_stats e CUMULATIVA desde a inicializacao da
    instancia. A coleta original gravava seus valores crus, de modo que:

      - MBRead/MBWritten sao totais acumulados, nao o volume do minuto. Um
        atributo monotonicamente crescente codifica o TEMPO, nao a carga, e
        envenena a deteccao de anomalias: o modelo passa a distinguir inicio
        de fim da serie. O valor do intervalo e recuperavel pela diferenca
        entre coletas consecutivas do mesmo arquivo.

      - ReadLatencyMs/WriteLatencyMs sao a media de latencia desde a
        inicializacao (io_stall_ms / num_of_reads sobre os totais), e nao a
        latencia do minuto. Em LAB-SQL, um arquivo apresentou apenas 3 valores
        distintos em 14 dias, permanentemente acima de 25 ms, o que fazia a
        regra de limiar disparar em 100% dos minutos. NAO sao recuperaveis a
        posteriori: a procedure gravou so a razao, nao o numerador e o
        denominador. Por isso so entram no modelo quando a coleta ja grava as
        colunas _Periodo (procedure corrigida em 02_stored_procedures_coleta).
    """
    if df_io.empty:
        return None

    d = df_io.sort_values("DataColeta").copy()
    chave = [c for c in ("DatabaseName", "FileName") if c in d.columns]

    # ---- volume transferido no intervalo
    if "MBReadPeriodo" in d.columns:
        d["_mbr"] = d["MBReadPeriodo"]
        d["_mbw"] = d.get("MBWrittenPeriodo")
    elif chave:
        for origem, destino in (("MBRead", "_mbr"), ("MBWritten", "_mbw")):
            if origem not in d.columns:
                continue
            delta = d.groupby(chave, sort=False)[origem].diff()
            # delta negativo = contador reiniciado (restart da instancia):
            # adota o valor corrente, mesma convencao do padrao _total/_periodo
            # das demais coletas. O primeiro registro de cada arquivo fica nulo,
            # por nao ter anterior com que comparar.
            d[destino] = delta.mask(delta < 0, d[origem])
    else:
        return None

    agregacoes = {}
    if "_mbr" in d.columns:
        agregacoes["IO_MBRead"] = ("_mbr", "sum")
    if "_mbw" in d.columns:
        agregacoes["IO_MBWritten"] = ("_mbw", "sum")

    # ---- latencia: somente na forma por intervalo
    if "ReadLatencyPeriodoMs" in d.columns:
        agregacoes["IO_ReadLatencyMs"] = ("ReadLatencyPeriodoMs", "max")
    if "WriteLatencyPeriodoMs" in d.columns:
        agregacoes["IO_WriteLatencyMs"] = ("WriteLatencyPeriodoMs", "max")

    if not agregacoes:
        return None

    return d.groupby("DataColeta").agg(**agregacoes).resample(GRADE).mean()


def _colunas_atividade_sql(df_ativ):
    cols = [
        "BatchRequestsPeriodo", "SqlCompilationsPeriodo", "SqlReCompilationsPeriodo",
        "DeadlocksPeriodo", "PageSplitsPeriodo", "ConexoesAtivas", "SessoesBloqueadas",
    ]
    cols = [c for c in cols if c in df_ativ.columns]
    if not cols:
        return None
    return df_ativ.set_index("DataColeta")[cols].resample(GRADE).mean()


_BUILDERS = {
    "cpu": _colunas_cpu,
    "memoria": _colunas_memoria,
    "io": _colunas_io,
    "atividade_sql": _colunas_atividade_sql,
}


def montar_dataset(grupos_selecionados, df_cpu=None, df_mem=None, df_io=None, df_ativ=None):
    """Monta o dataframe único (indexado por tempo) a partir dos grupos escolhidos."""
    fontes = {"cpu": df_cpu, "memoria": df_mem, "io": df_io, "atividade_sql": df_ativ}
    partes = []
    for grupo in grupos_selecionados:
        builder = _BUILDERS.get(grupo)
        df_fonte = fontes.get(grupo)
        if builder is None or df_fonte is None or df_fonte.empty:
            continue
        parte = builder(df_fonte)
        if parte is not None and not parte.empty:
            partes.append(parte)

    if not partes:
        return pd.DataFrame()

    df = pd.concat(partes, axis=1).dropna(how="all").ffill().dropna()
    # remove colunas constantes (variância zero atrapalha e não informa)
    df = df.loc[:, df.nunique() > 1]
    return df


def detectar_anomalias(df, contamination=0.02, random_state=42):
    """
    Treina o Isolation Forest e devolve o dataframe com três colunas extras:
      - is_anomaly:       True/False
      - anomaly_score:    quanto menor, mais anômalo
      - metrica_destaque: a métrica que mais destoou (maior |z-score|) no ponto
    """
    if df.empty or len(df) < 20:
        out = df.copy()
        out["is_anomaly"] = False
        out["anomaly_score"] = 0.0
        out["metrica_destaque"] = None
        return out

    features = df.columns.tolist()
    scaler = StandardScaler()
    X = scaler.fit_transform(df[features])

    model = IsolationForest(
        contamination=contamination,
        n_estimators=200,
        random_state=random_state,
    )
    pred = model.fit_predict(X)  # -1 = anomalia, 1 = normal
    score = model.score_samples(X)  # menor = mais anômalo

    out = df.copy()
    out["is_anomaly"] = pred == -1
    out["anomaly_score"] = score

    medias = df[features].mean()
    desvios = df[features].std().replace(0, 1)
    z = (df[features] - medias) / desvios
    out["metrica_destaque"] = z.abs().idxmax(axis=1)

    return out
