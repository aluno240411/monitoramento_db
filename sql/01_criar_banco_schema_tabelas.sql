-- ============================================================
-- DB_Observability — Criacao do banco, schema e tabelas de historico
-- Executar uma vez em cada instancia SQL Server monitorada.
-- Coleta 100% local (sem linked server / OPENQUERY).
-- ============================================================

USE [master];
GO

IF NOT EXISTS (SELECT name FROM sys.databases WHERE name = N'DB_Observability')
BEGIN
    CREATE DATABASE [DB_Observability];
END
GO

USE [DB_Observability];
GO

IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = N'obs')
BEGIN
    EXEC('CREATE SCHEMA [obs]');
END
GO

-- ------------------------------------------------------------
-- 1. CPU e contadores de memoria (coleta a cada 1 minuto)
--    LazyWrites segue o padrao _total/_periodo (sem WAITFOR):
--    _total = valor acumulado do contador; _periodo = consumo desde
--    a coleta anterior (LAG por DataColeta).
-- ------------------------------------------------------------
IF OBJECT_ID(N'obs.Historico_CPU_Counters', N'U') IS NULL
BEGIN
    CREATE TABLE obs.Historico_CPU_Counters (
        ID BIGINT IDENTITY(1,1) NOT NULL,
        DataColeta DATETIME2(0) NOT NULL DEFAULT GETDATE(),
        SQLServerCPU INT NOT NULL,
        SystemIdleCPU INT NOT NULL,
        OtherProcessCPU INT NOT NULL,
        PageLifeExpectancy BIGINT NULL,
        BufferCacheHitRatio DECIMAL(6,2) NULL,   -- 6,2 e nao 5,2: artefato de contador pode exceder 100%
        LazyWritesTotal BIGINT NULL,
        LazyWritesPeriodo BIGINT NULL,
        CONSTRAINT PK_Historico_CPU_Counters PRIMARY KEY CLUSTERED (ID)
    );
    CREATE NONCLUSTERED INDEX IX_Hist_CPU_DataColeta ON obs.Historico_CPU_Counters(DataColeta);
END
GO

-- ------------------------------------------------------------
-- 2. Atividade SQL (coleta a cada 1 minuto)
--    Contadores cumulativos no padrao _total/_periodo; valores
--    instantaneos (conexoes, bloqueios, transacoes) gravados direto.
-- ------------------------------------------------------------
IF OBJECT_ID(N'obs.Historico_AtividadeSQL', N'U') IS NULL
BEGIN
    CREATE TABLE obs.Historico_AtividadeSQL (
        ID BIGINT IDENTITY(1,1) NOT NULL,
        DataColeta DATETIME2(0) NOT NULL DEFAULT GETDATE(),
        BatchRequestsTotal BIGINT NULL,
        BatchRequestsPeriodo BIGINT NULL,
        SqlCompilationsTotal BIGINT NULL,
        SqlCompilationsPeriodo BIGINT NULL,
        SqlReCompilationsTotal BIGINT NULL,
        SqlReCompilationsPeriodo BIGINT NULL,
        DeadlocksTotal BIGINT NULL,
        DeadlocksPeriodo BIGINT NULL,
        PageSplitsTotal BIGINT NULL,
        PageSplitsPeriodo BIGINT NULL,
        ConexoesAtivas INT NULL,
        SessoesBloqueadas INT NULL,
        TransacoesAtivas INT NULL,
        CONSTRAINT PK_Historico_AtividadeSQL PRIMARY KEY CLUSTERED (ID)
    );
    CREATE NONCLUSTERED INDEX IX_Hist_AtividadeSQL_DataColeta ON obs.Historico_AtividadeSQL(DataColeta);
END
GO

