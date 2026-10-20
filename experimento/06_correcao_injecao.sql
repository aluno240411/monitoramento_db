-- ============================================================
-- CORRECAO DOS MODOS DE INJECAO DE CPU E DE BLOQUEIO
--
-- Aplicar na instancia de laboratorio, sobre 00/01/02/03 ja executados.
-- Ajuste o USE se a instancia usar outro banco de observabilidade.
--
-- O PROBLEMA, medido na primeira janela (10 a 16/09, 48 injecoes)
--
--   CPU: o laco rodava em UMA sessao, portanto em um nucleo. A utilizacao
--   registrada foi de 6% nas 12 injecoes, IDENTICA nas intensidades 1, 2 e 3 --
--   o parametro variava as iteracoes por ciclo, nao o paralelismo. O limiar de
--   aviso e 70%: inalcancavel por construcao, e o fator intensidade do
--   delineamento fatorial ficou nulo.
--
--   BLOQUEIO: um bloqueador e uma vitima produzem sempre 1 sessao bloqueada,
--   em qualquer intensidade. O limiar de aviso e "> 1", estritamente maior --
--   tambem inalcancavel por construcao.
--
-- A CORRECAO
--   Ambos passam a escalar o PARALELISMO com a intensidade:
--     CPU      -> 25%, 50% ou 100% dos nucleos da instancia (sys.dm_os_sys_info)
--     BLOQUEIO -> 2, 4 ou 8 sessoes vitimas simultaneas
--   O paralelismo efetivamente realizado e gravado no log, para constar da
--   descricao do delineamento.
-- ============================================================

USE [DB_Observability];   -- <<< ajustar se necessario
GO

DECLARE @db SYSNAME = DB_NAME();
PRINT 'Banco alvo: ' + @db;
GO

-- ------------------------------------------------------------
-- PASSO 1 - registrar o paralelismo realizado
-- ------------------------------------------------------------
IF COL_LENGTH('obs.Log_Injecoes', 'Paralelismo') IS NULL
BEGIN
    ALTER TABLE obs.Log_Injecoes ADD Paralelismo INT NULL;
    PRINT 'Coluna Paralelismo adicionada a obs.Log_Injecoes.';
END
GO

-- ------------------------------------------------------------
-- PASSO 2 - trabalhadores
-- ------------------------------------------------------------
CREATE OR ALTER PROCEDURE obs.stp_Exp_Carga_CPU_Worker
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @dur INT, @fim DATETIME2(0), @i BIGINT, @r FLOAT, @iter BIGINT = 2000000;

    SELECT TOP (1) @dur = DuracaoPlanejadaSeg
    FROM obs.Log_Injecoes
    WHERE ModoFalha = 'CPU' AND Status = 'EM_EXECUCAO'
    ORDER BY ID DESC;

    IF @dur IS NULL RETURN;          -- ninguem pediu carga
    IF @dur > 600 SET @dur = 600;    -- teto de seguranca

    SET @fim = DATEADD(SECOND, @dur, SYSDATETIME());
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
END
GO

CREATE OR ALTER PROCEDURE obs.stp_Exp_Bloqueio_Vitima
AS
BEGIN
    SET NOCOUNT ON;
    SET LOCK_TIMEOUT 600000;   -- 10 min
    BEGIN TRY
        BEGIN TRANSACTION;
            UPDATE obs.Scratch_Bloqueio SET Contador = Contador + 1 WHERE ID = 1;
        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;   -- 1222 esperado
    END CATCH
END
GO

-- ------------------------------------------------------------
-- PASSO 3 - pool de jobs trabalhadores, sem agenda
-- ------------------------------------------------------------
DECLARE @db SYSNAME = DB_NAME();
DECLARE @nucleos INT = (SELECT CASE WHEN cpu_count > 32 THEN 32 ELSE cpu_count END
                        FROM sys.dm_os_sys_info);
DECLARE @i INT, @nome NVARCHAR(128);

PRINT 'Nucleos detectados na instancia: ' + CAST(@nucleos AS VARCHAR(10));

