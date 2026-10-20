-- ============================================================
-- EXPERIMENTO DE AVALIACAO DO DETECTOR DE ANOMALIAS
-- 03 - Jobs do SQL Agent do experimento
--
-- ATENCAO: os dois jobs sao criados DESABILITADOS (@enabled = 0).
-- Nada e executado ao rodar este script. Voce habilita quando
-- decidir iniciar a janela de injecoes:
--
--   EXEC msdb.dbo.sp_update_job
--        @job_name = N'DBAExp_Orquestrador', @enabled = 1;
--
-- E interrompe a qualquer momento com @enabled = 0.
--
-- O job DBAExp_Bloqueador NAO tem agenda: ele so e disparado
-- pela procedure obs.stp_Injetar_Bloqueio, mas precisa estar
-- habilitado para que sp_start_job funcione.
-- ============================================================

USE [msdb];
GO

DECLARE @cat NVARCHAR(128) = N'DBA_Experimento';
IF NOT EXISTS (SELECT name FROM msdb.dbo.syscategories WHERE name = @cat)
    EXEC msdb.dbo.sp_add_category @class = N'JOB', @type = N'LOCAL', @name = @cat;
GO

-- ------------------------------------------------------------
-- Procedure executora: le o plano e dispara UMA injecao pendente
-- ------------------------------------------------------------
USE [DB_Observability];
GO

CREATE OR ALTER PROCEDURE obs.stp_Exp_Executar_Pendentes
    @ToleranciaMin INT = 30     -- descarta injecao atrasada alem disso
AS
BEGIN
    SET NOCOUNT ON;

    -- nao inicia uma injecao se outra ainda esta em execucao
    IF EXISTS (SELECT 1 FROM obs.Log_Injecoes WHERE Status = 'EM_EXECUCAO'
                 AND DataInicio > DATEADD(MINUTE, -30, SYSDATETIME()))
    BEGIN
        RAISERROR('Injecao anterior ainda em execucao. Nada a fazer.', 0, 1) WITH NOWAIT;
        RETURN;
    END

    DECLARE @ID INT, @Modo VARCHAR(20), @Int TINYINT, @Dur INT;

    SELECT TOP (1) @ID = ID, @Modo = ModoFalha, @Int = Intensidade, @Dur = DuracaoSeg
    FROM obs.Plano_Injecoes
    WHERE Executada = 0
      AND DataPlanejada <= SYSDATETIME()
      AND DataPlanejada >= DATEADD(MINUTE, -@ToleranciaMin, SYSDATETIME())
    ORDER BY DataPlanejada ASC;

    IF @ID IS NULL
    BEGIN
        -- marca como perdidas as injecoes atrasadas alem da tolerancia
        UPDATE obs.Plano_Injecoes
           SET Executada = 1, IDLogInjecao = NULL
         WHERE Executada = 0
           AND DataPlanejada < DATEADD(MINUTE, -@ToleranciaMin, SYSDATETIME());
        RETURN;
    END

    DECLARE @IDLogAntes BIGINT = ISNULL((SELECT MAX(ID) FROM obs.Log_Injecoes), 0);

    IF    @Modo = 'CPU'      EXEC obs.stp_Injetar_CPU      @Intensidade = @Int, @DuracaoSeg = @Dur;
    ELSE IF @Modo = 'MEMORIA'  EXEC obs.stp_Injetar_Memoria  @Intensidade = @Int, @DuracaoSeg = @Dur;
    ELSE IF @Modo = 'IO'       EXEC obs.stp_Injetar_IO       @Intensidade = @Int, @DuracaoSeg = @Dur;
    ELSE IF @Modo = 'BLOQUEIO' EXEC obs.stp_Injetar_Bloqueio @Intensidade = @Int, @DuracaoSeg = @Dur;

    UPDATE obs.Plano_Injecoes
       SET Executada = 1,
           IDLogInjecao = (SELECT MAX(ID) FROM obs.Log_Injecoes WHERE ID > @IDLogAntes)
     WHERE ID = @ID;
END
GO

