"""
App de alerta de alta - WEGE3 e LEVE3 (B3)

Instalação:
    pip install streamlit yfinance pandas plotly requests

Uso:
    streamlit run app_alerta_b3.py

Dados do Yahoo Finance (costumam ter ~15 min de atraso para a B3).
Mantenha a página aberta: o alerta só funciona enquanto o app está aberto
e rodando. Para receber aviso no celular com a página fechada, use o
Telegram (preencha na barra lateral) e deixe o app rodando em um servidor.
"""

import base64
import io
import math
import struct
import time
import wave
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="Alerta B3", page_icon="📈", layout="wide")

ATIVOS_PADRAO = ["WEGE3", "LEVE3"]


# ---------------------------------------------------------------- utilidades
@st.cache_data
def bipe_base64() -> str:
    """Gera um bipe curto em WAV (sem arquivos externos)."""
    taxa, dur, freq = 22050, 0.6, 880
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taxa)
        for i in range(int(taxa * dur)):
            v = int(16000 * math.sin(2 * math.pi * freq * i / taxa))
            w.writeframes(struct.pack("<h", v))
    return base64.b64encode(buf.getvalue()).decode()


def tocar_som() -> None:
    st.markdown(
        f'<audio autoplay><source src="data:audio/wav;base64,{bipe_base64()}" '
        'type="audio/wav"></audio>',
        unsafe_allow_html=True,
    )


def telegram(token: str, chat_id: str, msg: str) -> None:
    if not (token and chat_id):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": msg},
            timeout=10,
        )
    except Exception:
        pass


@st.cache_data(ttl=30, show_spinner=False)
def cotacao(ticker: str):
    info = yf.Ticker(f"{ticker}.SA").fast_info
    return float(info["last_price"]), float(info["previous_close"])


@st.cache_data(ttl=300, show_spinner=False)
def intradia(ticker: str) -> pd.DataFrame:
    df = yf.Ticker(f"{ticker}.SA").history(period="1d", interval="5m")
    return df.reset_index()


# ---------------------------------------------------------------- estado
if "hist" not in st.session_state:
    st.session_state.hist = {}      # ticker -> lista de (timestamp, preço)
if "ultimo" not in st.session_state:
    st.session_state.ultimo = {}    # (ticker, tipo) -> timestamp do aviso
if "log" not in st.session_state:
    st.session_state.log = []       # avisos disparados

# ---------------------------------------------------------------- barra lateral
st.sidebar.header("Configuração")
ativos = [
    a.strip().upper()
    for a in st.sidebar.text_input("Ativos (separe por vírgula)",
                                   ",".join(ATIVOS_PADRAO)).split(",")
    if a.strip()
]
alta_dia = st.sidebar.number_input("Avisar se subir no dia (%)", 0.1, 20.0, 2.0, 0.1)
alta_jan = st.sidebar.number_input("Avisar alta rápida (%)", 0.1, 20.0, 1.0, 0.1)
janela = st.sidebar.number_input("Janela da alta rápida (min)", 5, 240, 30, 5)
reavisar = st.sidebar.number_input("Reavisar após (min)", 1, 240, 30, 5)
intervalo = st.sidebar.slider("Atualizar a cada (segundos)", 15, 300, 60)
som = st.sidebar.toggle("Tocar som", value=True)

st.sidebar.subheader("Telegram (opcional)")
tg_token = st.sidebar.text_input("Token do bot", type="password")
tg_chat = st.sidebar.text_input("Chat ID")
st.sidebar.caption("Token pelo @BotFather. Use para receber o aviso no celular.")

st.title("📈 Alerta de alta - B3")
st.caption("Dados com possível atraso de ~15 min. Não é recomendação de investimento.")


# ---------------------------------------------------------------- painel
@st.fragment(run_every=intervalo)
def painel():
    agora = time.time()
    novos_avisos = []
    linhas = []

    for t in ativos:
        try:
            preco, anterior = cotacao(t)
        except Exception as e:
            st.warning(f"{t}: não consegui buscar a cotação ({e})")
            continue

        h = st.session_state.hist.setdefault(t, [])
        h.append((agora, preco))
        h[:] = [(ts, p) for ts, p in h if agora - ts <= janela * 60]

        var_dia = (preco / anterior - 1) * 100
        var_jan = (preco / h[0][1] - 1) * 100 if len(h) > 1 else 0.0
        linhas.append((t, preco, var_dia, var_jan))

        for tipo, cond, msg in (
            ("dia", var_dia >= alta_dia,
             f"{t} em alta: {var_dia:+.2f}% no dia (R$ {preco:.2f})"),
            ("janela", var_jan >= alta_jan,
             f"{t} subiu {var_jan:+.2f}% em {janela} min (R$ {preco:.2f})"),
        ):
            ult = st.session_state.ultimo.get((t, tipo))
            if cond and (ult is None or agora - ult >= reavisar * 60):
                st.session_state.ultimo[(t, tipo)] = agora
                novos_avisos.append(msg)
                st.session_state.log.insert(
                    0, f"{datetime.now():%H:%M:%S} - {msg}")
                telegram(tg_token, tg_chat, msg)

    # cartões
    if linhas:
        cols = st.columns(len(linhas))
        for col, (t, preco, vd, vj) in zip(cols, linhas):
            col.metric(t, f"R$ {preco:.2f}", f"{vd:+.2f}% no dia")
            col.caption(f"{janela} min: {vj:+.2f}%")

    # avisos novos
    for msg in novos_avisos:
        st.toast(msg, icon="🔔")
        st.success(f"🔔 {msg}")
    if novos_avisos and som:
        tocar_som()

    # gráficos do dia
    gcols = st.columns(max(len(ativos), 1))
    for col, t in zip(gcols, ativos):
        try:
            df = intradia(t)
            if not df.empty:
                x = df.iloc[:, 0]
                fig = go.Figure(go.Scatter(x=x, y=df["Close"], mode="lines", name=t))
                fig.update_layout(title=f"{t} - hoje (5 min)", height=300,
                                  margin=dict(l=10, r=10, t=40, b=10))
                col.plotly_chart(fig, use_container_width=True)
            else:
                col.info(f"{t}: sem dados intradiários (mercado fechado?).")
        except Exception:
            col.info(f"{t}: gráfico indisponível.")

    st.subheader("Histórico de avisos")
    if st.session_state.log:
        st.write("\n".join(f"- {l}" for l in st.session_state.log[:20]))
    else:
        st.caption("Nenhum aviso ainda.")

    st.caption(f"Última atualização: {datetime.now():%H:%M:%S}")


painel()