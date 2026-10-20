-- ============================================================
-- EXPERIMENTO DE AVALIACAO DO DETECTOR DE ANOMALIAS
-- 02 - Geracao do plano de injecoes (delineamento fatorial)
--
-- Delineamento: 4 modos de falha x 3 intensidades = 12 celulas,
-- cada uma repetida @RepeticoesPorCelula vezes.
--
-- As injecoes sao distribuidas em passo constante ao longo da
-- janela de coleta. Com 14 dias e 4 repeticoes o passo e de 7
-- horas, o que faz o horario girar por todas as faixas do dia e
-- cobrir dias uteis e fim de semana sem correlacionar modo de
-- falha com horario.
--
-- A ordem das celulas e sorteada uma unica vez e PERSISTIDA em
-- obs.Plano_Injecoes: o plano realizado fica auditavel, o que
-- satisfaz a exigencia de reprodutibilidade sem depender de
-- semente de aleatorizacao.
-- ============================================================

USE [DB_Observability];
GO

CREATE OR ALTER PROCEDURE obs.stp_Exp_Gerar_Plano
    @DataInicio          DATETIME2(0),          -- inicio da janela de injecao
    @Dias                INT = 14,
    @RepeticoesPorCelula INT = 4,
    @DuracaoSeg          INT = 180,
    @Limpar              BIT = 0                 -- 1 = descarta plano anterior
AS
BEGIN
    SET NOCOUNT ON;

    IF @Limpar = 1
        DELETE FROM obs.Plano_Injecoes WHERE Executada = 0;

    IF EXISTS (SELECT 1 FROM obs.Plano_Injecoes WHERE Executada = 0)
    BEGIN
        RAISERROR('Ja existe plano pendente. Use @Limpar = 1 para substituir.', 16, 1);
        RETURN;
    END

    DECLARE @N INT, @PassoMin INT;

    ;WITH celulas AS (
        SELECT ModoFalha, Intensidade
        FROM (VALUES ('CPU'), ('MEMORIA'), ('IO'), ('BLOQUEIO')) m(ModoFalha)
        CROSS JOIN (VALUES (1), (2), (3)) i(Intensidade)
    ),
    repeticoes AS (
        SELECT TOP (@RepeticoesPorCelula) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS r
        FROM sys.all_objects
    ),
    sorteio AS (
        SELECT c.ModoFalha,
               c.Intensidade,
               ROW_NUMBER() OVER (ORDER BY NEWID()) AS slot
        FROM celulas c CROSS JOIN repeticoes
    )
    SELECT @N = COUNT(*) FROM sorteio;

    SET @PassoMin = (@Dias * 1440) / NULLIF(@N, 0);

    IF @PassoMin < 10
    BEGIN
        RAISERROR('Passo de %d min e curto demais: reduza @RepeticoesPorCelula ou amplie @Dias.', 16, 1, @PassoMin);
        RETURN;
    END

    ;WITH celulas AS (
        SELECT ModoFalha, Intensidade
        FROM (VALUES ('CPU'), ('MEMORIA'), ('IO'), ('BLOQUEIO')) m(ModoFalha)
        CROSS JOIN (VALUES (1), (2), (3)) i(Intensidade)
    ),
    repeticoes AS (
        SELECT TOP (@RepeticoesPorCelula) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS r
        FROM sys.all_objects
    ),
    sorteio AS (
        SELECT c.ModoFalha,
               c.Intensidade,
               ROW_NUMBER() OVER (ORDER BY NEWID()) AS slot
        FROM celulas c CROSS JOIN repeticoes
    )
    INSERT INTO obs.Plano_Injecoes (DataPlanejada, ModoFalha, Intensidade, DuracaoSeg)
    SELECT DATEADD(MINUTE, (slot - 1) * @PassoMin, @DataInicio),
           ModoFalha,
           Intensidade,
           @DuracaoSeg
    FROM sorteio
    ORDER BY slot;

    RAISERROR('Plano gerado: %d injecoes, passo de %d minutos.', 0, 1, @N, @PassoMin) WITH NOWAIT;

    -- resumo para conferencia e para a tabela da Metodologia
    SELECT ModoFalha, Intensidade, Injecoes = COUNT(*),
           Primeira = MIN(DataPlanejada), Ultima = MAX(DataPlanejada)
    FROM obs.Plano_Injecoes
    GROUP BY ModoFalha, Intensidade
    ORDER BY ModoFalha, Intensidade;

    SELECT FaixaHoraria =
             CASE WHEN DATEPART(HOUR, DataPlanejada) BETWEEN  0 AND  5 THEN '00-06 madrugada'
                  WHEN DATEPART(HOUR, DataPlanejada) BETWEEN  6 AND 11 THEN '06-12 manha'
                  WHEN DATEPART(HOUR, DataPlanejada) BETWEEN 12 AND 17 THEN '12-18 tarde'
                  ELSE '18-24 noite' END,
           Injecoes = COUNT(*)
    FROM obs.Plano_Injecoes
    GROUP BY CASE WHEN DATEPART(HOUR, DataPlanejada) BETWEEN  0 AND  5 THEN '00-06 madrugada'
                  WHEN DATEPART(HOUR, DataPlanejada) BETWEEN  6 AND 11 THEN '06-12 manha'
                  WHEN DATEPART(HOUR, DataPlanejada) BETWEEN 12 AND 17 THEN '12-18 tarde'
                  ELSE '18-24 noite' END
    ORDER BY 1;
END
GO

PRINT 'Procedure obs.stp_Exp_Gerar_Plano criada.';
PRINT '';
PRINT 'PARA GERAR O PLANO, ajuste a data e execute:';
PRINT '  EXEC obs.stp_Exp_Gerar_Plano @DataInicio = ''2026-09-04 00:00:00'',';
PRINT '                               @Dias = 14, @RepeticoesPorCelula = 4,';
PRINT '                               @DuracaoSeg = 180, @Limpar = 1;';
GO