-- ------------------------------------------------------------
-- Procedure do bloqueador: segura o lock pela duracao da
-- injecao BLOQUEIO em execucao mais recente
-- ------------------------------------------------------------
CREATE OR ALTER PROCEDURE obs.stp_Exp_Segurar_Lock
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @dur INT, @espera CHAR(8);

    SELECT TOP (1) @dur = DuracaoPlanejadaSeg
    FROM obs.Log_Injecoes
    WHERE ModoFalha = 'BLOQUEIO' AND Status = 'EM_EXECUCAO'
    ORDER BY ID DESC;

    IF @dur IS NULL RETURN;             -- ninguem pediu bloqueio
    IF @dur > 600 SET @dur = 600;       -- teto de seguranca: 10 min

    SET @espera = CONVERT(CHAR(8), DATEADD(SECOND, @dur, CAST('00:00:00' AS TIME)), 108);

    BEGIN TRY
        BEGIN TRANSACTION;
            UPDATE obs.Scratch_Bloqueio SET Contador = Contador + 1 WHERE ID = 1;
            WAITFOR DELAY @espera;
        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    END CATCH
END
GO

USE [msdb];
GO

-- ------------------------------------------------------------
-- JOB A: DBAExp_Bloqueador (sem agenda, disparado on demand)
-- ------------------------------------------------------------
IF EXISTS (SELECT job_id FROM msdb.dbo.sysjobs WHERE name = N'DBAExp_Bloqueador')
    EXEC msdb.dbo.sp_delete_job @job_name = N'DBAExp_Bloqueador', @delete_unused_schedule = 1;
GO

EXEC msdb.dbo.sp_add_job
    @job_name = N'DBAExp_Bloqueador',
    @enabled = 1,                       -- precisa estar habilitado para sp_start_job
    @description = N'Segura uma transacao sobre obs.Scratch_Bloqueio para gerar bloqueio no experimento. Sem agenda: disparado por obs.stp_Injetar_Bloqueio.',
    @category_name = N'DBA_Experimento',
    @notify_level_eventlog = 2;

EXEC msdb.dbo.sp_add_jobstep
    @job_name = N'DBAExp_Bloqueador',
    @step_name = N'Segurar Lock',
    @step_id = 1,
    @subsystem = N'TSQL',
    @database_name = N'DB_Observability',
    @command = N'EXEC obs.stp_Exp_Segurar_Lock;';

EXEC msdb.dbo.sp_add_jobserver @job_name = N'DBAExp_Bloqueador';
GO

-- ------------------------------------------------------------
-- JOB B: DBAExp_Orquestrador — a cada 5 min, DESABILITADO
-- ------------------------------------------------------------
IF EXISTS (SELECT job_id FROM msdb.dbo.sysjobs WHERE name = N'DBAExp_Orquestrador')
    EXEC msdb.dbo.sp_delete_job @job_name = N'DBAExp_Orquestrador', @delete_unused_schedule = 1;
GO

EXEC msdb.dbo.sp_add_job
    @job_name = N'DBAExp_Orquestrador',
    @enabled = 0,                       -- <<< DESABILITADO DE PROPOSITO
    @description = N'Le obs.Plano_Injecoes e executa a injecao pendente do horario. Habilitar somente para iniciar a janela do experimento.',
    @category_name = N'DBA_Experimento',
    @notify_level_eventlog = 2;

EXEC msdb.dbo.sp_add_jobstep
    @job_name = N'DBAExp_Orquestrador',
    @step_name = N'Executar Injecao Pendente',
    @step_id = 1,
    @subsystem = N'TSQL',
    @database_name = N'DB_Observability',
    @command = N'EXEC obs.stp_Exp_Executar_Pendentes @ToleranciaMin = 30;';

EXEC msdb.dbo.sp_add_jobserver @job_name = N'DBAExp_Orquestrador';

EXEC msdb.dbo.sp_add_schedule
    @schedule_name = N'Sch_Exp_Orquestrador',
    @freq_type = 4, @freq_interval = 1,
    @freq_subday_type = 4, @freq_subday_interval = 5;

EXEC msdb.dbo.sp_attach_schedule
    @job_name = N'DBAExp_Orquestrador',
    @schedule_name = N'Sch_Exp_Orquestrador';
GO

PRINT 'Jobs do experimento criados.';
PRINT 'DBAExp_Orquestrador esta DESABILITADO. Para iniciar a janela de injecoes:';
PRINT '  EXEC msdb.dbo.sp_update_job @job_name = N''DBAExp_Orquestrador'', @enabled = 1;';
PRINT 'Para interromper:';
PRINT '  EXEC msdb.dbo.sp_update_job @job_name = N''DBAExp_Orquestrador'', @enabled = 0;';
GO