IF NOT EXISTS (SELECT 1 FROM msdb.dbo.syscategories WHERE name = N'DBA_Experimento')
    EXEC msdb.dbo.sp_add_category @class = N'JOB', @type = N'LOCAL', @name = N'DBA_Experimento';

-- carga de CPU: um job por nucleo
SET @i = 1;
WHILE @i <= @nucleos
BEGIN
    SET @nome = N'DBAExp_CargaCPU_' + RIGHT('00' + CAST(@i AS VARCHAR(3)), 2);
    IF EXISTS (SELECT 1 FROM msdb.dbo.sysjobs WHERE name = @nome)
        EXEC msdb.dbo.sp_delete_job @job_name = @nome, @delete_unused_schedule = 1;
    EXEC msdb.dbo.sp_add_job @job_name = @nome, @enabled = 1,
        @description = N'Trabalhador de carga de CPU do experimento. Sem agenda.',
        @category_name = N'DBA_Experimento', @notify_level_eventlog = 0;
    EXEC msdb.dbo.sp_add_jobstep @job_name = @nome, @step_name = N'Carga',
        @step_id = 1, @subsystem = N'TSQL', @database_name = @db,
        @command = N'EXEC obs.stp_Exp_Carga_CPU_Worker;';
    EXEC msdb.dbo.sp_add_jobserver @job_name = @nome;
    SET @i = @i + 1;
END

-- vitimas de bloqueio: oito jobs
SET @i = 1;
WHILE @i <= 8
BEGIN
    SET @nome = N'DBAExp_Vitima_' + RIGHT('00' + CAST(@i AS VARCHAR(3)), 2);
    IF EXISTS (SELECT 1 FROM msdb.dbo.sysjobs WHERE name = @nome)
        EXEC msdb.dbo.sp_delete_job @job_name = @nome, @delete_unused_schedule = 1;
    EXEC msdb.dbo.sp_add_job @job_name = @nome, @enabled = 1,
        @description = N'Sessao vitima de bloqueio do experimento. Sem agenda.',
        @category_name = N'DBA_Experimento', @notify_level_eventlog = 0;
    EXEC msdb.dbo.sp_add_jobstep @job_name = @nome, @step_name = N'Disputar lock',
        @step_id = 1, @subsystem = N'TSQL', @database_name = @db,
        @command = N'EXEC obs.stp_Exp_Bloqueio_Vitima;';
    EXEC msdb.dbo.sp_add_jobserver @job_name = @nome;
    SET @i = @i + 1;
END

PRINT 'Pool de trabalhadores criado.';
GO

-- ------------------------------------------------------------
-- PASSO 4 - injecao de CPU com paralelismo
-- ------------------------------------------------------------
CREATE OR ALTER PROCEDURE obs.stp_Injetar_CPU
    @Intensidade TINYINT = 2,
    @DuracaoSeg  INT     = 180
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @IDLog BIGINT, @nucleos INT, @workers INT, @i INT = 1, @nome NVARCHAR(128);

    SET @nucleos = (SELECT CASE WHEN cpu_count > 32 THEN 32 ELSE cpu_count END
                    FROM sys.dm_os_sys_info);

    -- a intensidade passa a escalar o PARALELISMO, nao as iteracoes
    SET @workers = CASE @Intensidade
                       WHEN 1 THEN CEILING(@nucleos * 0.25)
                       WHEN 3 THEN @nucleos
                       ELSE        CEILING(@nucleos * 0.50) END;
    IF @workers < 1 SET @workers = 1;

    EXEC obs.stp_Exp_Log_Inicio 'CPU', @Intensidade, @DuracaoSeg, @IDLog OUTPUT;
    UPDATE obs.Log_Injecoes SET Paralelismo = @workers WHERE ID = @IDLog;

    BEGIN TRY
        WHILE @i <= @workers
        BEGIN
            SET @nome = N'DBAExp_CargaCPU_' + RIGHT('00' + CAST(@i AS VARCHAR(3)), 2);
            BEGIN TRY
                EXEC msdb.dbo.sp_start_job @job_name = @nome;
            END TRY
            BEGIN CATCH
                -- job ja em execucao: segue com os demais
            END CATCH
            SET @i = @i + 1;
        END

        DECLARE @espera CHAR(8) = CONVERT(CHAR(8),
            DATEADD(SECOND, @DuracaoSeg + 10, CAST('00:00:00' AS TIME)), 108);
        WAITFOR DELAY @espera;

        EXEC obs.stp_Exp_Log_Fim @IDLog, 'CONCLUIDA';
    END TRY
    BEGIN CATCH
        DECLARE @e NVARCHAR(2000) = ERROR_MESSAGE();
        EXEC obs.stp_Exp_Log_Fim @IDLog, 'ERRO', @e;
    END CATCH
