-- ============================================================
-- DB_Observability — Stored Procedures de Coleta (100% local, sem
-- linked server). Cada procedure e chamada por um SQL Agent Job.
-- ============================================================

USE [DB_Observability];
GO

-- ------------------------------------------------------------
-- SP 1: CPU e Contadores — chamar a cada 1 minuto
--    LazyWrites segue o padrao _total/_periodo: le o total acumulado
--    atual, subtrai o total gravado na coleta anterior. Sem WAITFOR.
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

-- ------------------------------------------------------------
-- SP 2: Atividade SQL — chamar a cada 1 minuto
--    Batch requests, compilacoes, recompilacoes, deadlocks e page
--    splits seguem o padrao _total/_periodo (sem WAITFOR).
-- ------------------------------------------------------------
CREATE OR ALTER PROCEDURE obs.stp_Coletar_AtividadeSQL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @batch BIGINT, @compil BIGINT, @recompil BIGINT, @dead BIGINT, @splits BIGINT;
    SELECT
        @batch    = MAX(CASE WHEN counter_name = 'Batch Requests/sec'      THEN cntr_value END),
        @compil   = MAX(CASE WHEN counter_name = 'SQL Compilations/sec'    THEN cntr_value END),
        @recompil = MAX(CASE WHEN counter_name = 'SQL Re-Compilations/sec' THEN cntr_value END),
        @dead     = MAX(CASE WHEN counter_name = 'Number of Deadlocks/sec' THEN cntr_value END),
        @splits   = MAX(CASE WHEN counter_name = 'Page Splits/sec'         THEN cntr_value END)
    FROM sys.dm_os_performance_counters
    WHERE counter_name IN ('Batch Requests/sec','SQL Compilations/sec',
          'SQL Re-Compilations/sec','Number of Deadlocks/sec','Page Splits/sec')
      AND instance_name IN ('', '_Total');

    DECLARE @batch_ant BIGINT, @compil_ant BIGINT, @recompil_ant BIGINT,
            @dead_ant BIGINT, @splits_ant BIGINT;
    SELECT TOP 1
        @batch_ant    = BatchRequestsTotal,
        @compil_ant   = SqlCompilationsTotal,
        @recompil_ant = SqlReCompilationsTotal,
        @dead_ant     = DeadlocksTotal,
        @splits_ant   = PageSplitsTotal
    FROM obs.Historico_AtividadeSQL
    ORDER BY DataColeta DESC;

    INSERT INTO obs.Historico_AtividadeSQL (
        BatchRequestsTotal, BatchRequestsPeriodo,
        SqlCompilationsTotal, SqlCompilationsPeriodo,
        SqlReCompilationsTotal, SqlReCompilationsPeriodo,
        DeadlocksTotal, DeadlocksPeriodo,
        PageSplitsTotal, PageSplitsPeriodo,
        ConexoesAtivas, SessoesBloqueadas, TransacoesAtivas
    )
    SELECT
        @batch,    CASE WHEN @batch_ant    IS NULL THEN NULL WHEN @batch    >= @batch_ant    THEN @batch    - @batch_ant    ELSE @batch    END,
        @compil,   CASE WHEN @compil_ant   IS NULL THEN NULL WHEN @compil   >= @compil_ant   THEN @compil   - @compil_ant   ELSE @compil   END,
        @recompil, CASE WHEN @recompil_ant IS NULL THEN NULL WHEN @recompil >= @recompil_ant THEN @recompil - @recompil_ant ELSE @recompil END,
        @dead,     CASE WHEN @dead_ant     IS NULL THEN NULL WHEN @dead     >= @dead_ant     THEN @dead     - @dead_ant     ELSE @dead     END,
        @splits,   CASE WHEN @splits_ant   IS NULL THEN NULL WHEN @splits   >= @splits_ant   THEN @splits   - @splits_ant   ELSE @splits   END,
        (SELECT COUNT(*) FROM sys.dm_exec_sessions WHERE is_user_process = 1),
        (SELECT COUNT(*) FROM sys.dm_exec_requests  WHERE blocking_session_id > 0),
        (SELECT COUNT(*) FROM sys.dm_tran_active_transactions);
END
GO

-- ------------------------------------------------------------
-- SP 3: Memoria (Memory Clerks) — chamar a cada 1 minuto
-- ------------------------------------------------------------
CREATE OR ALTER PROCEDURE obs.stp_Coletar_Memoria
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO obs.Historico_Memoria (ClerkType, MemoriaMB)
    SELECT TOP 10
        type,
        CAST(SUM(pages_kb) / 1024.0 AS DECIMAL(12,2)) AS MemoriaMB
    FROM sys.dm_os_memory_clerks
    GROUP BY type
    HAVING SUM(pages_kb) / 1024.0 > 10.0
    ORDER BY MemoriaMB DESC;
