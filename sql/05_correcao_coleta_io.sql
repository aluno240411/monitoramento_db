-- ============================================================
-- CORRECAO DA COLETA DE ESTATISTICAS DE E/S
--
-- Aplicar nas instancias JA provisionadas. Ajuste o USE:
-- LAB-SQL e SQLDEV usam DB_Observability; Prod usa DBA_Monitor.
--
-- O PROBLEMA
--   sys.dm_io_virtual_file_stats e CUMULATIVA desde a inicializacao da
--   instancia. A coleta gravava seus valores crus, de modo que:
--
--   ReadLatencyMs = io_stall_read_ms / num_of_reads sobre os TOTAIS, ou seja,
--   a latencia media desde que o servidor subiu -- nao a do minuto. Em LAB-SQL
--   um arquivo apresentou 3 valores distintos em 14 dias (64, 65 e 66 ms),
--   permanentemente acima do limiar critico de 25 ms. Como o modelo toma o
--   MAXIMO entre os arquivos, a regra de limiar disparava em 100% dos minutos.
--
--   MBRead/MBWritten eram totais acumulados, crescendo monotonicamente. Um
--   atributo monotonico codifica o TEMPO, nao a carga, e faz o detector
--   distinguir inicio de fim da serie em vez de normal de anomalo.
--
-- A CORRECAO
--   Aplica a esta coleta o mesmo padrao _total/_periodo que as demais ja
--   usam, apoiado numa tabela de snapshot -- como obs.Snapshot_WaitStats.
--   As colunas antigas sao mantidas (o painel as utiliza) e quatro novas
--   passam a trazer o valor do INTERVALO.
--
-- Latencia do intervalo fica NULL quando nao houve E/S no periodo: a latencia
-- de zero leituras e indefinida, nao zero.
--
-- NAO recalcula o historico ja gravado: a latencia por intervalo e
-- irrecuperavel a posteriori, porque so a razao foi persistida.
-- ============================================================

USE [DB_Observability];   -- <<< em Prod, trocar para [DBA_Monitor]
GO

-- ------------------------------------------------------------
-- PASSO 1 - colunas de intervalo
-- ------------------------------------------------------------
IF COL_LENGTH('obs.Historico_IO_Stats', 'MBReadPeriodo') IS NULL
BEGIN
    ALTER TABLE obs.Historico_IO_Stats ADD
        MBReadPeriodo         DECIMAL(12,2) NULL,
        MBWrittenPeriodo      DECIMAL(12,2) NULL,
        ReadLatencyPeriodoMs  BIGINT        NULL,
        WriteLatencyPeriodoMs BIGINT        NULL;
    PRINT 'Colunas de intervalo adicionadas a obs.Historico_IO_Stats.';
END
ELSE
    PRINT 'Colunas de intervalo ja existem.';
GO

-- ------------------------------------------------------------
-- PASSO 2 - snapshot do estado anterior, por arquivo
-- ------------------------------------------------------------
IF OBJECT_ID(N'obs.Snapshot_IO_Stats', N'U') IS NULL
BEGIN
    CREATE TABLE obs.Snapshot_IO_Stats (
        DatabaseName   SYSNAME NOT NULL,
        FileName       SYSNAME NOT NULL,
        NumOfReads     BIGINT  NOT NULL,
        IoStallReadMs  BIGINT  NOT NULL,
        NumOfWrites    BIGINT  NOT NULL,
        IoStallWriteMs BIGINT  NOT NULL,
        BytesRead      BIGINT  NOT NULL,
        BytesWritten   BIGINT  NOT NULL,
        DataSnapshot   DATETIME2(0) NOT NULL,
        CONSTRAINT PK_Snapshot_IO_Stats PRIMARY KEY CLUSTERED (DatabaseName, FileName)
    );
    PRINT 'Tabela obs.Snapshot_IO_Stats criada.';
END
GO

