# Experimento de avaliação do detector de anomalias

Kit para produzir a seção de **Resultados** do artigo. Gera verdade de referência
por injeção controlada de falhas e mede o desempenho das duas camadas de detecção
do painel, isoladas e combinadas.

> **Nada aqui executa sozinho.** Os scripts `.sql` só rodam quando você os abre no
> SSMS e executa. O job orquestrador é criado **desabilitado**; você o habilita
> quando decidir iniciar a janela de injeções, e desabilita para interromper.
> O script Python não tem nenhuma dependência de driver de banco nem qualquer
> caminho de conexão: lê exclusivamente os CSVs já extraídos.

---

## Delineamento

**Fatorial 4 × 3, com 4 repetições por célula — 48 injeções em 14 dias.**

| Fator | Níveis |
|---|---|
| Modo de falha | CPU, MEMÓRIA, I/O, BLOQUEIO |
| Intensidade | 1 (leve), 2 (média), 3 (pesada) |

Cada modo de falha ataca deliberadamente um dos quatro grupos de métricas que o
`app/ml_engine.py` combina no Isolation Forest:

| Modo | Mecanismo | Métricas afetadas |
|---|---|---|
| CPU | Laço de cálculo em ponto flutuante | `SQLServerCPU` |
| MEMÓRIA | Varreduras completas repetidas de tabela maior que o buffer pool, com ordenação forçada | `PageLifeExpectancy`, `BufferCacheHitRatio`, `LazyWritesPeriodo`, `MemoriaTotalMB` |
| I/O | Ciclo de INSERT + CHECKPOINT + varredura + DELETE | `IO_MBRead`, `IO_MBWritten`, `IO_ReadLatencyMs`, `IO_WriteLatencyMs` |
| BLOQUEIO | Job assíncrono segura transação sobre uma linha; a procedure vira vítima | `SessoesBloqueadas`, `ConexoesAtivas` |

As injeções são distribuídas em **passo constante de 7 horas**, o que faz o horário
girar por todas as faixas do dia e cobrir dias úteis e fim de semana sem
correlacionar modo de falha com horário. A ordem das células é sorteada uma única
vez e **persistida** em `obs.Plano_Injecoes` — o plano realizado fica auditável,
o que satisfaz a exigência de reprodutibilidade sem depender de semente.

Duração padrão de cada injeção: **180 s**. Mínimo útil é 120 s, porque a coleta
roda a cada 1 minuto e o efeito precisa existir no instante de pelo menos uma
coleta.

---

## Ordem de execução

### Uma vez, antes de começar

```
00_experimento_infra.sql     -- tabelas de log, plano e área de carga
01_procs_injecao.sql         -- as 4 procedures de injeção
02_gerar_plano.sql           -- cria obs.stp_Exp_Gerar_Plano
03_jobs_experimento.sql      -- cria os 2 jobs (orquestrador DESABILITADO)
```

Depois, dimensione a área de carga. **Ela precisa ser maior que a memória máxima
do SQL Server do lab** — se a tabela couber inteira no buffer pool, a injeção de
memória não produz pressão nenhuma e o experimento falha em silêncio:

```sql
SELECT value_in_use AS MaxServerMemoryMB
FROM sys.configurations WHERE name = 'max server memory (MB)';

-- @LinhasMil * 2 KB deve superar esse valor com folga
EXEC obs.stp_Exp_Popular_Scratch @LinhasMil = 300;   -- ~600 MB
```

Gere o plano com a data de início desejada:

```sql
EXEC obs.stp_Exp_Gerar_Plano
     @DataInicio = '2026-09-04 00:00:00',
     @Dias = 14, @RepeticoesPorCelula = 4,
     @DuracaoSeg = 180, @Limpar = 1;
```

A procedure devolve dois resumos — injeções por célula e distribuição por faixa
horária. **Guarde esses dois grids: eles são as tabelas do delineamento na
Metodologia.**

### Teste de fumaça (recomendado, antes de habilitar o job)

Rode cada modo manualmente uma vez, fora do plano, e confira que a linha fecha
com `Status = 'CONCLUIDA'`:

```sql
EXEC obs.stp_Injetar_CPU      @Intensidade = 1, @DuracaoSeg = 130;
EXEC obs.stp_Injetar_Memoria  @Intensidade = 1, @DuracaoSeg = 130;
EXEC obs.stp_Injetar_IO       @Intensidade = 1, @DuracaoSeg = 130;
EXEC obs.stp_Injetar_Bloqueio @Intensidade = 1, @DuracaoSeg = 130;

SELECT * FROM obs.Log_Injecoes ORDER BY ID DESC;
```

Confira também que o efeito apareceu na coleta:

```sql
SELECT TOP 20 DataColeta, SQLServerCPU, PageLifeExpectancy, BufferCacheHitRatio
FROM obs.Historico_CPU_Counters ORDER BY DataColeta DESC;
```

