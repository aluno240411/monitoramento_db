-- ============================================================
-- EXPERIMENTO DE AVALIACAO DO DETECTOR DE ANOMALIAS
-- 04 - Exportacao do log de injecoes (verdade de referencia)
--
-- Rode no SSMS e salve o grid como CSV em:
--     data/<alias>/injections.csv
--
-- No SSMS: clique com o botao direito no grid > "Save Results As..."
-- > CSV. Confirme em Tools > Options > Query Results > SQL Server
-- > Results to Grid que "Include column headers when copying or
-- saving the results" esta marcado, senao o CSV sai sem cabecalho.
--
-- Alternativa: acrescentar a linha abaixo ao dicionario `queries`
-- de app/db_connector.py, para que o painel extraia junto com as
-- demais metricas:
--
--   "injections.csv": f"SELECT * FROM obs.Log_Injecoes {date_filter} ORDER BY DataInicio ASC",
--
-- (naquele dicionario o filtro de data usa a coluna DataColeta, que
--  nao existe nesta tabela; para usar o filtro, troque por DataInicio.)
-- ============================================================

USE [DB_Observability];
GO

SELECT
    ID,
    ModoFalha,
    Intensidade,
    DataInicio,
    DataFim,
    DuracaoPlanejadaSeg,
    DuracaoRealSeg = DATEDIFF(SECOND, DataInicio, DataFim),
    Status,
    MensagemErro
FROM obs.Log_Injecoes
ORDER BY DataInicio ASC;
GO

-- ------------------------------------------------------------
-- Conferencia rapida do andamento do experimento
-- ------------------------------------------------------------
SELECT
    ModoFalha,
    Intensidade,
    Concluidas = SUM(CASE WHEN Status = 'CONCLUIDA'   THEN 1 ELSE 0 END),
    Erros      = SUM(CASE WHEN Status = 'ERRO'        THEN 1 ELSE 0 END),
    EmExecucao = SUM(CASE WHEN Status = 'EM_EXECUCAO' THEN 1 ELSE 0 END),
    DuracaoMediaSeg = AVG(DATEDIFF(SECOND, DataInicio, DataFim))
FROM obs.Log_Injecoes
GROUP BY ModoFalha, Intensidade
ORDER BY ModoFalha, Intensidade;
GO

SELECT
    Pendentes = SUM(CASE WHEN Executada = 0 THEN 1 ELSE 0 END),
    Executadas = SUM(CASE WHEN Executada = 1 THEN 1 ELSE 0 END),
    ProximaEm = MIN(CASE WHEN Executada = 0 THEN DataPlanejada END)
FROM obs.Plano_Injecoes;
GO

-- Cobertura da serie de metricas (para conferir se a coleta nao parou)
SELECT
    PrimeiraColeta = MIN(DataColeta),
    UltimaColeta   = MAX(DataColeta),
    Amostras       = COUNT(*),
    MinutosEsperados = DATEDIFF(MINUTE, MIN(DataColeta), MAX(DataColeta)) + 1,
    PctCobertura   = CAST(100.0 * COUNT(*)
                        / NULLIF(DATEDIFF(MINUTE, MIN(DataColeta), MAX(DataColeta)) + 1, 0)
                        AS DECIMAL(5,2))
FROM obs.Historico_CPU_Counters;
GO
