-- ============================================================
-- EXPERIMENTO DE AVALIACAO DO DETECTOR DE ANOMALIAS
-- 00 - Infraestrutura: log das injecoes, plano e area de carga
--
-- Executar UMA VEZ na instancia monitorada, DEPOIS de
-- sql/01_criar_banco_schema_tabelas.sql.
--
-- A tabela obs.Log_Injecoes e a VERDADE DE REFERENCIA do
-- experimento: cada injecao registra inicio e fim, e o script
-- de avaliacao rotula os minutos por interseccao com esses
-- intervalos. Nao existe anotacao manual.
-- ============================================================

USE [DB_Observability];
GO

-- ------------------------------------------------------------
-- 1. Log das injecoes efetivamente executadas
-- ------------------------------------------------------------
IF OBJECT_ID(N'obs.Log_Injecoes', N'U') IS NULL
BEGIN
    CREATE TABLE obs.Log_Injecoes (
        ID BIGINT IDENTITY(1,1) NOT NULL,
        ModoFalha VARCHAR(20) NOT NULL,      -- CPU | MEMORIA | IO | BLOQUEIO
        Intensidade TINYINT NOT NULL,        -- 1 leve | 2 media | 3 pesada
        DataInicio DATETIME2(0) NOT NULL,
        DataFim DATETIME2(0) NULL,
        DuracaoPlanejadaSeg INT NOT NULL,
        Status VARCHAR(20) NOT NULL CONSTRAINT DF_Log_Injecoes_Status DEFAULT 'EM_EXECUCAO',
        MensagemErro NVARCHAR(2000) NULL,
        CONSTRAINT PK_Log_Injecoes PRIMARY KEY CLUSTERED (ID)
    );
    CREATE NONCLUSTERED INDEX IX_Log_Injecoes_DataInicio ON obs.Log_Injecoes(DataInicio);
END
GO

-- ------------------------------------------------------------
-- 2. Plano de injecoes (delineamento pre-registrado)
-- ------------------------------------------------------------
IF OBJECT_ID(N'obs.Plano_Injecoes', N'U') IS NULL
BEGIN
    CREATE TABLE obs.Plano_Injecoes (
        ID INT IDENTITY(1,1) NOT NULL,
        DataPlanejada DATETIME2(0) NOT NULL,
        ModoFalha VARCHAR(20) NOT NULL,
        Intensidade TINYINT NOT NULL,
        DuracaoSeg INT NOT NULL,
        Executada BIT NOT NULL CONSTRAINT DF_Plano_Injecoes_Executada DEFAULT 0,
        IDLogInjecao BIGINT NULL,
        CONSTRAINT PK_Plano_Injecoes PRIMARY KEY CLUSTERED (ID)
    );
    CREATE NONCLUSTERED INDEX IX_Plano_Injecoes_Pendentes ON obs.Plano_Injecoes(Executada, DataPlanejada);
END
GO

-- ------------------------------------------------------------
-- 3. Area de carga para as injecoes de memoria e I/O
--    Linha de ~2 KB para que o volume em disco/buffer pool
--    cresca rapido com poucas linhas.
-- ------------------------------------------------------------
IF OBJECT_ID(N'obs.Scratch_Carga', N'U') IS NULL
BEGIN
    CREATE TABLE obs.Scratch_Carga (
        ID BIGINT IDENTITY(1,1) NOT NULL,
        Chave INT NOT NULL,
        Valor FLOAT NOT NULL,
        Enchimento CHAR(2000) NOT NULL CONSTRAINT DF_Scratch_Enchimento DEFAULT REPLICATE('x', 2000),
        CONSTRAINT PK_Scratch_Carga PRIMARY KEY CLUSTERED (ID)
    );
    CREATE NONCLUSTERED INDEX IX_Scratch_Carga_Chave ON obs.Scratch_Carga(Chave);
END
GO

-- ------------------------------------------------------------
-- 4. Linha unica disputada pela injecao de bloqueio
-- ------------------------------------------------------------
IF OBJECT_ID(N'obs.Scratch_Bloqueio', N'U') IS NULL
BEGIN
    CREATE TABLE obs.Scratch_Bloqueio (
        ID INT NOT NULL CONSTRAINT PK_Scratch_Bloqueio PRIMARY KEY,
        Contador INT NOT NULL
    );
    INSERT INTO obs.Scratch_Bloqueio (ID, Contador) VALUES (1, 0);
END
GO

-- ------------------------------------------------------------
-- 5. Popula a area de carga
--    @LinhasMil = 300 gera ~300 mil linhas de 2 KB (~600 MB).
--    Dimensione ACIMA da memoria maxima do SQL Server do lab,
--    senao a tabela cabe inteira no buffer pool e a injecao de
--    memoria nao produz pressao alguma.
--    Consulte: SELECT value_in_use FROM sys.configurations
--              WHERE name = 'max server memory (MB)';
-- ------------------------------------------------------------
CREATE OR ALTER PROCEDURE obs.stp_Exp_Popular_Scratch
    @LinhasMil INT = 300
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @alvo  BIGINT = CAST(@LinhasMil AS BIGINT) * 1000;
    DECLARE @atual BIGINT = (SELECT COUNT_BIG(*) FROM obs.Scratch_Carga);

    WHILE @atual < @alvo
    BEGIN
        INSERT INTO obs.Scratch_Carga (Chave, Valor)
        SELECT TOP (50000)
               ABS(CHECKSUM(NEWID())) % 100000,
               RAND(CHECKSUM(NEWID())) * 1000
        FROM sys.all_columns a
        CROSS JOIN sys.all_columns b;

        SET @atual = @atual + 50000;
        RAISERROR('obs.Scratch_Carga: %I64d linhas', 0, 1, @atual) WITH NOWAIT;
    END

    RAISERROR('Concluido. Tamanho:', 0, 1) WITH NOWAIT;
    SELECT
        NomeTabela   = 'obs.Scratch_Carga',
        Linhas       = COUNT_BIG(*),
        TamanhoMB    = CAST(SUM(DATALENGTH(Enchimento) + 20) / 1048576.0 AS DECIMAL(12,2))
    FROM obs.Scratch_Carga;
END
GO

PRINT 'Infraestrutura do experimento criada.';
GO