-- ------------------------------------------------------------
-- PASSO 3 - procedure de coleta
-- ------------------------------------------------------------
CREATE OR ALTER PROCEDURE obs.stp_Coletar_IO_Stats
AS
BEGIN
    SET NOCOUNT ON;

    CREATE TABLE #atual (
        DatabaseName SYSNAME NOT NULL,
        FileName     SYSNAME NOT NULL,
        FileType     VARCHAR(10) NOT NULL,
        NumOfReads     BIGINT NOT NULL,
        IoStallReadMs  BIGINT NOT NULL,
        NumOfWrites    BIGINT NOT NULL,
        IoStallWriteMs BIGINT NOT NULL,
        BytesRead      BIGINT NOT NULL,
        BytesWritten   BIGINT NOT NULL,
        PRIMARY KEY (DatabaseName, FileName)
    );

    INSERT INTO #atual
    SELECT
        DB_NAME(vfs.database_id), mf.name, mf.type_desc,
        vfs.num_of_reads, vfs.io_stall_read_ms,
        vfs.num_of_writes, vfs.io_stall_write_ms,
        vfs.num_of_bytes_read, vfs.num_of_bytes_written
    FROM sys.dm_io_virtual_file_stats(NULL, NULL) AS vfs
    INNER JOIN sys.master_files AS mf
        ON vfs.database_id = mf.database_id AND vfs.file_id = mf.file_id
    WHERE vfs.database_id > 4;

    INSERT INTO obs.Historico_IO_Stats (
        DatabaseName, FileName, FileType,
        ReadLatencyMs, WriteLatencyMs, MBRead, MBWritten,
        ReadLatencyPeriodoMs, WriteLatencyPeriodoMs, MBReadPeriodo, MBWrittenPeriodo
    )
    SELECT
        a.DatabaseName, a.FileName, a.FileType,

        -- acumulados desde a inicializacao (mantidos por compatibilidade)
        CASE WHEN a.NumOfReads  = 0 THEN 0 ELSE a.IoStallReadMs  / a.NumOfReads  END,
        CASE WHEN a.NumOfWrites = 0 THEN 0 ELSE a.IoStallWriteMs / a.NumOfWrites END,
        CAST(a.BytesRead    / 1048576.0 AS DECIMAL(12,2)),
        CAST(a.BytesWritten / 1048576.0 AS DECIMAL(12,2)),

        -- do intervalo. NULL na primeira coleta de cada arquivo (sem anterior),
        -- quando o contador regrediu (reinicio da instancia) ou quando nao
        -- houve E/S no periodo (latencia de zero operacoes e indefinida).
        CASE WHEN s.DatabaseName IS NULL             THEN NULL
             WHEN a.NumOfReads  <= s.NumOfReads      THEN NULL
             ELSE (a.IoStallReadMs  - s.IoStallReadMs)  / (a.NumOfReads  - s.NumOfReads)  END,
        CASE WHEN s.DatabaseName IS NULL             THEN NULL
             WHEN a.NumOfWrites <= s.NumOfWrites     THEN NULL
             ELSE (a.IoStallWriteMs - s.IoStallWriteMs) / (a.NumOfWrites - s.NumOfWrites) END,
        CASE WHEN s.DatabaseName IS NULL             THEN NULL
             WHEN a.BytesRead    < s.BytesRead       THEN CAST(a.BytesRead    / 1048576.0 AS DECIMAL(12,2))
             ELSE CAST((a.BytesRead    - s.BytesRead)    / 1048576.0 AS DECIMAL(12,2)) END,
        CASE WHEN s.DatabaseName IS NULL             THEN NULL
             WHEN a.BytesWritten < s.BytesWritten    THEN CAST(a.BytesWritten / 1048576.0 AS DECIMAL(12,2))
             ELSE CAST((a.BytesWritten - s.BytesWritten) / 1048576.0 AS DECIMAL(12,2)) END
    FROM #atual AS a
    LEFT JOIN obs.Snapshot_IO_Stats AS s
        ON s.DatabaseName = a.DatabaseName AND s.FileName = a.FileName;

    -- atualiza o snapshot para a proxima coleta
    MERGE obs.Snapshot_IO_Stats AS destino
    USING #atual AS origem
        ON destino.DatabaseName = origem.DatabaseName
       AND destino.FileName     = origem.FileName
    WHEN MATCHED THEN UPDATE SET
        NumOfReads = origem.NumOfReads, IoStallReadMs  = origem.IoStallReadMs,
        NumOfWrites= origem.NumOfWrites, IoStallWriteMs = origem.IoStallWriteMs,
        BytesRead  = origem.BytesRead,  BytesWritten   = origem.BytesWritten,
        DataSnapshot = SYSDATETIME()
    WHEN NOT MATCHED BY TARGET THEN INSERT (
        DatabaseName, FileName, NumOfReads, IoStallReadMs,
        NumOfWrites, IoStallWriteMs, BytesRead, BytesWritten, DataSnapshot)
    VALUES (
        origem.DatabaseName, origem.FileName, origem.NumOfReads, origem.IoStallReadMs,
        origem.NumOfWrites, origem.IoStallWriteMs, origem.BytesRead, origem.BytesWritten,
        SYSDATETIME())
    WHEN NOT MATCHED BY SOURCE THEN DELETE;   -- arquivo removido do servidor

    DROP TABLE #atual;
END
GO

PRINT 'Procedure obs.stp_Coletar_IO_Stats atualizada.';
GO

-- ------------------------------------------------------------
-- PASSO 4 - verificacao
-- A PRIMEIRA execucao grava NULL nas colunas de intervalo (nao ha
-- anterior com que comparar). A partir da SEGUNDA, os valores aparecem.
-- ------------------------------------------------------------
EXEC obs.stp_Coletar_IO_Stats;
WAITFOR DELAY '00:00:10';
EXEC obs.stp_Coletar_IO_Stats;
GO

SELECT TOP (10)
    DataColeta, DatabaseName, FileName,
    LatenciaAcumulada = ReadLatencyMs,
    LatenciaIntervalo = ReadLatencyPeriodoMs,
    MBAcumulado       = MBRead,
    MBIntervalo       = MBReadPeriodo
FROM obs.Historico_IO_Stats
ORDER BY DataColeta DESC, MBReadPeriodo DESC;
GO