-- ------------------------------------------------------------
-- 3. Memoria (Memory Clerks) — coleta a cada 1 minuto
-- ------------------------------------------------------------
IF OBJECT_ID(N'obs.Historico_Memoria', N'U') IS NULL
BEGIN
    CREATE TABLE obs.Historico_Memoria (
        ID BIGINT IDENTITY(1,1) NOT NULL,
        DataColeta DATETIME2(0) NOT NULL DEFAULT GETDATE(),
        ClerkType VARCHAR(60) NOT NULL,
        MemoriaMB DECIMAL(12,2) NOT NULL,
        CONSTRAINT PK_Historico_Memoria PRIMARY KEY CLUSTERED (ID)
    );
    CREATE NONCLUSTERED INDEX IX_Hist_Memoria_DataColeta ON obs.Historico_Memoria(DataColeta);
END
GO

-- ------------------------------------------------------------
-- 4. Wait Statistics (deltas de espera) — coleta a cada 1 minuto
-- ------------------------------------------------------------
IF OBJECT_ID(N'obs.Historico_WaitStats', N'U') IS NULL
BEGIN
    CREATE TABLE obs.Historico_WaitStats (
        ID BIGINT IDENTITY(1,1) NOT NULL,
        DataColeta DATETIME2(0) NOT NULL DEFAULT GETDATE(),
        WaitType NVARCHAR(60) NOT NULL,
        WaitingTasksCount BIGINT NOT NULL,
        WaitTimeMs BIGINT NOT NULL,
        SignalWaitTimeMs BIGINT NOT NULL,
        DeltaWaitTimeMs BIGINT NOT NULL,
        CONSTRAINT PK_Historico_WaitStats PRIMARY KEY CLUSTERED (ID)
    );
    CREATE NONCLUSTERED INDEX IX_Hist_WaitStats_DataColeta ON obs.Historico_WaitStats(DataColeta, WaitType);
END
GO

IF OBJECT_ID(N'obs.Snapshot_WaitStats', N'U') IS NULL
BEGIN
    CREATE TABLE obs.Snapshot_WaitStats (
        WaitType NVARCHAR(60) PRIMARY KEY,
        WaitingTasksCount BIGINT NOT NULL,
        WaitTimeMs BIGINT NOT NULL,
        SignalWaitTimeMs BIGINT NOT NULL,
        DataSnapshot DATETIME2(0) NOT NULL
    );
END
GO

-- ------------------------------------------------------------
-- 5. I/O Virtual File Stats — coleta a cada 1 minuto
-- ------------------------------------------------------------
IF OBJECT_ID(N'obs.Historico_IO_Stats', N'U') IS NULL
BEGIN
    CREATE TABLE obs.Historico_IO_Stats (
        ID BIGINT IDENTITY(1,1) NOT NULL,
        DataColeta DATETIME2(0) NOT NULL DEFAULT GETDATE(),
        DatabaseName SYSNAME NOT NULL,
        FileName SYSNAME NOT NULL,
        FileType VARCHAR(10) NOT NULL,
        ReadLatencyMs BIGINT NOT NULL,
        WriteLatencyMs BIGINT NOT NULL,
        MBRead DECIMAL(12,2) NOT NULL,
        MBWritten DECIMAL(12,2) NOT NULL,
        -- Valores do INTERVALO. A DMV sys.dm_io_virtual_file_stats e cumulativa
        -- desde a inicializacao; as colunas acima guardam o acumulado e estas,
        -- o consumo desde a coleta anterior do mesmo arquivo.
        MBReadPeriodo DECIMAL(12,2) NULL,
        MBWrittenPeriodo DECIMAL(12,2) NULL,
        ReadLatencyPeriodoMs BIGINT NULL,
        WriteLatencyPeriodoMs BIGINT NULL,
        CONSTRAINT PK_Historico_IO_Stats PRIMARY KEY CLUSTERED (ID)
    );
    CREATE NONCLUSTERED INDEX IX_Hist_IO_DataColeta ON obs.Historico_IO_Stats(DataColeta, DatabaseName);
END
GO


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
END
GO