Se `Status = 'ERRO'`, leia `MensagemErro`. As causas prováveis: a conta do SQL
Agent sem `SQLAgentUserRole` em `msdb` (impede `sp_start_job`, usado só pelo modo
BLOQUEIO), ou `obs.Scratch_Carga` vazia.

Antes de habilitar o orquestrador, apague as linhas do teste de fumaça — ou
deixe-as e declare no artigo que foram excluídas da análise:

```sql
DELETE FROM obs.Log_Injecoes WHERE DataInicio < '2026-09-04';
```

### Iniciar e interromper a janela

```sql
EXEC msdb.dbo.sp_update_job @job_name = N'DBAExp_Orquestrador', @enabled = 1;  -- iniciar
EXEC msdb.dbo.sp_update_job @job_name = N'DBAExp_Orquestrador', @enabled = 0;  -- parar
```

### Acompanhamento durante a janela

Rode `04_exportar_log_injecoes.sql` a cada dois ou três dias. Ele mostra o log, o
andamento por célula, quantas injeções faltam e — importante — a **cobertura da
série**: o percentual de minutos efetivamente coletados no intervalo. Se cair
abaixo de ~98%, a coleta teve lacunas e você precisa saber disso antes do dia 14,
não depois.

### Ao final da janela

1. No painel, tela **Gerenciar Servidores**, extraia os CSVs do período completo.
2. Exporte `obs.Log_Injecoes` como `data/<alias>/injections.csv`
   (`04_exportar_log_injecoes.sql` explica as duas formas).
3. Rode a avaliação:

```
python experimento/avaliar_deteccao.py --alias LAB-SQL
```

---

## O que a avaliação produz

Quatro braços de detecção sobre a mesma série e a mesma verdade de referência:

- **Camada 1, limiar de aviso** — regras fixas, nível permissivo
- **Camada 1, limiar crítico** — regras fixas, nível conservador
- **Camada 2** — Isolation Forest multivariado
- **União** — alarme se qualquer uma disparar

Os limiares são **importados de `app/views/dashboard.py`**, e o detector é o
**próprio `app/ml_engine.py`**. A avaliação mede o sistema em produção, não uma
reimplementação — o que é uma afirmação forte de validade interna e vale ser dita
na Metodologia.

Saídas em `experimento/resultados/<alias>/`:

| Arquivo | Conteúdo |
|---|---|
| `tabela1_bracos.csv` | VP, FP, FN, VN, precisão, revocação, F1, taxa de falsos positivos por braço |
| `tabela2_por_modo.csv` | Revocação por modo de falha × braço — a matriz "que camada pega o quê" |
| `tabela3_latencia_resumo.csv` | Injeções detectadas e tempo até detecção (média, mediana, máximo) |
| `tabela3_latencia_detalhe.csv` | Uma linha por injeção, com latência individual |
| `tabela4_varredura_contamination.csv` | Métricas para contamination em 1%, 2%, 5%, 10% e 15% |
| `serie_anotada.csv` | Série completa com rótulo, modo, `anomaly_score` e os alarmes de cada camada — base das figuras |

### Parâmetros

| Flag | Padrão | Para quê |
|---|---|---|
| `--alias` | (obrigatório) | Alias do servidor, como está em `config.yaml` |
| `--contaminacao` | 0.05 | `contamination` do braço principal da Camada 2 |
| `--nivel-limiar` | aviso | Qual nível da Camada 1 compõe o braço de união |
| `--folga-min` | 0 | Folga ao rotular as janelas de injeção — **mantenha 0** |
| `--sem-varredura` | — | Pula a varredura de contamination (mais rápido) |

Sobre `--folga-min`: rotular minutos sem injeção ativa infla os falsos negativos.
Em teste com série sintética de 14 dias e 48 injeções de 3 min, `folga-min 1`
rotulou 287 minutos onde apenas 192 tinham carga — 50% de rótulos espúrios, e a
revocação da união caiu de 0,54 para 0,37 sem que nada no detector mudasse. A
tolerância de borda é tratada na Tabela 3 (tempo até detecção), não no rótulo.

---



## Correções após a primeira janela (17/09/2026)

A primeira janela rodou de 10 a 16/09 com 48 injeções. A avaliação revelou três
defeitos que invalidavam parte dos resultados. Os três estão corrigidos; a
segunda janela roda sobre as versões corrigidas.

### 1. Coleta de E/S media valores acumulados (`sql/05_correcao_coleta_io.sql`)

`sys.dm_io_virtual_file_stats` é cumulativa desde a inicialização da instância.
A coleta gravava seus valores crus, então `ReadLatencyMs` era a latência **média
desde que o servidor subiu**, não a do minuto — um arquivo do LAB-SQL apresentou
apenas 3 valores distintos em 14 dias, permanentemente acima de 25 ms. Como o
modelo toma o máximo entre arquivos, a regra de limiar disparava em **100% dos
minutos**, e a taxa de falsos positivos da Camada 1 dava 1,0.

