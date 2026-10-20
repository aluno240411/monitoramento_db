-- ============================================================
-- DB_Observability — Criacao dos SQL Agent Jobs (coleta local)
-- Executar na mesma instancia onde 01_ e 02_ ja foram executados.
-- ============================================================

USE [msdb];
GO

DECLARE @categoria NVARCHAR(128) = N'DBA_Monitor';
IF NOT EXISTS (SELECT name FROM msdb.dbo.syscategories WHERE name = @categoria)
    EXEC msdb.dbo.sp_add_category @class = N'JOB', @type = N'LOCAL', @name = @categoria;
GO

-- ============================================================
-- JOB 1: Coleta de Performance — a cada 1 minuto
-- (CPU/Contadores, Atividade SQL, Memoria, Waits, I/O)
-- ============================================================
IF EXISTS (SELECT job_id FROM msdb.dbo.sysjobs WHERE name = N'DBAMon_Coleta_Performance')
    EXEC msdb.dbo.sp_delete_job @job_name = N'DBAMon_Coleta_Performance', @delete_unused_schedule = 1;
GO

EXEC msdb.dbo.sp_add_job
    @job_name = N'DBAMon_Coleta_Performance',
    @enabled = 1,
    @description = N'Coleta de CPU, atividade SQL, memoria, waits e I/O a cada 1 minuto.',
    @category_name = N'DBA_Monitor',
    @notify_level_eventlog = 2;

EXEC msdb.dbo.sp_add_jobstep
    @job_name = N'DBAMon_Coleta_Performance',
    @step_name = N'Executar Coletas de Performance',
    @step_id = 1,
    @subsystem = N'TSQL',
    @database_name = N'DB_Observability',
    @command = N'
        EXEC obs.stp_Coletar_CPU_Counters;
        EXEC obs.stp_Coletar_AtividadeSQL;
        EXEC obs.stp_Coletar_Memoria;
        EXEC obs.stp_Coletar_WaitStats;
        EXEC obs.stp_Coletar_IO_Stats;
    ';

EXEC msdb.dbo.sp_add_jobserver @job_name = N'DBAMon_Coleta_Performance';

EXEC msdb.dbo.sp_add_schedule
    @schedule_name = N'Sch_Coleta_Performance',
    @freq_type = 4,
    @freq_interval = 1,
    @freq_subday_type = 4,
    @freq_subday_interval = 1;

EXEC msdb.dbo.sp_attach_schedule
    @job_name = N'DBAMon_Coleta_Performance',
    @schedule_name = N'Sch_Coleta_Performance';
GO

-- ============================================================
-- JOB 2: Top Queries (classificacao ABC) — a cada 5 minutos
-- ============================================================
IF EXISTS (SELECT job_id FROM msdb.dbo.sysjobs WHERE name = N'DBAMon_Top_Queries')
    EXEC msdb.dbo.sp_delete_job @job_name = N'DBAMon_Top_Queries', @delete_unused_schedule = 1;
GO

EXEC msdb.dbo.sp_add_job
    @job_name = N'DBAMon_Top_Queries',
    @enabled = 1,
    @description = N'Coleta e classifica (ABC) as queries mais pesadas a cada 5 minutos.',
    @category_name = N'DBA_Monitor',
    @notify_level_eventlog = 2;

EXEC msdb.dbo.sp_add_jobstep
    @job_name = N'DBAMon_Top_Queries',
    @step_name = N'Executar Coleta de Top Queries',
    @step_id = 1,
    @subsystem = N'TSQL',
    @database_name = N'DB_Observability',
    @command = N'EXEC obs.stp_Coletar_TopQueries;';

EXEC msdb.dbo.sp_add_jobserver @job_name = N'DBAMon_Top_Queries';

EXEC msdb.dbo.sp_add_schedule
    @schedule_name = N'Sch_Top_Queries',
    @freq_type = 4,
    @freq_interval = 1,
    @freq_subday_type = 4,
    @freq_subday_interval = 5;

EXEC msdb.dbo.sp_attach_schedule
    @job_name = N'DBAMon_Top_Queries',
    @schedule_name = N'Sch_Top_Queries';
GO

-- ============================================================
-- JOB 3: Saude Operacional — a cada 15 minutos
-- (Discos, Falhas de Job)
-- ============================================================
IF EXISTS (SELECT job_id FROM msdb.dbo.sysjobs WHERE name = N'DBAMon_Saude_Operacional')
    EXEC msdb.dbo.sp_delete_job @job_name = N'DBAMon_Saude_Operacional', @delete_unused_schedule = 1;
GO

EXEC msdb.dbo.sp_add_job
    @job_name = N'DBAMon_Saude_Operacional',
    @enabled = 1,
    @description = N'Coleta de discos e falhas de job a cada 15 minutos.',
    @category_name = N'DBA_Monitor',
    @notify_level_eventlog = 2;

EXEC msdb.dbo.sp_add_jobstep
    @job_name = N'DBAMon_Saude_Operacional',
    @step_name = N'Executar Coletas Operacionais',
    @step_id = 1,
    @subsystem = N'TSQL',
    @database_name = N'DB_Observability',
    @command = N'
        EXEC obs.stp_Coletar_Discos;
        EXEC obs.stp_Coletar_FalhasJobs;
    ';

EXEC msdb.dbo.sp_add_jobserver @job_name = N'DBAMon_Saude_Operacional';

EXEC msdb.dbo.sp_add_schedule
    @schedule_name = N'Sch_Saude_Operacional',
    @freq_type = 4,
    @freq_interval = 1,
    @freq_subday_type = 4,
    @freq_subday_interval = 15;

EXEC msdb.dbo.sp_attach_schedule
    @job_name = N'DBAMon_Saude_Operacional',
    @schedule_name = N'Sch_Saude_Operacional';
GO

-- ============================================================
-- JOB 4: Limpeza e Retencao de Historico — diario as 02:00
-- ============================================================
IF EXISTS (SELECT job_id FROM msdb.dbo.sysjobs WHERE name = N'DBAMon_Limpeza_Historico')
    EXEC msdb.dbo.sp_delete_job @job_name = N'DBAMon_Limpeza_Historico', @delete_unused_schedule = 1;
GO

EXEC msdb.dbo.sp_add_job
    @job_name = N'DBAMon_Limpeza_Historico',
    @enabled = 1,
    @description = N'Remove registros com mais de 30 dias das tabelas de historico.',
    @category_name = N'DBA_Monitor',
    @notify_level_eventlog = 2;

EXEC msdb.dbo.sp_add_jobstep
    @job_name = N'DBAMon_Limpeza_Historico',
    @step_name = N'Limpar Historico',
    @step_id = 1,
    @subsystem = N'TSQL',
    @database_name = N'DB_Observability',
    @command = N'EXEC obs.stp_Limpeza_Historico @DiasRetencao = 30;';

EXEC msdb.dbo.sp_add_jobserver @job_name = N'DBAMon_Limpeza_Historico';

EXEC msdb.dbo.sp_add_schedule
    @schedule_name = N'Sch_Limpeza_Historico',
    @freq_type = 4,
    @freq_interval = 1,
    @freq_subday_type = 1,
    @active_start_time = 020000;

EXEC msdb.dbo.sp_attach_schedule
    @job_name = N'DBAMon_Limpeza_Historico',
    @schedule_name = N'Sch_Limpeza_Historico';
GO

PRINT 'Jobs do SQL Agent criados com sucesso.';
GO
