-- ============================================================
-- CORRECAO DA COLETA DE CONTADORES DE CPU E MEMORIA
--
-- Aplicar nas instancias JA provisionadas (LAB-SQL, SQLDEV, Prod).
-- Para instancias novas nao e necessario: as correcoes ja estao em
-- 01_criar_banco_schema_tabelas.sql e 02_stored_procedures_coleta.sql.
--
-- ATENCAO AO BANCO: em LAB-SQL e SQLDEV o banco e DB_Observability;
-- em Prod e DBA_Monitor. Ajuste o USE abaixo antes de executar.
--
-- O QUE MUDA
--   1. obs.Historico_CPU_Counters.BufferCacheHitRatio passa de
--      DECIMAL(5,2) para DECIMAL(6,2). Ambas as precisoes ocupam 5 bytes,
--      entao a alteracao e apenas de metadados: nao reescreve a tabela.
--   2. obs.stp_Coletar_CPU_Counters passa a ler os quatro contadores do
--      Buffer Manager numa UNICA varredura de sys.dm_os_performance_counters,
--      por agregacao condicional, em vez de tres consultas com auto-join.
--
-- POR QUE
--   'Buffer cache hit ratio' e do tipo PERF_LARGE_RAW_FRACTION e precisa ser
--   dividido por 'Buffer cache hit ratio base' -- essa parte estava correta.
--   O problema era o auto-join: numerador e denominador eram lidos em pontos
--   distintos da mesma varredura da DMV e, quando a base ficava defasada, a
--   razao ultrapassava 100%. Foram observados 821,95% e 648,51% em SQLDEV e
--   904,91% em Prod, em 4 de 22.670 registros.
--   Como a coluna era DECIMAL(5,2) (maximo 999,99), um artefato acima disso
--   levantaria Arithmetic overflow. O passo do job DBAMon_Coleta_Performance
--   executa as cinco procedures de coleta num unico batch, de modo que o erro
--   abortaria TODAS as coletas daquele minuto, e nao apenas esta.
--
--   A correcao tambem sana um defeito latente: 'Lazy writes/sec' era lido
--   apenas por instance_name, sem filtrar object_name, e existe tanto sob
--   Buffer Manager quanto sob Buffer Node -- em servidores NUMA o valor podia
--   vir do objeto errado.
--
-- NAO ALTERA os 4 registros historicos ja gravados: eles sao evidencia do
-- artefato e o tratamento cabe a camada de analise.
--
-- REVERSAO: os arquivos originais estao em 01_...sql.bak e 02_...sql.bak.
-- ============================================================

USE [DB_Observability];   -- <<< em Prod, trocar para [DBA_Monitor]
GO

-- ------------------------------------------------------------
-- PASSO 0 (opcional) - diagnostico ANTES da correcao.
-- Mostra os contadores crus. Rode algumas vezes: se em alguma
-- execucao Base < Valor, o artefato acaba de ser reproduzido.
-- ------------------------------------------------------------
SELECT
    Contador   = RTRIM(counter_name),
    Objeto     = RTRIM(object_name),
    cntr_value,
    cntr_type
FROM sys.dm_os_performance_counters
WHERE object_name LIKE '%Buffer Manager%'
  AND counter_name IN ('Page life expectancy', 'Buffer cache hit ratio',
                       'Buffer cache hit ratio base', 'Lazy writes/sec');
GO

-- ------------------------------------------------------------
-- PASSO 1 - alarga a coluna (metadados apenas)
-- ------------------------------------------------------------
IF EXISTS (
    SELECT 1 FROM sys.columns c
    JOIN sys.objects o ON o.object_id = c.object_id
    WHERE o.name = 'Historico_CPU_Counters'
      AND SCHEMA_NAME(o.schema_id) = 'obs'
      AND c.name = 'BufferCacheHitRatio'
      AND c.precision = 5
)
BEGIN
    ALTER TABLE obs.Historico_CPU_Counters
        ALTER COLUMN BufferCacheHitRatio DECIMAL(6,2) NULL;
    PRINT 'Coluna BufferCacheHitRatio alterada para DECIMAL(6,2).';
END
ELSE
    PRINT 'Coluna BufferCacheHitRatio ja esta com a precisao correta.';
GO