END
GO

-- ------------------------------------------------------------
-- PASSO 5 - injecao de bloqueio com multiplas vitimas
-- ------------------------------------------------------------
CREATE OR ALTER PROCEDURE obs.stp_Injetar_Bloqueio
    @Intensidade TINYINT = 2,
    @DuracaoSeg  INT     = 180
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @IDLog BIGINT, @vitimas INT, @i INT = 1, @nome NVARCHAR(128);

    IF @DuracaoSeg < 120 SET @DuracaoSeg = 120;
    SET @vitimas = CASE @Intensidade WHEN 1 THEN 2 WHEN 3 THEN 8 ELSE 4 END;

    EXEC obs.stp_Exp_Log_Inicio 'BLOQUEIO', @Intensidade, @DuracaoSeg, @IDLog OUTPUT;
    UPDATE obs.Log_Injecoes SET Paralelismo = @vitimas WHERE ID = @IDLog;

    BEGIN TRY
        EXEC msdb.dbo.sp_start_job @job_name = N'DBAExp_Bloqueador';
        WAITFOR DELAY '00:00:05';   -- deixa o bloqueador pegar o lock

        WHILE @i <= @vitimas
        BEGIN
            SET @nome = N'DBAExp_Vitima_' + RIGHT('00' + CAST(@i AS VARCHAR(3)), 2);
            BEGIN TRY
                EXEC msdb.dbo.sp_start_job @job_name = @nome;
            END TRY
            BEGIN CATCH
            END CATCH
            SET @i = @i + 1;
        END

        DECLARE @espera CHAR(8) = CONVERT(CHAR(8),
            DATEADD(SECOND, @DuracaoSeg, CAST('00:00:00' AS TIME)), 108);
        WAITFOR DELAY @espera;

        EXEC obs.stp_Exp_Log_Fim @IDLog, 'CONCLUIDA';
    END TRY
    BEGIN CATCH
        DECLARE @e NVARCHAR(2000) = ERROR_MESSAGE();
        EXEC obs.stp_Exp_Log_Fim @IDLog, 'ERRO', @e;
    END CATCH
END
GO

PRINT 'Procedures de injecao de CPU e bloqueio corrigidas.';
GO

-- ------------------------------------------------------------
-- PASSO 6 - teste de fumaca. Rode e confira o efeito.
-- CPU deve passar de 70%; SessoesBloqueadas deve chegar a 8.
-- ------------------------------------------------------------
EXEC obs.stp_Injetar_CPU      @Intensidade = 3, @DuracaoSeg = 130;
GO
SELECT TOP (6) DataColeta, SQLServerCPU, SystemIdleCPU
FROM obs.Historico_CPU_Counters ORDER BY DataColeta DESC;
GO

EXEC obs.stp_Injetar_Bloqueio @Intensidade = 3, @DuracaoSeg = 130;
GO
SELECT TOP (6) DataColeta, SessoesBloqueadas, ConexoesAtivas
FROM obs.Historico_AtividadeSQL ORDER BY DataColeta DESC;
GO

SELECT ID, ModoFalha, Intensidade, Paralelismo, DataInicio, DataFim, Status
FROM obs.Log_Injecoes ORDER BY ID DESC;
GO
