"""Onboarding do experimento de injeção controlada de falhas.

Reúne, na ordem de execução, os arquivos necessários para montar e rodar o
experimento, com descrição, download e visualização. Segue o mesmo padrão da
tela de Onboarding do DBA: este painel nunca executa os scripts por você.
"""
import os

import streamlit as st

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
EXP_DIR = os.path.join(RAIZ, "experimento")
SQL_DIR = os.path.join(RAIZ, "sql")

# (pasta, arquivo, etapa, descrição)
ARQUIVOS = [
    (EXP_DIR, "00_experimento_infra.sql", "1. Preparar",
     "Cria as tabelas de log das injeções, o plano de execução e a área de carga usada pelos modos de memória e de entrada e saída."),
    (EXP_DIR, "01_procs_injecao.sql", "1. Preparar",
     "Cria as quatro procedures de injeção: processador, memória, entrada e saída e bloqueio."),
    (EXP_DIR, "02_gerar_plano.sql", "1. Preparar",
     "Cria a procedure que sorteia e persiste o plano fatorial — quatro modos por três intensidades, com repetições."),
    (EXP_DIR, "03_jobs_experimento.sql", "1. Preparar",
     "Cria os dois jobs do SQL Agent. O orquestrador nasce DESABILITADO: nada roda sem você habilitar."),
    (EXP_DIR, "06_correcao_injecao.sql", "2. Corrigir",
     "Dimensiona a carga por paralelismo. Sem isso, a saturação de processador e a contenção de bloqueio não atingem os limiares em instâncias maiores."),
    (SQL_DIR, "05_correcao_coleta_io.sql", "2. Corrigir",
     "Corrige a coleta de entrada e saída, que gravava valores acumulados em vez do consumo por intervalo."),
    (EXP_DIR, "04_exportar_log_injecoes.sql", "3. Acompanhar e exportar",
     "Mostra o andamento por célula, quantas injeções faltam e a cobertura da série. Ao final, exporta obs.Log_Injecoes para injections.csv."),
    (EXP_DIR, "avaliar_deteccao.py", "4. Avaliar",
     "Compara a verdade de referência do log com os alarmes do painel e gera as tabelas de precisão, revocação, F1 e taxa de falsos positivos."),
    (EXP_DIR, "README.md", "Referência",
     "Delineamento completo, ordem de execução, teste de fumaça e solução dos erros mais comuns."),
]

LINGUAGEM = {".sql": "sql", ".py": "python", ".md": "markdown"}

PASSOS = """
### Como rodar o experimento, do início ao fim

1. **Provisione a coleta.** Antes de tudo, a instância precisa estar sendo monitorada.
   Use a tela *Onboarding DBA* e confirme que o histórico está sendo gravado.
2. **Prepare a infraestrutura do experimento.** Execute, nesta ordem, os scripts da
   etapa *Preparar*. O job orquestrador é criado desabilitado de propósito.
3. **Dimensione a área de carga.** Ela precisa ser maior que a memória máxima do
   SQL Server, senão a injeção de memória não gera pressão nenhuma e o experimento
   falha em silêncio.
4. **Gere o plano** com a data de início desejada. A procedure devolve dois resumos —
   injeções por célula e distribuição por faixa horária. Guarde os dois: são as tabelas
   do delineamento na metodologia.
5. **Faça o teste de fumaça.** Rode cada modo uma vez, fora do plano, e confirme que a
   linha fecha com `Status = 'CONCLUIDA'` e que o efeito aparece na coleta. Apague essas
   linhas antes de iniciar a janela, ou declare no texto que foram excluídas.
6. **Habilite o orquestrador** para iniciar a janela de injeções, e desabilite para parar.
7. **Acompanhe** a cada dois ou três dias com o script de exportação. Se a cobertura da
   série cair abaixo de 98%, houve lacuna na coleta — melhor saber no meio da janela.
8. **Ao final**, extraia os CSVs do período completo em *Gerenciar Servidores*, exporte o
   log de injeções para `data/<alias>/injections.csv` e rode a avaliação.
9. **Confira o resultado** na tela *Resultado do Experimento*: as faixas coloridas devem
   coincidir com a reação da métrica correspondente.
"""


def render_experimento_scripts():
    st.header("🧪 Onboarding do Experimento - Injeção Controlada de Falhas")
    st.markdown(
        """
        O experimento provoca falhas de propósito, em horários e intensidades planejados,
        para produzir uma **verdade de referência**: sabe-se com exatidão em que minutos
        houve degradação. É o que permite medir se os limiares do painel sinalizam problemas
        reais, em vez de apenas descrevê-los.

        Baixe os arquivos abaixo e execute-os **manualmente**, via SSMS ou pelo terminal.
        Este painel nunca executa esses scripts por você.
        """
    )

    st.info(
        "O job orquestrador é criado **desabilitado**. As injeções só começam quando você "
        "o habilitar, e param quando você o desabilitar.",
        icon="⚠️",
    )

    st.markdown(PASSOS)
    st.divider()
    st.subheader("📦 Arquivos, na ordem de execução")

    etapa_atual = None
    for pasta, arquivo, etapa, descricao in ARQUIVOS:
        if etapa != etapa_atual:
            st.markdown(f"#### {etapa}")
            etapa_atual = etapa

        caminho = os.path.join(pasta, arquivo)
        origem = os.path.basename(pasta)
        st.markdown(f"**{origem}/{arquivo}** — {descricao}")

        if os.path.exists(caminho):
            with open(caminho, "r", encoding="utf-8") as f:
                conteudo = f.read()
        else:
            conteudo = f"-- Arquivo {arquivo} não encontrado em {origem}/."

        st.download_button(
            label=f"📥 Baixar {arquivo}",
            data=conteudo,
            file_name=arquivo,
            mime="text/plain",
            key=f"download_exp_{origem}_{arquivo}",
        )
        extensao = os.path.splitext(arquivo)[1].lower()
        with st.expander(f"👁️ Visualizar {arquivo}"):
            st.code(conteudo, language=LINGUAGEM.get(extensao, "text"))
        st.divider()

    st.subheader("▶️ Comando da avaliação")
    st.code("python experimento/avaliar_deteccao.py --alias LAB-SQL", language="bash")
    st.caption(
        "Rode uma vez por instância avaliada. Use --de e --ate para recortar uma campanha "
        "específica quando a série contiver mais de uma janela de injeções."
    )