-- ------------------------------------------------------------
-- PASSO 2 - substitui a procedure de coleta
-- ------------------------------------------------------------
CREATE OR ALTER PROCEDURE obs.stp_Coletar_CPU_Counters
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @SQLCPU INT, @SystemIdle INT, @OtherCPU INT;

    SELECT TOP(1)
        @SQLCPU = SQLProcessUtilization,
        @SystemIdle = SystemIdle,
        @OtherCPU = (100 - SystemIdle - SQLProcessUtilization)
    FROM (
        SELECT
            record.value('(./Record/@id)[1]', 'int') AS record_id,
            record.value('(./Record/SchedulerMonitorEvent/SystemHealth/SystemIdle)[1]', 'int') AS SystemIdle,
            record.value('(./Record/SchedulerMonitorEvent/SystemHealth/ProcessUtilization)[1]', 'int') AS SQLProcessUtilization
        FROM (
            SELECT CAST(record AS XML) AS record
            FROM sys.dm_os_ring_buffers
            WHERE ring_buffer_type = N'RING_BUFFER_SCHEDULER_MONITOR'
            AND record LIKE '%<SystemHealth>%'
        ) AS x
    ) AS y
    ORDER BY record_id DESC;

    -- Leitura UNICA da DMV para os quatro contadores do Buffer Manager.
    -- O auto-join anterior lia numerador e denominador em pontos distintos da
    -- mesma varredura, o que produzia razoes acima de 100% (observados 821,95%,
    -- 648,51% e 904,91%). A agregacao condicional le tudo num so instante.
    -- O filtro por object_name tambem corrige 'Lazy writes/sec', que antes
    -- podia ser lido do objeto Buffer Node em vez do Buffer Manager.
    DECLARE @PLE BIGINT, @BufferHit DECIMAL(6,2), @LazyAtual BIGINT;
    DECLARE @BchrValor BIGINT, @BchrBase BIGINT, @Ratio DECIMAL(19,4);

    SELECT
        @PLE       = MAX(CASE WHEN counter_name = 'Page life expectancy'        THEN cntr_value END),
        @BchrValor = MAX(CASE WHEN counter_name = 'Buffer cache hit ratio'      THEN cntr_value END),
        @BchrBase  = MAX(CASE WHEN counter_name = 'Buffer cache hit ratio base' THEN cntr_value END),
        @LazyAtual = MAX(CASE WHEN counter_name = 'Lazy writes/sec'             THEN cntr_value END)
    FROM sys.dm_os_performance_counters
    WHERE object_name LIKE '%Buffer Manager%'
      AND counter_name IN ('Page life expectancy', 'Buffer cache hit ratio',
                           'Buffer cache hit ratio base', 'Lazy writes/sec');

    -- Guarda contra divisao por zero e contra estouro do DECIMAL(6,2), que
    -- abortaria o batch das cinco coletas do job DBAMon_Coleta_Performance.
    SET @Ratio = CASE WHEN ISNULL(@BchrBase, 0) = 0 THEN NULL
                      ELSE @BchrValor * 100.0 / @BchrBase END;
    SET @BufferHit = CASE WHEN @Ratio IS NULL   THEN NULL
                          WHEN @Ratio > 9999.99 THEN 9999.99
                          ELSE CAST(@Ratio AS DECIMAL(6,2)) END;

    -- _total/_periodo: le o ultimo total gravado, calcula o delta desde entao.
    DECLARE @LazyAnt BIGINT;
    SELECT TOP 1 @LazyAnt = LazyWritesTotal FROM obs.Historico_CPU_Counters ORDER BY DataColeta DESC;

    INSERT INTO obs.Historico_CPU_Counters (
        SQLServerCPU, SystemIdleCPU, OtherProcessCPU,
        PageLifeExpectancy, BufferCacheHitRatio,
        LazyWritesTotal, LazyWritesPeriodo
    )
    VALUES (
        ISNULL(@SQLCPU, 0), ISNULL(@SystemIdle, 0), ISNULL(@OtherCPU, 0),
        @PLE, @BufferHit,
        @LazyAtual,
        CASE WHEN @LazyAnt IS NULL THEN NULL
             WHEN @LazyAtual >= @LazyAnt THEN @LazyAtual - @LazyAnt
             ELSE @LazyAtual END
    );
END
GO

PRINT 'Procedure obs.stp_Coletar_CPU_Counters atualizada.';
GO

-- ------------------------------------------------------------
-- PASSO 3 - verificacao. Execute a coleta uma vez e confira o
-- valor gravado. Deve estar entre 0 e 100.
-- ------------------------------------------------------------
EXEC obs.stp_Coletar_CPU_Counters;
GO

SELECT TOP (5)
    DataColeta, SQLServerCPU, PageLifeExpectancy,
    BufferCacheHitRatio, LazyWritesTotal, LazyWritesPeriodo
FROM obs.Historico_CPU_Counters
ORDER BY DataColeta DESC;
GO

-- Ocorrencias historicas do artefato (nao sao alteradas por este script):
SELECT
    Registros    = COUNT(*),
    PrimeiraData = MIN(DataColeta),
    UltimaData   = MAX(DataColeta),
    MaiorValor   = MAX(BufferCacheHitRatio)
FROM obs.Historico_CPU_Counters
WHERE BufferCacheHitRatio > 100;
GO