`MBRead`/`MBWritten` eram totais acumulados, crescendo monotonicamente. Atributo
monotônico codifica o tempo, não a carga: o detector passa a distinguir início de
fim da série.

A correção aplica a esta coleta o padrão `_total`/`_periodo` que as demais já
usavam, com tabela de snapshot. O histórico já coletado é parcialmente
aproveitável: o volume por intervalo é recuperado por diferença na análise
(`app/ml_engine.py`), mas **a latência por intervalo é irrecuperável**, porque só
a razão foi persistida — por isso ela sai do conjunto de atributos para os dados
anteriores à correção.

### 2. Injeção de CPU e de bloqueio não atingiam os limiares (`06_correcao_injecao.sql`)

Medido na primeira janela:

| Modo | Efeito observado | Limiar de aviso | Diagnóstico |
|---|---|---|---|
| CPU | 6%, **idêntico nas intensidades 1, 2 e 3** | 70% | Laço monothread: um núcleo apenas |
| BLOQUEIO | 1 sessão, **idêntico nas três intensidades** | > 1 | Um bloqueador e uma vítima |

Em ambos os casos o parâmetro de intensidade não variava o paralelismo, de modo
que o fator do delineamento fatorial ficava nulo, e o limiar era inalcançável por
construção. Os dois passam a escalar o paralelismo: CPU usa 25%, 50% ou 100% dos
núcleos (lidos de `sys.dm_os_sys_info`), e o bloqueio usa 2, 4 ou 8 vítimas
simultâneas. O paralelismo realizado é gravado na coluna `Paralelismo` do log.

**Consequência para a análise da primeira janela:** os números de revocação da
Camada 1 para CPU e BLOQUEIO não medem a qualidade dos limiares — medem que a
carga injetada foi insuficiente. Só memória e E/S rendem comparação legítima
naquela janela.

### 3. Leitura do log de injeções (`avaliar_deteccao.py`)

O SSMS em português salva CSV com ponto e vírgula e com BOM. O leitor passou a
detectar o separador e a validar as colunas obrigatórias, em vez de falhar com
`KeyError: True`.

---

## Segunda janela — sequência de execução

**Passo 1 — corrigir a coleta.** Executar `sql/05_correcao_coleta_io.sql` nas três
instâncias, ajustando o `USE` (Prod usa `DBA_Monitor`). A primeira execução grava
NULL nas colunas de intervalo; da segunda em diante os valores aparecem.

**Passo 2 — corrigir o simulador.** Executar `experimento/06_correcao_injecao.sql`
no LAB-SQL. Ele cria um job trabalhador por núcleo e oito jobs vítima, e traz um
teste de fumaça no fim: confira que a CPU passa de 70% e que `SessoesBloqueadas`
chega a 8. **Se não passar, não adianta abrir a janela.**

**Passo 3 — limpar o teste de fumaça e gerar o plano.**

```sql
DELETE FROM obs.Log_Injecoes WHERE DataInicio >= '2026-09-17';

EXEC obs.stp_Exp_Gerar_Plano
     @DataInicio = '2026-09-19 12:00:00',
     @Dias = 5, @RepeticoesPorCelula = 4,
     @DuracaoSeg = 180, @Limpar = 1;
```

Cinco dias com 48 injeções dá passo de 150 minutos. Inclui um fim de semana.

**Passo 4 — habilitar o orquestrador** e conferir a cobertura a cada dois dias
com `04_exportar_log_injecoes.sql`.

**Passo 5 — ao encerrar**, extrair os CSVs pelo painel, exportar o log e rodar:

```
python experimento/avaliar_deteccao.py --alias LAB-SQL
```

Ao exportar o log, o `Status` e o `Paralelismo` precisam vir no CSV.


## Limitações conhecidas

1. `cpu_total_pct` e `espaco_livre_pct` existem em `LIMITES` mas não na grade
   montada por `montar_dataset()`. A Camada 1 é avaliada com 6 dos 8 indicadores.
2. As injeções são sintéticas e isoladas. Falhas reais tendem a ser combinadas e
   graduais; o experimento mede detecção de degradação abrupta e de causa única.
3. O ambiente é de laboratório, sem a carga concorrente de um ERP em produção.
   A taxa de falsos positivos medida aqui é um limite inferior otimista.
4. Uma única instância monitorada. Não há como estimar variância entre servidores.
5. O Isolation Forest é treinado sobre a série inteira, incluindo os períodos
   anômalos (não supervisionado, sem separação treino/teste). É o comportamento
   real do painel, mas otimiza a favor do detector e deve ser dito.
