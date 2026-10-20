-- ============================================================
-- EXPERIMENTO DE AVALIACAO DO DETECTOR DE ANOMALIAS
-- 01 - Procedures de injecao controlada de falhas
--
-- Quatro modos de falha, um para cada grupo de metricas que o
-- app/ml_engine.py combina no Isolation Forest:
--
--   CPU       -> SQLServerCPU
--   MEMORIA   -> PageLifeExpectancy, BufferCacheHitRatio, LazyWrites
--   IO        -> MBRead/MBWritten, ReadLatencyMs/WriteLatencyMs
--   BLOQUEIO  -> SessoesBloqueadas, ConexoesAtivas
--
-- Toda procedure abre uma linha em obs.Log_Injecoes ao iniciar e
-- a fecha ao terminar. Esse par (DataInicio, DataFim) e o rotulo.
-- ============================================================

USE [DB_Observability];
GO

-- ------------------------------------------------------------
-- Helpers de log
-- ------------------------------------------------------------
CREATE OR ALTER PROCEDURE obs.stp_Exp_Log_Inicio
    @ModoFalha   VARCHAR(20),
    @Intensidade TINYINT,
    @DuracaoSeg  INT,
    @IDLog       BIGINT OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO obs.Log_Injecoes (ModoFalha, Intensidade, DataInicio, DuracaoPlanejadaSeg, Status)
    VALUES (@ModoFalha, @Intensidade, SYSDATETIME(), @DuracaoSeg, 'EM_EXECUCAO');
    SET @IDLog = SCOPE_IDENTITY();
END
GO

CREATE OR ALTER PROCEDURE obs.stp_Exp_Log_Fim
    @IDLog   BIGINT,
    @Status  VARCHAR(20) = 'CONCLUIDA',
    @Erro    NVARCHAR(2000) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE obs.Log_Injecoes
       SET DataFim = SYSDATETIME(), Status = @Status, MensagemErro = @Erro
     WHERE ID = @IDLog;
END
GO

-- ------------------------------------------------------------
-- MODO 1: CPU
-- Laco de calculo matematico em ponto flutuante. Mesma logica de
-- sql/simular_pico_cpu.sql, agora instrumentada.
-- ------------------------------------------------------------
CREATE OR ALTER PROCEDURE obs.stp_Injetar_CPU
    @Intensidade TINYINT = 2,
    @DuracaoSeg  INT     = 180
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @IDLog BIGINT, @fim DATETIME2(0), @i BIGINT, @r FLOAT, @iter BIGINT;

    SET @iter = CASE @Intensidade WHEN 1 THEN 500000 WHEN 3 THEN 5000000 ELSE 2000000 END;
    EXEC obs.stp_Exp_Log_Inicio 'CPU', @Intensidade, @DuracaoSeg, @IDLog OUTPUT;

    BEGIN TRY
        SET @fim = DATEADD(SECOND, @DuracaoSeg, SYSDATETIME());
        WHILE SYSDATETIME() < @fim
        BEGIN
            SET @i = 0; SET @r = 0;
            WHILE @i < @iter AND SYSDATETIME() < @fim
            BEGIN
                SET @r = @r + SQRT(CAST(@i AS FLOAT) + 1.0)
                             * LOG(CAST(@i AS FLOAT) + 2.0)
                             / (SIN(CAST(@i AS FLOAT) * 0.0001) + 1.5);
                SET @i = @i + 1;
            END
        END
        EXEC obs.stp_Exp_Log_Fim @IDLog, 'CONCLUIDA';
    END TRY
    BEGIN CATCH
        DECLARE @e1 NVARCHAR(2000) = ERROR_MESSAGE();
        EXEC obs.stp_Exp_Log_Fim @IDLog, 'ERRO', @e1;
    END CATCH
END
GO

-- ------------------------------------------------------------
-- MODO 2: MEMORIA (pressao de buffer pool)
-- Varreduras completas repetidas da area de carga, com ordenacao
-- para forcar concessao de memoria. Derruba Page Life Expectancy
-- e Buffer Cache Hit Ratio e eleva Lazy Writes.
-- ------------------------------------------------------------
CREATE OR ALTER PROCEDURE obs.stp_Injetar_Memoria
    @Intensidade TINYINT = 2,
    @DuracaoSeg  INT     = 180
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @IDLog BIGINT, @fim DATETIME2(0), @lixo FLOAT, @topo BIGINT;

    -- fracao da tabela varrida por passada
    SET @topo = CASE @Intensidade WHEN 1 THEN 50000 WHEN 3 THEN 100000000 ELSE 500000 END;
    EXEC obs.stp_Exp_Log_Inicio 'MEMORIA', @Intensidade, @DuracaoSeg, @IDLog OUTPUT;

    BEGIN TRY
        IF NOT EXISTS (SELECT 1 FROM obs.Scratch_Carga)
            RAISERROR('obs.Scratch_Carga vazia. Rode obs.stp_Exp_Popular_Scratch primeiro.', 16, 1);

        SET @fim = DATEADD(SECOND, @DuracaoSeg, SYSDATETIME());
        WHILE SYSDATETIME() < @fim
        BEGIN
            -- varredura completa: traz paginas para o buffer pool
            SELECT @lixo = SUM(Valor)
            FROM (SELECT TOP (@topo) Valor FROM obs.Scratch_Carga WITH (INDEX(0), FORCESCAN)
                  ORDER BY Chave DESC) q;

            IF SYSDATETIME() >= @fim BREAK;

            -- ordenacao pesada: pede concessao de memoria (memory grant)
            SELECT @lixo = SUM(Valor)
            FROM (SELECT TOP (@topo) Valor, Enchimento
                  FROM obs.Scratch_Carga WITH (INDEX(0), FORCESCAN)
                  ORDER BY Valor DESC, Enchimento DESC) q2;
        END
        EXEC obs.stp_Exp_Log_Fim @IDLog, 'CONCLUIDA';
    END TRY
    BEGIN CATCH
        DECLARE @e2 NVARCHAR(2000) = ERROR_MESSAGE();
        EXEC obs.stp_Exp_Log_Fim @IDLog, 'ERRO', @e2;
    END CATCH
