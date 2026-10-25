"""
App de alerta de alta - WEGE3 e LEVE3 (B3)

Versão sem yfinance e sem plotly: usa só streamlit, pandas e requests,
que já vêm instalados no Streamlit Cloud. Não precisa de requirements.txt.

Local:  streamlit run avisoalta.py
"""

import base64
import io
import math
import struct
import time
import wave
from datetime import datetime

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Alerta B3", page_icon="📈", layout="wide")

ATIVOS_PADRAO = ["WEGE3", "LEVE3"]
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; alerta-b3/1.0)"}


# ---------------------------------------------------------------- utilidades
@st.cache_data
def bipe_base64() -> str:
    taxa, dur, freq = 22050, 0.6, 880
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taxa)
        for i in range(int(taxa * dur)):
            w.writeframes(struct.pack(
                "<h", int(16000 * math.sin(2 * math.pi * freq * i / taxa))))
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
            data={"chat_id": chat_id, "text": msg}, timeout=10)
    except Exception:
        pass


@st.cache_data(ttl=30, show_spinner=False)
def buscar(ticker: str):
    """Retorna (preço atual, fechamento anterior, DataFrame intradiário)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}.SA"
    r = requests.get(url, params={"interval": "5m", "range": "1d"},
                     headers=HEADERS, timeout=15)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    meta = res["meta"]
    preco = float(meta["regularMarketPrice"])
    anterior = float(meta.get("chartPreviousClose") or meta.get("previousClose"))

    ts = res.get("timestamp") or []
    close = (res["indicators"]["quote"][0].get("close")) or []
    df = pd.DataFrame({"hora": pd.to_datetime(ts, unit="s", utc=True)
                       .tz_convert("America/Sao_Paulo"), "preço": close}).dropna()
    return preco, anterior, df.set_index("hora")


def segredo(nome: str) -> str:
    try:
        return st.secrets.get(nome, "")
    except Exception:
        return ""


# ---------------------------------------------------------------- estado
ss = st.session_state
ss.setdefault("hist", {})
ss.setdefault("ultimo", {})
ss.setdefault("log", [])

# ---------------------------------------------------------------- barra lateral
st.sidebar.header("Configuração")
ativos = [a.strip().upper() for a in st.sidebar.text_input(
    "Ativos (separe por vírgula)", ",".join(ATIVOS_PADRAO)).split(",") if a.strip()]
alta_dia = st.sidebar.number_input("Avisar se subir no dia (%)", 0.1, 20.0, 2.0, 0.1)
alta_jan = st.sidebar.number_input("Avisar alta rápida (%)", 0.1, 20.0, 1.0, 0.1)
janela = st.sidebar.number_input("Janela da alta rápida (min)", 5, 240, 30, 5)
reavisar = st.sidebar.number_input("Reavisar após (min)", 1, 240, 30, 5)
intervalo = st.sidebar.slider("Atualizar a cada (segundos)", 15, 300, 60)
som = st.sidebar.toggle("Tocar som", value=True)

st.sidebar.subheader("Telegram (opcional)")
tg_token = st.sidebar.text_input("Token do bot", value=segredo("TELEGRAM_TOKEN"),
                                 type="password")
tg_chat = st.sidebar.text_input("Chat ID", value=segredo("TELEGRAM_CHAT_ID"))

st.title("📈 Alerta de alta - B3")
st.caption("Dados com possível atraso de ~15 min. Não é recomendação de investimento.")


# ---------------------------------------------------------------- painel
@st.fragment(run_every=intervalo)
def painel():
    agora = time.time()
    novos, linhas, series = [], [], {}

    for t in ativos:
        try:
            preco, anterior, df = buscar(t)
        except Exception as e:
            st.warning(f"{t}: não consegui buscar a cotação ({e})")
            continue

        h = ss.hist.setdefault(t, [])
        h.append((agora, preco))
        h[:] = [(ts, p) for ts, p in h if agora - ts <= janela * 60]

        vd = (preco / anterior - 1) * 100
        vj = (preco / h[0][1] - 1) * 100 if len(h) > 1 else 0.0
        linhas.append((t, preco, vd, vj))
        series[t] = df

        for tipo, cond, msg in (
            ("dia", vd >= alta_dia, f"{t} em alta: {vd:+.2f}% no dia (R$ {preco:.2f})"),
            ("janela", vj >= alta_jan, f"{t} subiu {vj:+.2f}% em {janela} min (R$ {preco:.2f})"),
        ):
            ult = ss.ultimo.get((t, tipo))
            if cond and (ult is None or agora - ult >= reavisar * 60):
                ss.ultimo[(t, tipo)] = agora
                novos.append(msg)
                ss.log.insert(0, f"{datetime.now():%H:%M:%S} - {msg}")
                telegram(tg_token, tg_chat, msg)

    if linhas:
        for col, (t, preco, vd, vj) in zip(st.columns(len(linhas)), linhas):
            col.metric(t, f"R$ {preco:.2f}", f"{vd:+.2f}% no dia")
            col.caption(f"{janela} min: {vj:+.2f}%")

    for msg in novos:
        st.toast(msg, icon="🔔")
        st.success(f"🔔 {msg}")
    if novos and som:
        tocar_som()

    if series:
        for col, (t, df) in zip(st.columns(len(series)), series.items()):
            col.markdown(f"**{t} - hoje (5 min)**")
            if df.empty:
                col.info("Sem dados intradiários (mercado fechado?).")
            else:
                col.line_chart(df["preço"], height=280)

    st.subheader("Histórico de avisos")
    if ss.log:
        st.write("\n".join(f"- {l}" for l in ss.log[:20]))
    else:
        st.caption("Nenhum aviso ainda.")
    st.caption(f"Última atualização: {datetime.now():%H:%M:%S}")


painel()