END
GO

-- ------------------------------------------------------------
-- SP 4: Wait Statistics — chamar a cada 1 minuto
-- ------------------------------------------------------------
CREATE OR ALTER PROCEDURE obs.stp_Coletar_WaitStats
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @DataAtual DATETIME2(0) = GETDATE();

    INSERT INTO obs.Historico_WaitStats (WaitType, WaitingTasksCount, WaitTimeMs, SignalWaitTimeMs, DeltaWaitTimeMs, DataColeta)
    SELECT
        w.wait_type,
        w.waiting_tasks_count,
        w.wait_time_ms,
        w.signal_wait_time_ms,
        (w.wait_time_ms - s.WaitTimeMs) AS DeltaWaitTimeMs,
        @DataAtual
    FROM sys.dm_os_wait_stats w
    INNER JOIN obs.Snapshot_WaitStats s ON w.wait_type = s.WaitType
    WHERE (w.wait_time_ms - s.WaitTimeMs) > 100
      AND w.wait_type NOT IN (
          'CLR_SEMAPHORE', 'LAZYWRITER_SLEEP', 'RESOURCE_QUEUE', 'SLEEP_TASK',
          'SLEEP_SYSTEMTASK', 'SQLTRACE_BUFFER_FLUSH', 'WAITFOR', 'LOGMGR_QUEUE',
          'CHECKPOINT_QUEUE', 'REQUEST_FOR_DEADLOCK_SEARCH', 'XE_TIMER_EVENT',
          'BROKER_TO_FLUSH', 'BROKER_TASK_STOP', 'CLR_MANUAL_EVENT', 'CLR_AUTO_EVENT',
          'DISPATCHER_QUEUE_SEMAPHORE', 'FT_IFTS_SCHEDULER_IDLE_WAIT', 'XE_DISPATCHER_WAIT'
      );

    MERGE obs.Snapshot_WaitStats AS target
    USING (
        SELECT wait_type, waiting_tasks_count, wait_time_ms, signal_wait_time_ms
        FROM sys.dm_os_wait_stats
    ) AS source
    ON (target.WaitType = source.wait_type)
    WHEN MATCHED THEN
        UPDATE SET
            target.WaitingTasksCount = source.waiting_tasks_count,
            target.WaitTimeMs = source.wait_time_ms,
            target.SignalWaitTimeMs = source.signal_wait_time_ms,
            target.DataSnapshot = @DataAtual
    WHEN NOT MATCHED THEN
        INSERT (WaitType, WaitingTasksCount, WaitTimeMs, SignalWaitTimeMs, DataSnapshot)
        VALUES (source.wait_type, source.waiting_tasks_count, source.wait_time_ms, source.signal_wait_time_ms, @DataAtual);
END
GO

-- ------------------------------------------------------------
-- SP 5: I/O Virtual File Stats — chamar a cada 1 minuto
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

