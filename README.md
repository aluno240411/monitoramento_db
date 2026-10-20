# DBA Monitor — Painel de Observabilidade para SQL Server

Painel web para monitorar instâncias Microsoft SQL Server, com alertas por limiares
fixos e derivados dos percentils da linha de base 
e detecção de anomalias por aprendizado de máquina (Isolation Forest). Desenvolvido
como parte do Trabalho de Conclusão de Curso da pós-graduação em Ciência de Dados
(FACENS).

O projeto tem duas partes:

1. **Coleta** — scripts T-SQL e jobs do SQL Server Agent gravam métricas de
   desempenho e saúde em um banco de histórico dentro de cada instância monitorada.
2. **Painel** — aplicação Streamlit que extrai esse histórico para arquivos CSV
   locais e o exibe em gráficos, alertas e análise de anomalias.

Além disso, o repositório inclui um **kit de experimento** (`experimento/`) que
injeta falhas controladas no servidor para medir a qualidade da detecção.

---

## Sumário

- [Arquitetura](#arquitetura)
- [Requisitos](#requisitos)
- [Instalação passo a passo](#instalação-passo-a-passo)
- [Como usar o painel](#como-usar-o-painel)
- [Detecção de anomalias](#detecção-de-anomalias)
- [Experimento de injeção de falhas](#experimento-de-injeção-de-falhas)
- [Estrutura do repositório](#estrutura-do-repositório)
- [Dados incluídos no repositório](#dados-incluídos-no-repositório)
- [Segurança](#segurança)
- [Solução de problemas](#solução-de-problemas)

---

## Arquitetura

```
Instância SQL Server monitorada
  DMVs (sys.dm_os_*, sys.dm_exec_*, sys.dm_io_*)
        |
        |  SQL Server Agent Jobs executam as procedures obs.stp_Coletar_*
        v
  Banco de histórico (schema obs, retenção de 30 dias)
        |
        |  Painel > Sincronizar (extração incremental por ID)
        v
  data/<alias>/*.csv
        |
        |  Streamlit + Plotly + scikit-learn
        v
  Painel web: gráficos, alertas por limiar e anomalias
```

Pontos de projeto:

- **Coleta local por instância.** Cada instância monitorada tem o seu próprio banco
  de histórico; não há linked server. O painel suporta vários servidores
  cadastrados ao mesmo tempo.
- **CSV como camada intermediária.** O painel lê os CSVs de `data/<alias>/`. Ele só
  se conecta ao SQL Server no momento da sincronização, e continua funcionando sem
  conexão (por exemplo, hospedado em nuvem) sobre os dados já extraídos.
- **Extração incremental.** A sincronização traz apenas as linhas novas, usando o
  `ID` IDENTITY de cada tabela como marca d'água (arquivo
  `_ultima_sincronizacao.json`), e as acrescenta ao CSV existente.
- **Contadores cumulativos no padrão `_total` / `_periodo`.** Contadores que o SQL
  Server acumula desde a inicialização (batch requests, compilações, deadlocks,
  page splits, lazy writes, estatísticas de E/S) são gravados como `_total` (valor
  acumulado) e `_periodo` (consumo desde a coleta anterior). O painel e o detector
  usam sempre o `_periodo`.

---

## Requisitos

**Painel (máquina do usuário)**

- Python 3.11 ou superior
- Dependências em `requirements.txt`: Streamlit, pandas, Plotly, scikit-learn,
  pyodbc, SQLAlchemy, PyYAML e streamlit-authenticator
- Para sincronizar com um SQL Server: **ODBC Driver 17 (ou 18) for SQL Server**
  instalado na máquina que roda o painel

**Instância monitorada**

- Microsoft SQL Server com SQL Server Agent em execução (os jobs de coleta dependem
  dele; não funciona em edições sem Agent, como a Express)
- Um login com permissão para criar banco, schema, procedures e jobs (somente na
  instalação) e, depois, com permissão de leitura no banco de histórico (uso diário
  do painel)

---

## Instalação passo a passo

### 1. Obter o código e instalar as dependências

```bash
git clone https://github.com/aluno240411/monitoramento_db/TCCPosCienciaDados.git
cd monitoramento_db

python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

### 2. Provisionar a coleta em cada instância SQL Server

Os scripts ficam em `sql/` e devem ser executados **manualmente, no SSMS (ou
ferramenta equivalente), na instância que será monitorada, nesta ordem**:

| Ordem | Script | O que faz |
|---|---|---|
| 1 | `sql/01_criar_banco_schema_tabelas.sql` | Cria o banco `DB_Observability`, o schema `obs` e as tabelas de histórico |
| 2 | `sql/02_stored_procedures_coleta.sql` | Cria as procedures de coleta `obs.stp_Coletar_*` e a de limpeza `obs.stp_Limpeza_Historico` |
| 3 | `sql/03_sql_agent_jobs.sql` | Cria os jobs do SQL Server Agent que chamam as procedures |

O painel nunca executa esses scripts por você. A tela **Onboarding DBA** do painel
permite escolher o nome do banco e baixar os três scripts já com o nome
substituído.

Jobs criados (categoria `DBA_Monitor`):

| Job | Intervalo | Coleta |
|---|---|---|
| `DBAMon_Coleta_Performance` | 1 minuto | CPU e contadores, atividade SQL, memória, waits, E/S |
| `DBAMon_Top_Queries` | 5 minutos | Queries mais pesadas, com classificação ABC |
| `DBAMon_Saude_Operacional` | 15 minutos | Espaço em disco e falhas de jobs |
| `DBAMon_Limpeza_Historico` | diário, 02:00 | Remove histórico com mais de 30 dias |

Scripts complementares:

| Script | Quando aplicar | O que faz |
|---|---|---|
| `sql/04_correcao_coleta_cpu_counters.sql` | Somente em instâncias **já provisionadas** com versão anterior (em instalações novas a correção já está em 01 e 02) | Corrige buffer cache hit ratio acima de 100% (numerador e denominador eram lidos em pontos distintos da DMV) |
| `sql/05_correcao_coleta_io.sql` | Somente em instâncias **já provisionadas** com versão anterior (idem) | Estatísticas de E/S passam a ser gravadas pelo valor do intervalo, e não pelo acumulado desde o boot |
| `sql/06_coleta_io_por_volume.sql` | **Em toda instância**, depois de 01 a 03 (e depois de 05, se aplicável) | Associa cada arquivo de E/S ao volume (disco) em que está, para cruzar a latência com o espaço livre |

> **Atenção ao nome do banco.** Nos scripts de correção e nos de experimento há um
> `USE` no início. Ajuste-o para o nome do banco da instância.


### 3. Executar o painel

```bash
streamlit run app/main.py
```

O painel abre em `http://localhost:8501`.

### 4. Primeiro acesso e usuários

Os usuários ficam em `config/users.yaml` (senhas com hash bcrypt).

- Se o repositório trouxer um `config/users.yaml` com os usuários do ambiente do autor,
  cujas senhas não são públicas. **Para ter um acesso novo, apague
  `config/users.yaml` e inicie o painel:** ele recria o arquivo com o usuário
  `dba_admin` e senha `admin123`.
- Entre com esse usuário, abra **Gerenciar Usuários**, crie os usuários reais e
  **remova ou troque a senha do `dba_admin`**.

### 5. Cadastrar o servidor e sincronizar

1. Abra **Gerenciar Servidores** e cadastre a instância: alias (nome amigável),
   host, porta, nome do banco de histórico, driver ODBC e tipo de autenticação
   (Windows ou SQL). Use **Testar Conexão** para validar.
2. Em **Painel de Observabilidade**, clique em **Sincronizar**. A primeira
   sincronização traz o histórico completo; as seguintes trazem só o que é novo.

---

## Como usar o painel

O menu lateral tem as telas abaixo.

| Tela | Para que serve |
|---|---|
| **Painel de Observabilidade** | Tela principal: seleção do servidor, sincronização, janela de tempo e as abas de métricas |
| **Gerenciar Servidores** | Cadastro, teste de conexão e remoção de instâncias; escolha dos grupos de métricas do detector de anomalias; extração manual dos CSVs |
| **Gerenciar Usuários** | Criação e exclusão de usuários de acesso ao painel |
| **Onboarding DBA** | Download dos scripts SQL de provisionamento, com nome de banco configurável |
| **Onboarding do Experimento** | Arquivos do kit de experimento, na ordem de execução, e o comando de avaliação |
| **Resultado do Experimento** | Telemetria com as injeções sobrepostas e calibração de limiares |
| **Detecção de Anomalias (ML)** | Isolation Forest multivariado sobre os grupos de métricas escolhidos |

### Abas do painel principal

- **Visão Geral** — grade de cartões com todos os domínios da instância, para leitura
  rápida
- **CPU & Performance** — CPU do SQL Server, Page Life Expectancy, buffer cache hit
  ratio, lazy writes
- **Atividade SQL** — batch requests, conexões, sessões bloqueadas, compilações,
  recompilações, deadlocks
- **Memória & Discos** — distribuição por memory clerk e capacidade dos volumes
- **Wait Statistics** — principais esperas e latência de E/S por arquivo
- **Top Queries (ABC)** — queries mais pesadas por CPU, E/S e duração
- **Falhas de Jobs** — falhas recentes de jobs do SQL Server Agent

### Janela de tempo

O seletor no topo do painel funciona como o de consoles de monitoramento:
`◄ [Tempo Real: data ▾] ►`.

- O menu oferece os últimos 15 e 30 minutos, 1, 2, 4, 6 e 8 horas, 1 dia e 5 dias,
  além de **intervalo personalizado** e **Tempo Real**.
- Os botões `◄` e `►` deslocam a janela para o fragmento de tempo anterior ou
  seguinte, com a mesma duração, facilitando percorrer o histórico.
- Uma legenda abaixo do seletor mostra o tamanho do fragmento selecionado e seus
  instantes inicial e final.

### Alertas por limiar

Os limites ficam no dicionário `LIMITES` de `app/views/dashboard.py`, com a
orientação ao DBA em `ORIENTACOES`. Valores atuais:

| Métrica | Aviso | Crítico | Sentido |
|---|---|---|---|
| Page Life Expectancy (s) | < 1000 | < 300 | abaixo |
| Buffer cache hit (%) | < 97 | < 95 | abaixo |
| CPU SQL Server (%) | > 70 | > 90 | acima |
| CPU total (%) | > 75 | > 90 | acima |
| Latência de leitura (ms) | > 15 | > 25 | acima |
| Latência de escrita (ms) | > 15 | > 25 | acima |
| Sessões bloqueadas | > 1 | > 5 | acima |
| Espaço livre (%) | < 20 | < 10 | abaixo |

---

## Detecção de anomalias

O painel combina duas camadas:

1. **Camada 1 — limiares fixos.** Regras do dicionário `LIMITES`, avaliadas métrica
   a métrica.
2. **Camada 2 — Isolation Forest multivariado** (`app/ml_engine.py`). Combina os
   grupos de métricas numa grade de 1 minuto, padroniza com `StandardScaler` e
   sinaliza **combinações** atípicas (por exemplo, PLE caindo, latência subindo e
   sessões bloqueadas ao mesmo tempo), mesmo quando cada métrica isolada ainda está
   dentro do limite. Para cada ponto anômalo, a tabela indica a `metrica_destaque`,
   isto é, a que mais se afastou da média (maior |z-score|).

Grupos de métricas disponíveis, configuráveis **por servidor** em **Gerenciar
Servidores** (campo `metricas_ml` do `config/config.yaml`):

| Grupo | Métricas |
|---|---|
| `cpu` | CPU do SQL Server, PLE, buffer cache hit, lazy writes do período |
| `memoria` | Memória total dos memory clerks |
| `io` | MB lidos e escritos, latências de leitura e escrita |
| `atividade_sql` | Batch requests, compilações, recompilações, deadlocks, page splits, conexões, sessões bloqueadas |

A sensibilidade (`contamination`, a proporção esperada de anomalias) é ajustável no
painel. O modelo precisa de pelo menos 20 amostras de 1 minuto.

---

## Experimento de injeção de falhas

A pasta `experimento/` contém o kit usado para avaliar quantitativamente o detector:
um delineamento fatorial 4 × 3 (modo de falha: CPU, memória, E/S, bloqueio; três
intensidades), com injeções controladas, registradas em `obs.Log_Injecoes` como
verdade de referência, e um script que mede precisão, revocação, F1 e latência de
detecção de cada camada.

```bash
python experimento/avaliar_deteccao.py --alias <alias-do-servidor>
```

O script não se conecta a nenhum banco: lê apenas os CSVs de `data/<alias>/` e o
log de injeções. Os resultados são gravados em `experimento/resultados/<alias>/`.

O passo a passo completo (ordem dos scripts, teste de fumaça, geração do plano,
acompanhamento da janela e correções aplicadas após a primeira janela) está em
[`experimento/README.md`](experimento/README.md).

> Os scripts `.sql` do experimento só rodam quando você os executa no SSMS. O job
> orquestrador é criado **desabilitado**, e você o habilita para iniciar as
> injeções.

---

## Estrutura do repositório

```
.
├── app/
│   ├── main.py                  Ponto de entrada (autenticação e menu)
│   ├── auth.py                  Login com streamlit-authenticator
│   ├── db_connector.py          Conexão, extração incremental e leitura dos CSVs
│   ├── ml_engine.py             Isolation Forest multivariado
│   └── views/                   Uma tela por arquivo (dashboard, visao_geral,
│                                anomalies, server_mgmt, user_mgmt, dba_scripts,
│                                experimento, experimento_scripts, calibracao)
├── sql/                         Scripts de provisionamento e de correção
├── experimento/                 Kit de injeção de falhas e avaliação
│   └── resultados/<alias>/      Tabelas geradas pela avaliação
├── config/
│   ├── config.yaml              Servidores cadastrados
│   └── users.yaml               Usuários do painel (hashes bcrypt)
├── data/<alias>/                CSVs extraídos de cada servidor
├── Caderno de calculos - ARTIGO.ipynb   Cálculos do artigo
└── requirements.txt
```

---

## Dados incluídos no repositório

A pasta `data/` contém séries  coletadas em ambientes de laboratório , 
para que o painel e a avaliação possam ser reproduzidos
sem acesso a nenhum SQL Server.

- Os arquivos grandes (`io_stats`, `top_queries`, `wait_stats` )
  estão compactados como `.csv.gz`. O painel lê o `.csv.gz` automaticamente quando o
  `.csv` não existe.
- Arquivos acima de 100 MB não são versionados (veja o `.gitignore`).
- Para analisar os seus próprios dados, cadastre o seu servidor e sincronize; os
  CSVs serão gravados em `data/<seu-alias>/`.

---

## Segurança

- **Credenciais SQL.** Para instâncias com autenticação SQL, o `config/config.yaml`
  guarda usuário e senha em texto puro. Prefira **autenticação Windows**
  (`Trusted_Connection`) e **não versione** um `config.yaml` com senhas.
- **Usuários do painel.** `config/users.yaml` contém hashes bcrypt e a chave do
  cookie de sessão. Em uma instalação própria, gere um arquivo novo (veja o
  [primeiro acesso](#4-primeiro-acesso-e-usuários)) e troque a chave do cookie.
- **Execução dos scripts SQL.** O painel não executa scripts no banco. Provisionamento,
  correções e experimento são sempre executados por uma pessoa, no SSMS.
- **Hospedagem em nuvem.** Em ambientes sem acesso à rede interna ou ao driver ODBC,
  a sincronização retorna o aviso "SQL Server indisponível neste ambiente" e o
  painel segue exibindo os CSVs já presentes.

---

## Solução de problemas

| Sintoma | Causa provável e solução |
|---|---|
| `Invalid object name 'obs.Historico_...'` ao sincronizar | O banco ainda não foi provisionado, ou o nome do banco cadastrado em **Gerenciar Servidores** difere do criado. Confira se os scripts 01, 02 e 03 foram executados e se o campo "Database" está correto |
| `Data source name not found` ou driver ausente | Instale o ODBC Driver 17 (ou 18) for SQL Server e selecione o mesmo driver no cadastro do servidor |
| O painel mostra dados antigos após ajustes no código | O Streamlit mantém módulos em memória: feche o processo e execute `streamlit run app/main.py` novamente |
| "Nenhum dado local encontrado" | Falta sincronizar: clique em **Sincronizar** ou extraia os CSVs em **Gerenciar Servidores** |
| Detecção de anomalias pede mais amostras | O modelo exige ao menos 20 amostras de 1 minuto nos grupos escolhidos; aguarde mais coletas |
| Não consigo entrar no painel | Apague `config/users.yaml` para recriar o usuário padrão (`dba_admin` / `admin123`) e troque a senha em seguida |

---

## Contexto acadêmico

Projeto desenvolvido para o TCC da pós-graduação em Ciência de Dados (FACENS).
O caderno de cálculos que acompanham o trabalho está na raiz do
repositório.
