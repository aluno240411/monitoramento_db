"""Demonstração visual do limiar calibrado.

Mostra o limiar em relação à distribuição que ele deveria classificar. O limiar
fixo vem da literatura técnica e é o mesmo para qualquer instância; o calibrado
é derivado de um percentil da linha de base da própria instância. As três vistas
desta tela respondem, nessa ordem: onde cada limiar cai na distribuição, quanto
cada um alarma ao longo do tempo, e se a linha de base permite calibrar.

Lê apenas os CSVs já extraídos em data/<alias>/; não consulta nenhuma instância.
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.db_connector import load_local_metric_data
from app.views.dashboard import LIMITES
from app.views.experimento import METRICAS, carregar_injecoes, listar_logs_injecao

COR_FIXO = "#e45756"
COR_CALIBRADO = "#54a24b"
COR_BASE = "#4c78a8"
COR_INJECAO = "#f58518"

# Percentis padrão: cauda de 5% para aviso e de 1% para crítico, no sentido do desvio.
PCT_AVISO, PCT_CRITICO = 5, 1


def _percentis(direcao):
    if direcao == "abaixo":
        return PCT_AVISO, PCT_CRITICO
    return 100 - PCT_AVISO, 100 - PCT_CRITICO


def _cruza(serie, valor, direcao):
    """Fração da série que cruza o limiar, no sentido do desvio."""
    if serie.empty or valor is None or pd.isna(valor):
        return float("nan")
    return float((serie < valor).mean() if direcao == "abaixo" else (serie > valor).mean())


def _monotonicidade(serie):
    """Correlação de postos entre o valor e a ordem temporal (Spearman).

    Perto de 1 ou -1 indica série crescente ou decrescente — uma rampa. Percentis
    de uma rampa descrevem o momento da medição, não o comportamento da instância.
    """
    if len(serie) < 3 or serie.nunique() <= 1:
        return float("nan")
    ordem = pd.Series(np.arange(len(serie)), index=serie.index)
    return float(serie.rank().corr(ordem.rank()))


def _diagnostico(base, direcao, aviso_cal):
    """Verifica as duas condições de aplicabilidade da calibração."""
    avisos = []
    distintos = base.nunique()
    if distintos <= 1:
        avisos.append(
            f"A linha de base tem **um único valor** ({base.iloc[0]:,.2f}). "
            "Sem dispersão não há percentil que signifique alguma coisa: o limiar "
            "calibrado degenera em alarme contínuo ou em silêncio total."
        )
    else:
        # o percentil coincidir com o extremo observado é o mesmo problema, em grau menor
        extremo = base.min() if direcao == "acima" else base.max()
        if aviso_cal is not None and np.isclose(aviso_cal, extremo):
            avisos.append(
                f"O percentil de calibração caiu exatamente sobre o valor mais comum "
                f"({aviso_cal:,.2f}). A linha de base é concentrada demais, e qualquer "
                "desvio mínimo passa a disparar alarme."
            )
        concentracao = base.value_counts(normalize=True).iloc[0]
        if concentracao >= 0.90:
            avisos.append(
                f"**{concentracao:.1%}** das amostras da linha de base repetem o mesmo valor. "
                "A dispersão é baixa demais para calibrar com segurança."
            )

    mono = _monotonicidade(base)
    if not np.isnan(mono) and abs(mono) >= 0.95:
        avisos.append(
            f"A série da linha de base é praticamente monotônica (correlação de postos "
            f"com o tempo: **{mono:.3f}**). É uma rampa, não uma distribuição estacionária — "
            "caso típico da expectativa de vida de página numa instância sem pressão de memória, "
            "em que a métrica apenas conta o tempo desde a inicialização do serviço."
        )
    return avisos, mono


def _histograma(base, rotulo, unidade, direcao, fixo, aviso_cal, critico_cal, log_x):
    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=base, nbinsx=60, name="Linha de base",
        marker=dict(color=COR_BASE, line=dict(width=0)), opacity=0.85,
        hovertemplate="%{x}<br>%{y} amostras<extra></extra>",
    ))

    def _linha(valor, cor, texto, tracejado):
        if valor is None or pd.isna(valor):
            return
        fig.add_vline(x=valor, line_color=cor, line_dash=tracejado, line_width=2,
                      annotation_text=texto, annotation_position="top",
                      annotation_font_color=cor)

    _linha(aviso_cal, COR_CALIBRADO, f"calibrado {aviso_cal:,.2f}".replace(",", "."), "solid")
    _linha(critico_cal, COR_CALIBRADO, f"crítico {critico_cal:,.2f}".replace(",", "."), "dot")

    # O limiar fixo pode cair fora da faixa observada — é justamente o achado.
    dentro = base.min() <= fixo <= base.max()
    _linha(fixo, COR_FIXO, f"fixo {fixo}" + ("" if dentro else " — fora da faixa observada"), "dash")

    if not dentro:
        margem = (base.max() - base.min()) * 0.05 or 1
        if fixo > base.max():
            fig.update_xaxes(range=[base.min() - margem, fixo + margem])
        else:
            fig.update_xaxes(range=[fixo - margem, base.max() + margem])

    if log_x:
        fig.update_xaxes(type="log")

    fig.update_layout(
        height=360, margin=dict(l=10, r=10, t=40, b=10), bargap=0.02,
        xaxis_title=f"{rotulo}" + (f" ({unidade})" if unidade else ""),
        yaxis_title="Amostras da linha de base", showlegend=False,
    )
    return fig


def _tira_alarme(serie, coluna, direcao, fixo, calibrado, injecoes):
    """Três faixas no tempo: injeções reais, alarme fixo e alarme calibrado."""
    fig = go.Figure()
    valores = serie[coluna]

    def _marca(mascara, y, nome, cor):
        pontos = serie.index[mascara.to_numpy()]
        if len(pontos) == 0:
            return
        fig.add_trace(go.Scatter(
            x=pontos, y=np.full(len(pontos), y), mode="markers",
            name=f"{nome} ({len(pontos):,})".replace(",", "."),
            marker=dict(symbol="square", size=4, color=cor),
            hovertemplate="%{x|%d/%m %H:%M}<br>" + nome + "<extra></extra>",
        ))

    if injecoes is not None and not injecoes.empty:
        dentro = pd.Series(False, index=serie.index)
        for _, linha in injecoes.iterrows():
            dentro |= (serie.index >= linha["DataInicio"]) & (serie.index <= linha["DataFim"])
        _marca(dentro, 2, "Injeção em curso", COR_INJECAO)

    cruza = (lambda t: valores < t) if direcao == "abaixo" else (lambda t: valores > t)
    _marca(cruza(fixo).fillna(False), 1, "Alarme do limiar fixo", COR_FIXO)
    if calibrado is not None and not pd.isna(calibrado):
        _marca(cruza(calibrado).fillna(False), 0, "Alarme do limiar calibrado", COR_CALIBRADO)

    fig.update_layout(
        height=260, margin=dict(l=10, r=10, t=30, b=10),
        xaxis_title="Data / hora",
        yaxis=dict(tickmode="array", tickvals=[0, 1, 2],
                   ticktext=["Calibrado", "Fixo", "Injeção"], range=[-0.6, 2.6]),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title_text=""),
    )
    return fig


def _serie_base(base, rotulo, aviso_cal, mono):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=base.index, y=base.values, mode="lines", name="Linha de base",
        line=dict(width=1.2, color=COR_BASE),
    ))
    if aviso_cal is not None and not pd.isna(aviso_cal):
        fig.add_hline(y=aviso_cal, line_color=COR_CALIBRADO, line_width=2,
                      annotation_text=f"percentil de calibração ({aviso_cal:,.2f})".replace(",", "."),
                      annotation_position="top left", annotation_font_color=COR_CALIBRADO)
    titulo = "Linha de base ao longo do tempo"
    if not np.isnan(mono):
        titulo += f" — correlação de postos com o tempo: {mono:.3f}"
    fig.update_layout(height=280, margin=dict(l=10, r=10, t=40, b=10),
                      title=titulo, xaxis_title="Data / hora", yaxis_title=rotulo,
                      showlegend=False)
    return fig


def render_calibracao(alias, nome, arquivo_log=None):
    """Renderiza a calibração para o indicador escolhido no topo da tela.

    A métrica e o log de injeções vêm do chamador, para que as duas abas da
    página falem sempre da mesma coisa.
    """
    st.subheader(f"🎚️ Calibração de limiares — {nome}")
    st.markdown(
        """
        O limiar fixo vem da literatura técnica e vale o mesmo para qualquer instância.
        O limiar calibrado é derivado de um **percentil da linha de base da própria
        instância** — o período anterior a qualquer injeção. As vistas abaixo mostram
        onde cada um cai na distribuição, quanto cada um alarma, e se a linha de base
        tem as condições necessárias para calibrar.
        """
    )

    if nome not in METRICAS:
        st.error(f"Indicador `{nome}` desconhecido.")
        return
    arquivo, coluna, unidade, chave = METRICAS[nome]
    cfg = LIMITES.get(chave)
    if cfg is None:
        st.error(f"Indicador `{chave}` não está definido em LIMITES.")
        return
    direcao, fixo = cfg["direcao"], cfg["aviso"]

    dados = load_local_metric_data(alias, arquivo)
    if dados.empty or coluna not in dados.columns:
        st.error(f"Não foi possível ler a coluna `{coluna}` em `{arquivo}`.")
        return

    serie = (dados[["DataColeta", coluna]].dropna(subset=["DataColeta"])
             .set_index("DataColeta").sort_index())
    if chave == "buffer_cache_hit_pct":
        # artefato conhecido dos contadores de razão: leitura não simultânea
        serie[coluna] = serie[coluna].clip(0, 100)

    if arquivo_log is None:
        logs = listar_logs_injecao(alias)
        arquivo_log = ("injections.csv" if "injections.csv" in logs else logs[0]) if logs else None
    injecoes = pd.DataFrame()
    if arquivo_log:
        injecoes = carregar_injecoes(alias, arquivo_log)
        if "Status" in injecoes.columns:
            injecoes = injecoes[injecoes["Status"] == "CONCLUIDA"]

    c1, c2 = st.columns([2, 2])
    with c1:
        opcoes = (["Antes da primeira injeção", "Período completo"] if not injecoes.empty
                  else ["Período completo"])
        escopo_base = st.radio("Linha de base", opcoes, horizontal=True, key="calib_base")
    with c2:
        faixa = serie[coluna].replace(0, np.nan).dropna()
        auto_log = bool(len(faixa) and faixa.max() / max(faixa.min(), 1e-9) > 1000)
        log_x = st.checkbox("Escala logarítmica no histograma", value=auto_log, key="calib_log")

    if escopo_base == "Antes da primeira injeção" and not injecoes.empty:
        corte = injecoes["DataInicio"].min()
        base = serie.loc[serie.index < corte, coluna].dropna()
        legenda_base = f"{len(base):,} amostras até {corte:%d/%m/%Y %H:%M}".replace(",", ".")
    else:
        base = serie[coluna].dropna()
        legenda_base = f"{len(base):,} amostras (período completo)".replace(",", ".")

    if base.empty:
        st.warning("Não há amostras na linha de base escolhida.")
        return

    p_aviso, p_critico = _percentis(direcao)
    aviso_cal = float(np.percentile(base, p_aviso))
    critico_cal = float(np.percentile(base, p_critico))

    st.caption(
        f"Linha de base: {legenda_base}. Sentido do desvio: **{direcao}**. "
        f"Calibração pelo percentil {p_aviso} (aviso) e {p_critico} (crítico)."
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Limiar fixo (aviso)", f"{fixo}")
    m2.metric("Limiar calibrado (aviso)", f"{aviso_cal:,.2f}".replace(",", "."))
    m3.metric("Linha de base que o fixo cruza", f"{_cruza(base, fixo, direcao):.1%}")
    m4.metric("Linha de base que o calibrado cruza", f"{_cruza(base, aviso_cal, direcao):.1%}")

    avisos, mono = _diagnostico(base, direcao, aviso_cal)

    aba1, aba2, aba3 = st.tabs([
        "📊 Onde cai cada limiar", "🚨 Quanto cada um alarma", "🩺 A base permite calibrar?"
    ])

    with aba1:
        st.plotly_chart(
            _histograma(base, nome, unidade, direcao, fixo, aviso_cal, critico_cal, log_x),
            use_container_width=True,
        )
        st.caption(
            "A barra azul é a distribuição dos valores normais da instância. A linha vermelha é "
            "o limiar da literatura; a verde, o derivado desta instância. Um limiar que corta o "
            "corpo da distribuição alarma no comportamento normal; um que cai fora da faixa "
            "observada nunca dispara."
        )

    with aba2:
        st.plotly_chart(
            _tira_alarme(serie, coluna, direcao, fixo, aviso_cal,
                         injecoes if not injecoes.empty else None),
            use_container_width=True,
        )
        st.caption(
            "Cada quadradinho é um minuto em alarme. A faixa superior marca os minutos em que "
            "havia falha provocada de verdade — é contra ela que as outras duas devem ser lidas."
        )

    with aba3:
        if avisos:
            for texto in avisos:
                st.warning(texto, icon="⚠️")
            st.markdown(
                "**Consequência:** nesta instância, calibrar por percentil troca cegueira por "
                "ruído. A recomendação é exigir dispersão mínima na linha de base e descartar "
                "indicadores cuja série não seja estacionária."
            )
        else:
            st.success(
                "A linha de base tem dispersão e não é monotônica: as duas condições para "
                "calibrar por percentil estão satisfeitas.",
                icon="✅",
            )
        st.plotly_chart(_serie_base(base, nome, aviso_cal, mono), use_container_width=True)
        d1, d2, d3 = st.columns(3)
        d1.metric("Valores distintos", f"{base.nunique():,}".replace(",", "."))
        d2.metric("Valor mais frequente", f"{base.value_counts(normalize=True).iloc[0]:.1%}")
        d3.metric("Correlação de postos com o tempo", "—" if np.isnan(mono) else f"{mono:.3f}")