-- ------------------------------------------------------------
-- SP 6: Top Queries com classificacao ABC — chamar a cada 5 minutos
--    Filtra por last_execution_time desde a ultima coleta, classifica
--    por volume de execucao (curva ABC) e grava o Top 10 de cada
--    categoria (CPU/IO/DURACAO) dentro de cada classe (A/B/C).
-- ------------------------------------------------------------
CREATE OR ALTER PROCEDURE obs.stp_Coletar_TopQueries
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @agora DATETIME2(0) = GETDATE();
    DECLARE @ultima DATETIME2(0);
    SELECT @ultima = MAX(DataColeta) FROM obs.Historico_TopQueries;
    IF @ultima IS NULL
        SET @ultima = DATEADD(MINUTE, -5, GETDATE());

    IF OBJECT_ID('tempdb..#base') IS NOT NULL DROP TABLE #base;
    IF OBJECT_ID('tempdb..#classif') IS NOT NULL DROP TABLE #classif;

    SELECT
        qs.execution_count AS ExecutionCount,
        qs.last_worker_time AS LastWorkerTime,
        qs.last_logical_reads AS LastLogicalReads,
        qs.last_elapsed_time AS LastElapsedTime,
        qs.total_worker_time AS TotalWorkerTime,
        qs.total_logical_reads AS TotalLogicalReads,
        qs.total_elapsed_time AS TotalElapsedTime,
        SUBSTRING(st.text, (qs.statement_start_offset/2) + 1,
            ((CASE qs.statement_end_offset
                WHEN -1 THEN DATALENGTH(st.text)
                ELSE qs.statement_end_offset END
                - qs.statement_start_offset)/2) + 1) AS QueryText,
        DB_NAME(st.dbid) AS DatabaseName
    INTO #base
    FROM sys.dm_exec_query_stats qs
    CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) st
    WHERE qs.last_execution_time >= @ultima;

    ;WITH abc AS (
        SELECT *,
            PctAcum = CAST(
                CAST(SUM(ExecutionCount) OVER (ORDER BY ExecutionCount DESC ROWS UNBOUNDED PRECEDING) AS FLOAT)
                * 100.0 / NULLIF(SUM(ExecutionCount) OVER (), 0)
                AS DECIMAL(5,2))
        FROM #base
    )
    SELECT *,
        ClasseABC = CASE WHEN PctAcum <= 80 THEN 'A'
                         WHEN PctAcum <= 95 THEN 'B'
                         ELSE 'C' END
    INTO #classif
    FROM abc;

    -- CPU: ordena por LastWorkerTime
    INSERT INTO obs.Historico_TopQueries (
        DataColeta, Categoria, ClasseABC, PctExecucoesAcumulado, ExecutionCount,
        LastWorkerTimeMs, LastLogicalReads, LastElapsedTimeMs,
        AvgWorkerTimeMs, AvgLogicalReads, AvgElapsedTimeMs,
        TotalWorkerTimeMs, TotalLogicalReads, TotalElapsedTimeMs, QueryText, DatabaseName)
    SELECT @agora, 'CPU', ClasseABC, PctAcum, ExecutionCount,
        LastWorkerTime/1000, LastLogicalReads, LastElapsedTime/1000,
        TotalWorkerTime/ExecutionCount/1000, TotalLogicalReads/ExecutionCount, TotalElapsedTime/ExecutionCount/1000,
        TotalWorkerTime/1000, TotalLogicalReads, TotalElapsedTime/1000, QueryText, DatabaseName
    FROM (SELECT *, rn = ROW_NUMBER() OVER (PARTITION BY ClasseABC ORDER BY LastWorkerTime DESC) FROM #classif) x
    WHERE rn <= 10;

    -- IO: ordena por LastLogicalReads
    INSERT INTO obs.Historico_TopQueries (
        DataColeta, Categoria, ClasseABC, PctExecucoesAcumulado, ExecutionCount,
        LastWorkerTimeMs, LastLogicalReads, LastElapsedTimeMs,
        AvgWorkerTimeMs, AvgLogicalReads, AvgElapsedTimeMs,
        TotalWorkerTimeMs, TotalLogicalReads, TotalElapsedTimeMs, QueryText, DatabaseName)
    SELECT @agora, 'IO', ClasseABC, PctAcum, ExecutionCount,
        LastWorkerTime/1000, LastLogicalReads, LastElapsedTime/1000,
        TotalWorkerTime/ExecutionCount/1000, TotalLogicalReads/ExecutionCount, TotalElapsedTime/ExecutionCount/1000,
        TotalWorkerTime/1000, TotalLogicalReads, TotalElapsedTime/1000, QueryText, DatabaseName
    FROM (SELECT *, rn = ROW_NUMBER() OVER (PARTITION BY ClasseABC ORDER BY LastLogicalReads DESC) FROM #classif) x
    WHERE rn <= 10;

    -- DURACAO: ordena por LastElapsedTime
    INSERT INTO obs.Historico_TopQueries (
        DataColeta, Categoria, ClasseABC, PctExecucoesAcumulado, ExecutionCount,
        LastWorkerTimeMs, LastLogicalReads, LastElapsedTimeMs,
        AvgWorkerTimeMs, AvgLogicalReads, AvgElapsedTimeMs,
        TotalWorkerTimeMs, TotalLogicalReads, TotalElapsedTimeMs, QueryText, DatabaseName)
    SELECT @agora, 'DURACAO', ClasseABC, PctAcum, ExecutionCount,
        LastWorkerTime/1000, LastLogicalReads, LastElapsedTime/1000,
        TotalWorkerTime/ExecutionCount/1000, TotalLogicalReads/ExecutionCount, TotalElapsedTime/ExecutionCount/1000,
        TotalWorkerTime/1000, TotalLogicalReads, TotalElapsedTime/1000, QueryText, DatabaseName
    FROM (SELECT *, rn = ROW_NUMBER() OVER (PARTITION BY ClasseABC ORDER BY LastElapsedTime DESC) FROM #classif) x
    WHERE rn <= 10;

    DROP TABLE #base;
    DROP TABLE #classif;
END
GO

-- ------------------------------------------------------------
-- SP 7: Capacidade de Discos — chamar a cada 15 minutos
-- ------------------------------------------------------------
CREATE OR ALTER PROCEDURE obs.stp_Coletar_Discos
AS
BEGIN
    SET NOCOUNT ON;

    INSERT INTO obs.Historico_Discos (VolumeMountPoint, VolumeName, TotalGB, AvailableGB, FreePercent)
    SELECT DISTINCT
        vs.volume_mount_point,
        vs.logical_volume_name,
        CAST(vs.total_bytes / 1073741824.0 AS DECIMAL(12,2)) AS TotalGB,
        CAST(vs.available_bytes / 1073741824.0 AS DECIMAL(12,2)) AS AvailableGB,
        CAST((vs.available_bytes * 100.0 / NULLIF(vs.total_bytes, 0)) AS DECIMAL(5,2)) AS FreePercent
    FROM sys.master_files AS mf
    CROSS APPLY sys.dm_os_volume_stats(mf.database_id, mf.file_id) AS vs;
END
GO

-- ------------------------------------------------------------
-- SP 8: Falhas em Jobs do SQL Agent (ultimas 24h) — a cada 15 minutos
-- ------------------------------------------------------------
CREATE OR ALTER PROCEDURE obs.stp_Coletar_FalhasJobs
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Ultimas24h INT = CONVERT(INT, CONVERT(VARCHAR(8), DATEADD(DAY, -1, GETDATE()), 112));
    DECLARE @Hora24h INT = CONVERT(INT, REPLACE(CONVERT(VARCHAR(8), DATEADD(DAY, -1, GETDATE()), 108), ':', ''));

    INSERT INTO obs.Historico_FalhasJobs (JobName, StepName, DataExecucao, DuracaoSegundos, MensagemErro)
    SELECT
        j.name AS JobName,
        h.step_name AS StepName,
        msdb.dbo.agent_datetime(h.run_date, h.run_time) AS DataExecucao,
        ((h.run_duration/10000 * 3600) + ((h.run_duration%10000)/100 * 60) + (h.run_duration%100)) AS DuracaoSegundos,
        LEFT(h.message, 4000) AS MensagemErro
    FROM msdb.dbo.sysjobhistory h
    INNER JOIN msdb.dbo.sysjobs j ON h.job_id = j.job_id
    WHERE h.run_status = 0 -- 0 = Falha
      AND h.step_id <> 0
      AND (h.run_date > @Ultimas24h OR (h.run_date = @Ultimas24h AND h.run_time >= @Hora24h))
      AND NOT EXISTS (
          SELECT 1 FROM obs.Historico_FalhasJobs f
          WHERE f.JobName = j.name
            AND f.StepName = h.step_name
            AND f.DataExecucao = msdb.dbo.agent_datetime(h.run_date, h.run_time)
      );
END
GO

-- ------------------------------------------------------------
-- SP 9: Limpeza e Retencao de Dados (30 dias)
-- ------------------------------------------------------------
CREATE OR ALTER PROCEDURE obs.stp_Limpeza_Historico
    @DiasRetencao INT = 30
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @DataLimite DATETIME2(0) = DATEADD(DAY, -@DiasRetencao, GETDATE());

    DELETE FROM obs.Historico_CPU_Counters WHERE DataColeta < @DataLimite;
    DELETE FROM obs.Historico_AtividadeSQL WHERE DataColeta < @DataLimite;
    DELETE FROM obs.Historico_Memoria WHERE DataColeta < @DataLimite;
    DELETE FROM obs.Historico_WaitStats WHERE DataColeta < @DataLimite;
    DELETE FROM obs.Historico_IO_Stats WHERE DataColeta < @DataLimite;
    DELETE FROM obs.Historico_TopQueries WHERE DataColeta < @DataLimite;
    DELETE FROM obs.Historico_Discos WHERE DataColeta < @DataLimite;
    DELETE FROM obs.Historico_FalhasJobs WHERE DataColeta < @DataLimite;

    PRINT 'Limpeza concluida. Registros anteriores a ' + CAST(@DataLimite AS VARCHAR) + ' removidos.';
END
GO

PRINT 'Stored procedures criadas com sucesso.';
GO