END
GO

-- ------------------------------------------------------------
-- MODO 3: I/O
-- Ciclo de escrita (INSERT + CHECKPOINT) e leitura (varredura),
-- elevando MBRead/MBWritten e as latencias por arquivo.
-- ------------------------------------------------------------
CREATE OR ALTER PROCEDURE obs.stp_Injetar_IO
    @Intensidade TINYINT = 2,
    @DuracaoSeg  INT     = 180
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @IDLog BIGINT, @fim DATETIME2(0), @lixo FLOAT, @lote INT;

    SET @lote = CASE @Intensidade WHEN 1 THEN 5000 WHEN 3 THEN 100000 ELSE 25000 END;
    EXEC obs.stp_Exp_Log_Inicio 'IO', @Intensidade, @DuracaoSeg, @IDLog OUTPUT;

    BEGIN TRY
        IF OBJECT_ID(N'obs.Scratch_IO', N'U') IS NULL
            CREATE TABLE obs.Scratch_IO (
                ID BIGINT IDENTITY(1,1) PRIMARY KEY,
                Valor FLOAT NOT NULL,
                Enchimento CHAR(2000) NOT NULL
            );

        SET @fim = DATEADD(SECOND, @DuracaoSeg, SYSDATETIME());
        WHILE SYSDATETIME() < @fim
        BEGIN
            INSERT INTO obs.Scratch_IO (Valor, Enchimento)
            SELECT TOP (@lote) RAND(CHECKSUM(NEWID())) * 1000, REPLICATE('y', 2000)
            FROM sys.all_columns a CROSS JOIN sys.all_columns b;

            CHECKPOINT;

            SELECT @lixo = SUM(Valor) FROM obs.Scratch_IO WITH (INDEX(0), FORCESCAN);

            DELETE TOP (@lote) FROM obs.Scratch_IO;
            CHECKPOINT;
        END

        TRUNCATE TABLE obs.Scratch_IO;
        EXEC obs.stp_Exp_Log_Fim @IDLog, 'CONCLUIDA';
    END TRY
    BEGIN CATCH
        DECLARE @e3 NVARCHAR(2000) = ERROR_MESSAGE();
        EXEC obs.stp_Exp_Log_Fim @IDLog, 'ERRO', @e3;
    END CATCH
END
GO

-- ------------------------------------------------------------
-- MODO 4: BLOQUEIO
-- Precisa de DUAS sessoes. O job DBAExp_Bloqueador (criado em
-- 03_job_orquestrador.sql) segura a transacao; esta procedure
-- assume o papel de VITIMA e fica bloqueada.
--
-- A duracao minima util e 120 s: a coleta roda a cada 1 minuto e
-- e preciso que o bloqueio exista no instante de pelo menos uma
-- coleta.
-- ------------------------------------------------------------
CREATE OR ALTER PROCEDURE obs.stp_Injetar_Bloqueio
    @Intensidade TINYINT = 2,
    @DuracaoSeg  INT     = 180
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @IDLog BIGINT, @erro NVARCHAR(2000) = NULL, @status VARCHAR(20) = 'CONCLUIDA';

    IF @DuracaoSeg < 120 SET @DuracaoSeg = 120;
    EXEC obs.stp_Exp_Log_Inicio 'BLOQUEIO', @Intensidade, @DuracaoSeg, @IDLog OUTPUT;

    BEGIN TRY
        -- o bloqueador le a duracao da linha EM_EXECUCAO mais recente
        EXEC msdb.dbo.sp_start_job @job_name = N'DBAExp_Bloqueador';
        WAITFOR DELAY '00:00:05';   -- deixa o bloqueador pegar o lock

        -- espera no maximo a duracao + folga, e desiste sem erro fatal
        SET LOCK_TIMEOUT 300000;    -- 300 s

        BEGIN TRANSACTION;
            UPDATE obs.Scratch_Bloqueio SET Contador = Contador + 1 WHERE ID = 1;
        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        -- 1222 = lock request time out: esperado se o bloqueador durar mais
        IF ERROR_NUMBER() <> 1222
        BEGIN
            SET @status = 'ERRO';
            SET @erro = ERROR_MESSAGE();
        END
    END CATCH

    SET LOCK_TIMEOUT -1;
    EXEC obs.stp_Exp_Log_Fim @IDLog, @status, @erro;
END
GO

PRINT 'Procedures de injecao criadas.';
GO
