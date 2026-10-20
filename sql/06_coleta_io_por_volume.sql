-- ============================================================
-- COLETA DE E/S POR VOLUME
--
-- Aplicar nas instancias JA provisionadas, DEPOIS de sql/05. Ajuste o USE:
-- LAB-SQL e SQLDEV usam DB_Observability; Prod  usa DBA_Monitor.
--
-- O PROBLEMA
--   obs.Historico_IO_Stats identifica o arquivo apenas por DatabaseName e
--   FileName (ex.: 'InfraFacil_Data'). Nao ha como saber em qual disco esse
--   arquivo esta, de modo que a latencia de E/S nao e atribuivel a um volume.
--
--   Ja obs.Historico_Discos grava o espaco livre por VolumeMountPoint ('F:\').
--   As duas coletas falam de disco e nao se ligam: o painel mostra "o volume F:
--   esta com 11% livre" e "o arquivo X esta com 40 ms de latencia" sem poder
--   dizer que X esta em F:.
--
-- A CORRECAO
--   Acrescenta o caminho fisico e o ponto de montagem a cada linha coletada.
--   O ponto de montagem vem de sys.dm_os_volume_stats, e nao de recorte de
--   string do caminho: alem de ser exato, devolve o mesmo formato ja gravado
--   em obs.Historico_Discos ('F:\'), o que permite juntar as duas series.
--
--   As colunas antigas permanecem intactas e o restante do procedimento nao
--   muda. Linhas ja gravadas ficam com Volume NULL -- o painel trata isso.
--
-- REQUISITOS
--   sys.dm_os_volume_stats existe a partir do SQL Server 2008 R2 e exige
--   VIEW SERVER STATE, permissao que a conta de coleta ja possui.
-- ============================================================

USE [DB_Observability];   -- <<< em Prod, trocar para [DBA_Monitor]
GO

-- ------------------------------------------------------------
-- PASSO 1 - colunas de localizacao fisica
-- ------------------------------------------------------------
IF COL_LENGTH('obs.Historico_IO_Stats', 'Volume') IS NULL
BEGIN
    ALTER TABLE obs.Historico_IO_Stats ADD
        CaminhoFisico NVARCHAR(260) NULL,
        Volume        NVARCHAR(10)  NULL;
    PRINT 'Colunas CaminhoFisico e Volume adicionadas a obs.Historico_IO_Stats.';
END
ELSE
    PRINT 'Colunas de localizacao fisica ja existem.';
GO

-- ------------------------------------------------------------
-- PASSO 2 - procedimento de coleta
--
-- Diferencas em relacao a sql/05:
--   * #atual passa a carregar CaminhoFisico e Volume;
--   * OUTER APPLY em sys.dm_os_volume_stats resolve o ponto de montagem
--     (OUTER, e nao CROSS, para nao perder a linha se o volume nao responder);
--   * o INSERT grava as duas colunas novas.
-- Todo o calculo de intervalo permanece como estava.
-- ------------------------------------------------------------
CREATE OR ALTER PROCEDURE obs.stp_Coletar_IO_Stats
AS
BEGIN
    SET NOCOUNT ON;

    CREATE TABLE #atual (
        DatabaseName SYSNAME NOT NULL,
        FileName     SYSNAME NOT NULL,
        FileType     VARCHAR(10) NOT NULL,
        CaminhoFisico NVARCHAR(260) NULL,
        Volume        NVARCHAR(10)  NULL,
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
        mf.physical_name,
        vs.volume_mount_point,
        vfs.num_of_reads, vfs.io_stall_read_ms,
        vfs.num_of_writes, vfs.io_stall_write_ms,
        vfs.num_of_bytes_read, vfs.num_of_bytes_written
    FROM sys.dm_io_virtual_file_stats(NULL, NULL) AS vfs
    INNER JOIN sys.master_files AS mf
        ON vfs.database_id = mf.database_id AND vfs.file_id = mf.file_id
    OUTER APPLY sys.dm_os_volume_stats(mf.database_id, mf.file_id) AS vs
    WHERE vfs.database_id > 4;

    INSERT INTO obs.Historico_IO_Stats (
        DatabaseName, FileName, FileType, CaminhoFisico, Volume,
        ReadLatencyMs, WriteLatencyMs, MBRead, MBWritten,
        ReadLatencyPeriodoMs, WriteLatencyPeriodoMs, MBReadPeriodo, MBWrittenPeriodo
    )
    SELECT
        a.DatabaseName, a.FileName, a.FileType, a.CaminhoFisico, a.Volume,

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
        NumOfWrites, IoStallWriteMs, BytesRead, BytesWritten, DataSnapshot
    ) VALUES (
        origem.DatabaseName, origem.FileName, origem.NumOfReads, origem.IoStallReadMs,
        origem.NumOfWrites, origem.IoStallWriteMs, origem.BytesRead, origem.BytesWritten,
        SYSDATETIME()
    );

    DROP TABLE #atual;
END
GO

-- ------------------------------------------------------------
-- PASSO 3 - conferencia
--
-- Execute a coleta uma vez e confirme que Volume veio preenchido. Espera-se
-- ver os mesmos pontos de montagem que aparecem em obs.Historico_Discos.
-- ------------------------------------------------------------
EXEC obs.stp_Coletar_IO_Stats;
GO

SELECT TOP 20
    DataColeta, DatabaseName, FileName, FileType, Volume, CaminhoFisico,
    ReadLatencyPeriodoMs, WriteLatencyPeriodoMs, MBReadPeriodo, MBWrittenPeriodo
FROM obs.Historico_IO_Stats
ORDER BY ID DESC;
GO

-- Volumes vistos pelas duas coletas. As duas colunas devem coincidir.
SELECT 'IO_Stats' AS Origem, Volume, COUNT(*) AS Arquivos
FROM obs.Historico_IO_Stats
WHERE Volume IS NOT NULL
  AND DataColeta >= DATEADD(MINUTE, -5, SYSDATETIME())
GROUP BY Volume
UNION ALL
SELECT 'Discos', VolumeMountPoint, COUNT(*)
FROM obs.Historico_Discos
WHERE DataColeta >= DATEADD(MINUTE, -20, SYSDATETIME())
GROUP BY VolumeMountPoint
ORDER BY Origem, Volume;
GO

-- Se Volume vier NULL em todas as linhas, a conta de coleta provavelmente nao
-- tem VIEW SERVER STATE suficiente para sys.dm_os_volume_stats. Verifique com:
--   SELECT * FROM sys.dm_os_volume_stats(DB_ID(), 1);