-- ------------------------------------------------------------
-- 6. Top Queries com classificacao ABC — coleta a cada 5 minutos
--    Mesma logica do dba-monitor: filtra por last_execution_time desde
--    a ultima coleta, ordena pela metrica da ULTIMA execucao dentro de
--    cada categoria (CPU/IO/DURACAO), classifica por volume de execucao
--    (A ate 80%, B ate 95%, C o restante) e grava o Top 10 de cada
--    categoria dentro de cada classe.
-- ------------------------------------------------------------
IF OBJECT_ID(N'obs.Historico_TopQueries', N'U') IS NULL
BEGIN
    CREATE TABLE obs.Historico_TopQueries (
        ID BIGINT IDENTITY(1,1) NOT NULL,
        DataColeta DATETIME2(0) NOT NULL DEFAULT GETDATE(),
        Categoria VARCHAR(20) NOT NULL,       -- CPU, IO, DURACAO
        ClasseABC CHAR(1) NOT NULL,           -- A / B / C
        PctExecucoesAcumulado DECIMAL(5,2) NOT NULL,
        ExecutionCount BIGINT NOT NULL,
        LastWorkerTimeMs BIGINT NOT NULL,
        LastLogicalReads BIGINT NOT NULL,
        LastElapsedTimeMs BIGINT NOT NULL,
        AvgWorkerTimeMs BIGINT NOT NULL,
        AvgLogicalReads BIGINT NOT NULL,
        AvgElapsedTimeMs BIGINT NOT NULL,
        TotalWorkerTimeMs BIGINT NOT NULL,
        TotalLogicalReads BIGINT NOT NULL,
        TotalElapsedTimeMs BIGINT NOT NULL,
        QueryText NVARCHAR(MAX) NULL,
        DatabaseName SYSNAME NULL,
        CONSTRAINT PK_Historico_TopQueries PRIMARY KEY CLUSTERED (ID)
    );
    CREATE NONCLUSTERED INDEX IX_Hist_TopQueries_DataColeta ON obs.Historico_TopQueries(DataColeta);
END
GO

-- ------------------------------------------------------------
-- 7. Capacidade de Discos — coleta a cada 15 minutos
-- ------------------------------------------------------------
IF OBJECT_ID(N'obs.Historico_Discos', N'U') IS NULL
BEGIN
    CREATE TABLE obs.Historico_Discos (
        ID BIGINT IDENTITY(1,1) NOT NULL,
        DataColeta DATETIME2(0) NOT NULL DEFAULT GETDATE(),
        VolumeMountPoint NVARCHAR(256) NOT NULL,
        VolumeName NVARCHAR(256) NULL,
        TotalGB DECIMAL(12,2) NOT NULL,
        AvailableGB DECIMAL(12,2) NOT NULL,
        FreePercent DECIMAL(5,2) NOT NULL,
        CONSTRAINT PK_Historico_Discos PRIMARY KEY CLUSTERED (ID)
    );
    CREATE NONCLUSTERED INDEX IX_Hist_Discos_DataColeta ON obs.Historico_Discos(DataColeta);
END
GO

-- ------------------------------------------------------------
-- 8. Falhas em Jobs do SQL Agent — coleta a cada 15 minutos
-- ------------------------------------------------------------
IF OBJECT_ID(N'obs.Historico_FalhasJobs', N'U') IS NULL
BEGIN
    CREATE TABLE obs.Historico_FalhasJobs (
        ID BIGINT IDENTITY(1,1) NOT NULL,
        DataColeta DATETIME2(0) NOT NULL DEFAULT GETDATE(),
        JobName SYSNAME NOT NULL,
        StepName SYSNAME NOT NULL,
        DataExecucao DATETIME2(0) NOT NULL,
        DuracaoSegundos INT NOT NULL,
        MensagemErro NVARCHAR(MAX) NULL,
        CONSTRAINT PK_Historico_FalhasJobs PRIMARY KEY CLUSTERED (ID)
    );
    CREATE NONCLUSTERED INDEX IX_Hist_FalhasJobs_DataColeta ON obs.Historico_FalhasJobs(DataColeta);
END
GO

PRINT 'Banco DB_Observability, schema obs e tabelas criados com sucesso.';
GO
