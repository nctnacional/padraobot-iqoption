# -*- coding: utf-8 -*-
import os
import time
import threading
import uuid
from flask import Flask, render_template_string, request, jsonify
from iqoptionapi.stable_api import IQ_Option

app = Flask(__name__)

# Armazena as sessões ativas de cada usuário (multi-usuário)
SESSOES = {}

DIRECAO = {
    # Padrões individuais de candle (15)
    "engulfing_bull": "call",
    "engulfing_bear": "put",
    "hammer": "call",
    "shooting_star": "put",
    "morning_star": "call",
    "evening_star": "put",
    "three_soldiers": "call",
    "three_crows": "put",
    "double_bottom": "call",
    "double_top": "put",
    "doji_bull": "call",
    "doji_bear": "put",
    "marubozu_bull": "call",
    "marubozu_bear": "put",
    "tweezer_bottom": "call",
    # Conjuntos de candles / Estratégias
    "estrategia_1_5candles": "auto",
    "estrategia_2_reversao": "auto",
    "estrategia_5_m3": "auto",
}

NOMES_ITENS = {
    "engulfing_bull": "Engolfo de Alta (CALL)",
    "engulfing_bear": "Engolfo de Baixa (PUT)",
    "hammer": "Martelo (CALL)",
    "shooting_star": "Estrela Cadente (PUT)",
    "morning_star": "Estrela da Manhã (CALL)",
    "evening_star": "Estrela da Noite (PUT)",
    "three_soldiers": "Três Soldados (CALL)",
    "three_crows": "Três Corvos (PUT)",
    "double_bottom": "Fundo Duplo (CALL)",
    "double_top": "Topo Duplo (PUT)",
    "doji_bull": "Doji de Alta (CALL)",
    "doji_bear": "Doji de Baixa (PUT)",
    "marubozu_bull": "Marubozu de Alta (CALL)",
    "marubozu_bear": "Marubozu de Baixa (PUT)",
    "tweezer_bottom": "Pinça de Fundo (CALL)",
}

# ---------------- FUNÇÕES AUXILIARES & EMAs ----------------
def corpo(c): return abs(c["close"] - c["open"])
def faixa(c): return c["high"] - c["low"]
def pavio_sup(c): return c["high"] - max(c["open"], c["close"])
def pavio_inf(c): return min(c["open"], c["close"]) - c["low"]
def alta(c): return c["close"] > c["open"]
def baixa(c): return c["close"] < c["open"]
def meio(c): return (c["open"] + c["close"]) / 2

def media_corpo(cs, fim, n=10):
    s = cs[max(0, fim - n):fim]
    return sum(corpo(c) for c in s) / len(s) if s else 0

def media_faixa(cs, n=14):
    s = cs[-n:]
    return sum(faixa(c) for c in s) / len(s) if s else 0

def calcular_ema_serie(valores, periodo):
    if not valores or len(valores) < periodo:
        return []
    k = 2 / (periodo + 1)
    ema = sum(valores[:periodo]) / periodo
    serie = [None] * (periodo - 1) + [ema]
    for preco in valores[periodo:]:
        ema = (preco * k) + (ema * (1 - k))
        serie.append(ema)
    return serie

def emas_alinhadas(cs, direcao):
    """Verifica se EMA 24 e EMA 32 estão alinhadas na direção do sinal."""
    closes = [c["close"] for c in cs]
    ema24_s = calcular_ema_serie(closes, 24)
    ema32_s = calcular_ema_serie(closes, 32)
    if not ema24_s or not ema32_s:
        return False
    e24 = ema24_s[-1]
    e32 = ema32_s[-1]
    if e24 is None or e32 is None:
        return False
    ultimo_close = closes[-1]
    if direcao == "call":
        return e24 > e32 and ultimo_close >= e24
    if direcao == "put":
        return e24 < e32 and ultimo_close <= e24
    return False

# ---------------- PADRÕES INDIVIDUAIS ----------------
def engulfing_bull(cs):
    p, c = cs[-2], cs[-1]
    return baixa(p) and alta(c) and c["open"] <= p["close"] and c["close"] >= p["open"] and corpo(c) > corpo(p)

def engulfing_bear(cs):
    p, c = cs[-2], cs[-1]
    return alta(p) and baixa(c) and c["open"] >= p["close"] and c["close"] <= p["open"] and corpo(c) > corpo(p)

def hammer(cs):
    c, antes, cedo = cs[-1], cs[-2], cs[-4]
    r = faixa(c)
    if r <= 0: return False
    return cedo["close"] > antes["close"] and corpo(c) <= 0.35 * r and pavio_inf(c) >= 2 * corpo(c) \
        and pavio_inf(c) >= 0.6 * r and pavio_sup(c) <= 0.15 * r

def shooting_star(cs):
    c, antes, cedo = cs[-1], cs[-2], cs[-4]
    r = faixa(c)
    if r <= 0: return False
    return cedo["close"] < antes["close"] and corpo(c) <= 0.35 * r and pavio_sup(c) >= 2 * corpo(c) \
        and pavio_sup(c) >= 0.6 * r and pavio_inf(c) <= 0.15 * r

def morning_star(cs):
    a, b, c = cs[-3], cs[-2], cs[-1]
    m = media_corpo(cs, len(cs) - 3) or corpo(a)
    return baixa(a) and corpo(a) >= m and corpo(b) <= 0.4 * corpo(a) and alta(c) and c["close"] > meio(a)

def evening_star(cs):
    a, b, c = cs[-3], cs[-2], cs[-1]
    m = media_corpo(cs, len(cs) - 3) or corpo(a)
    return alta(a) and corpo(a) >= m and corpo(b) <= 0.4 * corpo(a) and baixa(c) and c["close"] < meio(a)

def three_soldiers(cs):
    a, b, c = cs[-3], cs[-2], cs[-1]
    solida = lambda x: alta(x) and faixa(x) > 0 and corpo(x) >= 0.5 * faixa(x)
    return solida(a) and solida(b) and solida(c) and b["close"] > a["close"] and c["close"] > b["close"] \
        and a["open"] <= b["open"] <= a["close"] and b["open"] <= c["open"] <= b["close"]

def three_crows(cs):
    a, b, c = cs[-3], cs[-2], cs[-1]
    solida = lambda x: baixa(x) and faixa(x) > 0 and corpo(x) >= 0.5 * faixa(x)
    return solida(a) and solida(b) and solida(c) and b["close"] < a["close"] and c["close"] < b["close"] \
        and a["close"] <= b["open"] <= a["open"] and b["close"] <= c["open"] <= b["open"]

def swings(cs, tipo, w=2, lookback=40):
    out = []
    ultimo = len(cs) - 2
    for i in range(max(w, len(cs) - 1 - lookback), ultimo - w + 1):
        c = cs[i]
        ok = True
        for j in range(i - w, i + w + 1):
            if j == i: continue
            if (tipo == "high" and cs[j]["high"] > c["high"]) or (tipo == "low" and cs[j]["low"] < c["low"]):
                ok = False
                break
        if ok: out.append(i)
    return out

def double_top(cs):
    h = swings(cs, "high")
    if len(h) < 2: return False
    ia, ib = h[-2], h[-1]
    if ib - ia < 4: return False
    A, B = cs[ia], cs[ib]
    if abs(A["high"] - B["high"]) > 0.5 * media_faixa(cs): return False
    meio_ = cs[ia + 1:ib]
    pescoco = min(x["low"] for x in meio_)
    if any(x["high"] > min(A["high"], B["high"]) for x in meio_): return False
    return cs[-2]["close"] >= pescoco and cs[-1]["close"] < pescoco

def double_bottom(cs):
    l = swings(cs, "low")
    if len(l) < 2: return False
    ia, ib = l[-2], l[-1]
    if ib - ia < 4: return False
    A, B = cs[ia], cs[ib]
    if abs(A["low"] - B["low"]) > 0.5 * media_faixa(cs): return False
    meio_ = cs[ia + 1:ib]
    pescoco = max(x["high"] for x in meio_)
    if any(x["low"] < max(A["low"], B["low"]) for x in meio_): return False
    return cs[-2]["close"] <= pescoco and cs[-1]["close"] > pescoco

def doji_bull(cs):
    p, c = cs[-2], cs[-1]
    r = faixa(c)
    if r <= 0: return False
    return baixa(p) and corpo(c) <= 0.1 * r and pavio_inf(c) >= 0.3 * r

def doji_bear(cs):
    p, c = cs[-2], cs[-1]
    r = faixa(c)
    if r <= 0: return False
    return alta(p) and corpo(c) <= 0.1 * r and pavio_sup(c) >= 0.3 * r

def marubozu_bull(cs):
    c = cs[-1]
    r = faixa(c)
    if r <= 0: return False
    return alta(c) and corpo(c) >= 0.8 * r and pavio_sup(c) <= 0.1 * r and pavio_inf(c) <= 0.1 * r

def marubozu_bear(cs):
    c = cs[-1]
    r = faixa(c)
    if r <= 0: return False
    return baixa(c) and corpo(c) >= 0.8 * r and pavio_sup(c) <= 0.1 * r and pavio_inf(c) <= 0.1 * r

def tweezer_bottom(cs):
    p, c = cs[-2], cs[-1]
    mf = media_faixa(cs) or faixa(c)
    if mf <= 0: return False
    return baixa(p) and alta(c) and abs(p["low"] - c["low"]) <= 0.15 * mf

DETECTORES_INDIVIDUAIS = {
    "engulfing_bull": engulfing_bull,
    "engulfing_bear": engulfing_bear,
    "hammer": hammer,
    "shooting_star": shooting_star,
    "morning_star": morning_star,
    "evening_star": evening_star,
    "three_soldiers": three_soldiers,
    "three_crows": three_crows,
    "double_bottom": double_bottom,
    "double_top": double_top,
    "doji_bull": doji_bull,
    "doji_bear": doji_bear,
    "marubozu_bull": marubozu_bull,
    "marubozu_bear": marubozu_bear,
    "tweezer_bottom": tweezer_bottom,
}

# ---------------- ESTRATÉGIAS DE CONJUNTO DE CANDLES ----------------
def sinal_estrategia_1_5candles(cs):
    """Estratégia 1: Classificador de 5 candles (retorna 'call', 'put' ou None)."""
    if len(cs) < 5:
        return None
    janela = cs[-5:]
    altas = sum(1 for c in janela if alta(c))
    baixas = sum(1 for c in janela if baixa(c))
    if altas > baixas and altas >= 3:
        return "call"
    if baixas > altas and baixas >= 3:
        return "put"
    return None

def sinal_estrategia_2_reversao(cs, minimo=3):
    """Estratégia 2: Reversão após N candles consecutivos anteriores."""
    minimo = max(2, min(8, int(minimo)))
    if len(cs) < minimo + 1:
        return None
    anteriores = cs[-(minimo + 1):-1]
    atual = cs[-1]
    if all(baixa(c) for c in anteriores) and alta(atual):
        return "call"
    if all(alta(c) for c in anteriores) and baixa(atual):
        return "put"
    return None

def sinal_estrategia_5_m3(cs):
    """Estratégia 5: Maior Menor Menor medindo apenas abertura e fechamento (sem filtro EMA)."""
    if len(cs) < 3:
        return None
    a, b, c = cs[-3], cs[-2], cs[-1]
    if b["close"] > a["close"] and c["close"] < b["close"] and baixa(c):
        return "put"
    if b["close"] < a["close"] and c["close"] > b["close"] and alta(c):
        return "call"
    return None

def avaliar_sinais(cs, cfg, log_fn=None):
    usar_ema = bool(cfg.get("usar_ema_filtro", True))
    minimo_rev = int(cfg.get("reversao_minimo", 3))
    padroes_sel = cfg.get("padroes", [])
    e1_ativo = bool(cfg.get("est1_ativo", True))
    e2_ativo = bool(cfg.get("est2_ativo", True))
    e5_ativo = bool(cfg.get("est5_ativo", True))

    sinais_validos = []

    for p in padroes_sel:
        if p in DETECTORES_INDIVIDUAIS and DETECTORES_INDIVIDUAIS[p](cs):
            dir_p = DIRECAO[p]
            if usar_ema and not emas_alinhadas(cs, dir_p):
                if log_fn:
                    log_fn(f"Sinal {NOMES_ITENS.get(p, p)} ({dir_p.upper()}) filtrado por EMA 24/32.")
                continue
            sinais_validos.append((p, dir_p))

    if e1_ativo:
        d1 = sinal_estrategia_1_5candles(cs)
        if d1:
            if usar_ema and not emas_alinhadas(cs, d1):
                if log_fn:
                    log_fn(f"Est. 1 - 5 Candles ({d1.upper()}) filtrado por EMA 24/32.")
            else:
                sinais_validos.append(("Est. 1 (5 Candles)", d1))

    if e2_ativo:
        d2 = sinal_estrategia_2_reversao(cs, minimo_rev)
        if d2:
            if usar_ema and not emas_alinhadas(cs, d2):
                if log_fn:
                    log_fn(f"Est. 2 - Reversão [{minimo_rev}] ({d2.upper()}) filtrado por EMA 24/32.")
            else:
                sinais_validos.append((f"Est. 2 (Reversão {minimo_rev})", d2))

    if e5_ativo:
        d5 = sinal_estrategia_5_m3(cs)
        if d5:
            sinais_validos.append(("Est. 5 (Maior Menor Menor)", d5))

    if not sinais_validos:
        return None, []

    nomes_achados = [nome for nome, _ in sinais_validos]
    direcoes = {d for _, d in sinais_validos}
    if len(direcoes) != 1:
        return None, nomes_achados

    return direcoes.pop(), nomes_achados

def converter(v):
    return {"time": v["from"], "open": v["open"], "close": v["close"], "high": v["max"], "low": v["min"]}

# ---------------- EXECUÇÃO DO ROBÔ EM THREAD ----------------
def log_msg(sessao_id, texto):
    if sessao_id in SESSOES:
        hora = time.strftime("%H:%M:%S")
        SESSOES[sessao_id]["logs"].append(f"[{hora}] {texto}")
        SESSOES[sessao_id]["logs"] = SESSOES[sessao_id]["logs"][-50:]

def rodar_robo(sessao_id, cfg):
    sessao = SESSOES[sessao_id]
    log_msg(sessao_id, "Conectando à IQ Option...")

    iq = IQ_Option(cfg["email"], cfg["senha"])
    ok, motivo = iq.connect()
    if not ok:
        log_msg(sessao_id, f"Erro ao conectar: {motivo}")
        sessao["rodando"] = False
        return

    iq.change_balance(cfg["conta"])
    saldo = iq.get_balance()
    sessao["saldo"] = saldo
    log_msg(sessao_id, f"Conectado! Conta: {cfg['conta']} | Saldo: {saldo} | Ativo: {cfg['ativo']}")

    padroes_ativos = [p for p in cfg.get("padroes", []) if p in DETECTORES_INDIVIDUAIS]
    e1_ativo = bool(cfg.get("est1_ativo", True))
    e2_ativo = bool(cfg.get("est2_ativo", True))
    e5_ativo = bool(cfg.get("est5_ativo", True))

    if not padroes_ativos and not (e1_ativo or e2_ativo or e5_ativo):
        log_msg(sessao_id, "Nenhuma estratégia ou padrão ativado! Marque ao menos uma opção.")
        sessao["rodando"] = False
        return

    filtro_txt = "ON (EMA 24/32)" if cfg.get("usar_ema_filtro", True) else "OFF"
    log_msg(sessao_id, f"Filtro EMA 24/32: {filtro_txt} | Padrões: {len(padroes_ativos)} | Est1:{e1_ativo} Est2:{e2_ativo} Est5:{e5_ativo}")

    timeframe = int(cfg["timeframe"])
    expiracao_min = max(1, (timeframe // 60) * int(cfg["expiracao_velas"]))

    while sessao["rodando"]:
        agora = iq.get_server_timestamp()
        espera = timeframe - (agora % timeframe) + 1

        for _ in range(int(espera)):
            if not sessao["rodando"]:
                break
            time.sleep(1)

        if not sessao["rodando"]:
            break

        if not iq.check_connect():
            iq.connect()

        velas = [converter(v) for v in iq.get_candles(cfg["ativo"], timeframe, 80, time.time())]
        fechadas = velas[:-1]
        if len(fechadas) < 35:
            continue

        direcao, achados = avaliar_sinais(
            fechadas,
            cfg,
            log_fn=lambda msg: log_msg(sessao_id, msg)
        )
        if not achados:
            continue
        if direcao is None:
            log_msg(sessao_id, f"Sinais em conflito ignorados: {', '.join(achados)}")
            continue

        valor = float(cfg["valor"])
        gale_max = int(cfg["gale_max"]) if cfg["gale"] else 0
        gale_mult = float(cfg["gale_mult"])

        for nivel in range(0, gale_max + 1):
            if not sessao["rodando"]:
                break
            log_msg(sessao_id, f"Entrada {direcao.upper()} R${valor} | {', '.join(achados)} | Gale {nivel}")
            ok, ordem = iq.buy(valor, cfg["ativo"], direcao, expiracao_min)
            if not ok:
                log_msg(sessao_id, f"Ordem recusada: {ordem}")
                break

            resultado = iq.check_win_v3(ordem)
            sessao["lucro"] = round(sessao["lucro"] + resultado, 2)
            sessao["saldo"] = iq.get_balance()
            log_msg(sessao_id, f"Resultado: {round(resultado, 2)} | Lucro sessão: {sessao['lucro']}")

            if resultado >= 0:
                break
            valor = round(valor * gale_mult, 2)

        stop_win = float(cfg["stop_win"])
        stop_loss = float(cfg["stop_loss"])
        if stop_win > 0 and sessao["lucro"] >= stop_win:
            log_msg(sessao_id, "Stop Win atingido! Robô encerrado.")
            sessao["rodando"] = False
            break
        if stop_loss > 0 and sessao["lucro"] <= -stop_loss:
            log_msg(sessao_id, "Stop Loss atingido! Robô encerrado.")
            sessao["rodando"] = False
            break

    log_msg(sessao_id, "Robô parado.")

# ---------------- INTERFACE WEB (HTML/CSS/JS) ----------------
HTML_PAGE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BOT RLTRADER</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #070d19;
            color: #e2e8f0;
            margin: 0;
            padding: 16px;
            min-height: 100vh;
        }
        .container {
            width: 100%;
            max-width: 1240px;
            margin: 0 auto;
            background: #0d172a;
            padding: 20px;
            border-radius: 14px;
            border: 1px solid #1e3a8a;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.65);
        }
        h2 {
            text-align: center;
            color: #60a5fa;
            margin: 0 0 18px 0;
            letter-spacing: 1px;
            font-size: clamp(20px, 3vw, 26px);
        }
        .main-grid {
            display: grid;
            grid-template-columns: minmax(0, 1.45fr) minmax(0, 0.95fr);
            gap: 18px;
            align-items: start;
        }
        .col-left {
            display: flex;
            flex-direction: column;
            gap: 14px;
            min-width: 0;
        }
        .col-right {
            display: flex;
            flex-direction: column;
            gap: 14px;
            min-width: 0;
            position: sticky;
            top: 16px;
        }
        .panel-box {
            background: #11203b;
            padding: 14px;
            border-radius: 10px;
            border: 1px solid #1d3557;
        }
        label {
            display: block;
            margin-top: 10px;
            font-size: 13px;
            color: #93c5fd;
        }
        .panel-box > label:first-child,
        .row > div > label:first-child {
            margin-top: 0;
        }
        input, select {
            width: 100%;
            padding: 9px 10px;
            margin-top: 5px;
            border-radius: 7px;
            border: 1px solid #1e3a8a;
            background: #081224;
            color: #f8fafc;
            font-size: 14px;
        }
        input:focus, select:focus {
            outline: none;
            border-color: #3b82f6;
        }
        .row {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
            margin-top: 8px;
        }
        .gale-inputs {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 8px;
            margin-top: 5px;
        }
        .gale-inputs input {
            margin-top: 0;
        }
        .secao-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
            flex-wrap: wrap;
            gap: 8px;
        }
        .secao-header strong {
            font-size: 13.5px;
            color: #dbeafe;
        }
        .acoes-rapidas {
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
        }
        .btn-mini {
            width: auto;
            margin-top: 0;
            padding: 5px 10px;
            font-size: 12px;
            border-radius: 6px;
            background: #172554;
            color: #bfdbfe;
            border: 1px solid #2563eb;
            font-weight: 600;
            cursor: pointer;
        }
        .btn-mini:hover {
            background: #1e3a8a;
        }
        .est-card {
            background: #091428;
            border: 1px solid #1e3a8a;
            border-radius: 8px;
            padding: 10px 12px;
            margin-bottom: 8px;
        }
        .est-card:last-child {
            margin-bottom: 0;
        }
        .est-top {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            flex-wrap: wrap;
        }
        .est-check {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-top: 0;
            cursor: pointer;
            color: #f1f5f9;
            font-size: 13px;
            font-weight: 600;
        }
        .est-check input[type="checkbox"] {
            width: auto;
            margin: 0;
            cursor: pointer;
            accent-color: #2563eb;
        }
        .badge-ema {
            font-size: 11px;
            padding: 2px 8px;
            border-radius: 999px;
            font-weight: 600;
            white-space: nowrap;
        }
        .badge-sim {
            background: rgba(37, 99, 235, 0.22);
            color: #93c5fd;
            border: 1px solid rgba(59, 130, 246, 0.5);
        }
        .badge-nao {
            background: rgba(30, 58, 138, 0.25);
            color: #cbd5e1;
            border: 1px solid rgba(148, 163, 184, 0.35);
        }
        .est-sub {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            margin-top: 8px;
            font-size: 12px;
            color: #93c5fd;
            flex-wrap: wrap;
        }
        .est-sub input[type="number"] {
            width: 78px;
            padding: 5px 8px;
            margin-top: 0;
            font-size: 13px;
        }
        .padroes-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 7px;
        }
        .padrao-item {
            display: flex;
            align-items: center;
            gap: 7px;
            background: #091428;
            padding: 7px 9px;
            border-radius: 7px;
            border: 1px solid #1e3a8a;
            margin-top: 0;
            cursor: pointer;
            color: #e2e8f0;
            font-size: 12px;
        }
        .padrao-item input[type="checkbox"] {
            width: auto;
            margin: 0;
            cursor: pointer;
            accent-color: #2563eb;
            flex-shrink: 0;
        }
        button.btn-main {
            width: 100%;
            padding: 13px;
            margin-top: 0;
            border: none;
            border-radius: 8px;
            font-size: 15px;
            font-weight: 700;
            cursor: pointer;
            letter-spacing: 0.5px;
        }
        .btn-start {
            background: linear-gradient(135deg, #1d4ed8, #1e3a8a);
            color: #ffffff;
            border: 1px solid #3b82f6;
        }
        .btn-start:hover {
            background: linear-gradient(135deg, #2563eb, #1d4ed8);
        }
        .btn-stop {
            background: #b91c1c;
            color: #ffffff;
            display: none;
            border: 1px solid #ef4444;
        }
        .status-box {
            display: flex;
            justify-content: space-between;
            gap: 10px;
            flex-wrap: wrap;
            background: #091428;
            padding: 12px 14px;
            border-radius: 8px;
            border: 1px solid #1e3a8a;
            font-weight: 700;
            font-size: 14px;
            color: #bfdbfe;
        }
        .resumo-lista {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 8px;
            margin-top: 4px;
        }
        .resumo-item {
            background: #091428;
            border: 1px solid #1e3a8a;
            border-radius: 7px;
            padding: 8px 10px;
            font-size: 12px;
            color: #93c5fd;
        }
        .resumo-item strong {
            display: block;
            color: #f8fafc;
            font-size: 13px;
            margin-top: 2px;
        }
        .logs {
            background: #040914;
            color: #60a5fa;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 12px;
            height: 310px;
            overflow-y: auto;
            padding: 12px;
            border-radius: 8px;
            border: 1px solid #1e3a8a;
            line-height: 1.45;
        }
        @media (max-width: 980px) {
            .padroes-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }
        @media (max-width: 860px) {
            .main-grid {
                grid-template-columns: 1fr;
            }
            .col-right {
                position: static;
            }
            .logs {
                height: 220px;
            }
        }
        @media (max-width: 520px) {
            body {
                padding: 10px;
            }
            .container {
                padding: 14px;
            }
            .row, .resumo-lista {
                grid-template-columns: 1fr;
            }
            .padroes-grid {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
<div class="container">
    <h2>BOT RLTRADER</h2>

    <div class="main-grid">
        <div class="col-left" id="form-config">
            <div class="panel-box">
                <label>E-mail IQ Option</label>
                <input type="email" id="email" placeholder="seu@email.com" required>

                <label>Senha IQ Option</label>
                <input type="password" id="senha" placeholder="********" required>

                <div class="row">
                    <div>
                        <label>Conta</label>
                        <select id="conta">
                            <option value="PRACTICE">PRACTICE (Treino)</option>
                            <option value="REAL">REAL</option>
                        </select>
                    </div>
                    <div>
                        <label>Ativo</label>
                        <input type="text" id="ativo" value="BTCUSD">
                    </div>
                </div>

                <div class="row">
                    <div>
                        <label>Valor Entrada</label>
                        <input type="number" id="valor" value="2" step="0.5">
                    </div>
                    <div>
                        <label>Timeframe (seg)</label>
                        <select id="timeframe">
                            <option value="60">1 Minuto (60s)</option>
                            <option value="300">5 Minutos (300s)</option>
                        </select>
                    </div>
                </div>

                <div class="row">
                    <div>
                        <label>Stop Win</label>
                        <input type="number" id="stop_win" value="20">
                    </div>
                    <div>
                        <label>Stop Loss</label>
                        <input type="number" id="stop_loss" value="20">
                    </div>
                </div>

                <div class="row">
                    <div>
                        <label>Usar Martingale?</label>
                        <select id="gale">
                            <option value="false">Não</option>
                            <option value="true">Sim</option>
                        </select>
                    </div>
                    <div>
                        <label>Gale Máx / Mult</label>
                        <div class="gale-inputs">
                            <input type="number" id="gale_max" value="1">
                            <input type="number" id="gale_mult" value="2.2" step="0.1">
                        </div>
                    </div>
                </div>
            </div>

            <!-- FILTRO DE MÉDIAS MÓVEIS EMA 24 E EMA 32 -->
            <div class="panel-box">
                <div class="secao-header" style="margin-bottom: 0;">
                    <label class="est-check" style="font-size: 13.5px;">
                        <input type="checkbox" id="usar_ema_filtro" checked onchange="salvarPreferencias()">
                        <span>Filtro Alinhamento EMA 24 e EMA 32</span>
                    </label>
                    <span class="badge-ema badge-sim">Exceto Est. 5</span>
                </div>
                <div style="font-size: 12px; color: #93c5fd; margin-top: 6px;">
                    Exige EMA 24 &gt; EMA 32 para CALL e EMA 24 &lt; EMA 32 para PUT. Não se aplica à Estratégia 5.
                </div>
            </div>

            <!-- ESTRATÉGIAS DE CONJUNTO DE CANDLES -->
            <div class="panel-box">
                <div class="secao-header">
                    <strong>Estratégias de Conjunto de Candles</strong>
                </div>

                <div class="est-card">
                    <div class="est-top">
                        <label class="est-check">
                            <input type="checkbox" id="est1_ativo" checked onchange="salvarPreferencias()">
                            <span>ESTRATEGIA 1 - CLASSIFICADOR 5 CANDLES</span>
                        </label>
                        <span class="badge-ema badge-sim">Filtro EMA 24/32</span>
                    </div>
                </div>

                <div class="est-card">
                    <div class="est-top">
                        <label class="est-check">
                            <input type="checkbox" id="est2_ativo" checked onchange="salvarPreferencias()">
                            <span>ESTRATEGIA 2 - PADRAO DE REVERSAO</span>
                        </label>
                        <span class="badge-ema badge-sim">Filtro EMA 24/32</span>
                    </div>
                    <div class="est-sub">
                        <span>Mínimo de Candles Anteriores (2 a 8):</span>
                        <input type="number" id="reversao_minimo" min="2" max="8" step="1" value="3" onchange="salvarPreferencias()">
                    </div>
                </div>

                <div class="est-card">
                    <div class="est-top">
                        <label class="est-check">
                            <input type="checkbox" id="est5_ativo" checked onchange="salvarPreferencias()">
                            <span>ESTRATEGIA 5 - MAIOR MENOR MENOR</span>
                        </label>
                        <span class="badge-ema badge-nao">Sem Filtro EMA</span>
                    </div>
                </div>
            </div>

            <!-- PADRÕES DE CANDLE INDIVIDUAIS -->
            <div class="panel-box">
                <div class="secao-header">
                    <strong>Padrões de Candle (<span id="qtd-padroes">15</span> ativos)</strong>
                    <div class="acoes-rapidas">
                        <button type="button" class="btn-mini" onclick="marcarTodos(true)">Marcar todos</button>
                        <button type="button" class="btn-mini" onclick="marcarTodos(false)">Desmarcar todos</button>
                    </div>
                </div>
                <div class="padroes-grid" id="lista-padroes"></div>
            </div>
        </div>

        <div class="col-right">
            <div class="panel-box">
                <button type="button" class="btn-main btn-start" id="btn-start" onclick="iniciar()">INICIAR ROBÔ</button>
                <button type="button" class="btn-main btn-stop" id="btn-stop" onclick="parar()">PARAR ROBÔ</button>

                <div class="status-box" style="margin-top: 12px;">
                    <span>Saldo: <span id="saldo">--</span></span>
                    <span>Lucro: <span id="lucro">0.00</span></span>
                </div>

                <div class="resumo-lista" style="margin-top: 10px;">
                    <div class="resumo-item">
                        Padrões Ativos
                        <strong id="resumo-padroes">15 / 15</strong>
                    </div>
                    <div class="resumo-item">
                        Conjuntos Ativos
                        <strong id="resumo-conjuntos">3 / 3</strong>
                    </div>
                    <div class="resumo-item">
                        Filtro EMA 24/32
                        <strong id="resumo-ema">ATIVADO</strong>
                    </div>
                    <div class="resumo-item">
                        Mín. Reversão (Est. 2)
                        <strong id="resumo-minrev">3 velas</strong>
                    </div>
                </div>
            </div>

            <div class="panel-box">
                <div class="secao-header">
                    <strong>Logs do Sistema</strong>
                </div>
                <div class="logs" id="logs">Aguardando início...</div>
            </div>
        </div>
    </div>
</div>

<script>
    const PADROES_INFO = [
        ["engulfing_bull", "Engolfo de Alta (CALL)"],
        ["engulfing_bear", "Engolfo de Baixa (PUT)"],
        ["hammer", "Martelo (CALL)"],
        ["shooting_star", "Estrela Cadente (PUT)"],
        ["morning_star", "Estrela da Manhã (CALL)"],
        ["evening_star", "Estrela da Noite (PUT)"],
        ["three_soldiers", "Três Soldados (CALL)"],
        ["three_crows", "Três Corvos (PUT)"],
        ["double_bottom", "Fundo Duplo (CALL)"],
        ["double_top", "Topo Duplo (PUT)"],
        ["doji_bull", "Doji de Alta (CALL)"],
        ["doji_bear", "Doji de Baixa (PUT)"],
        ["marubozu_bull", "Marubozu de Alta (CALL)"],
        ["marubozu_bear", "Marubozu de Baixa (PUT)"],
        ["tweezer_bottom", "Pinça de Fundo (CALL)"]
    ];

    let sessaoId = localStorage.getItem("sessao_id") || null;
    let intervalo = null;

    function renderizarPadroes() {
        const container = document.getElementById("lista-padroes");
        const salvos = JSON.parse(localStorage.getItem("padroes_ativos") || "null");

        container.innerHTML = PADROES_INFO.map(([id, nome]) => {
            const checked = salvos ? salvos.includes(id) : true;
            return `
                <label class="padrao-item">
                    <input type="checkbox" class="chk-padrao" value="${id}" ${checked ? "checked" : ""} onchange="salvarPreferencias()">
                    <span>${nome}</span>
                </label>
            `;
        }).join("");
        carregarEstrategiasSalvas();
        atualizarContagem();
    }

    function carregarEstrategiasSalvas() {
        const cfgSalva = JSON.parse(localStorage.getItem("estrategias_cfg") || "null");
        if (!cfgSalva) return;
        if (typeof cfgSalva.usar_ema_filtro === "boolean") {
            document.getElementById("usar_ema_filtro").checked = cfgSalva.usar_ema_filtro;
        }
        if (typeof cfgSalva.est1_ativo === "boolean") {
            document.getElementById("est1_ativo").checked = cfgSalva.est1_ativo;
        }
        if (typeof cfgSalva.est2_ativo === "boolean") {
            document.getElementById("est2_ativo").checked = cfgSalva.est2_ativo;
        }
        if (typeof cfgSalva.est5_ativo === "boolean") {
            document.getElementById("est5_ativo").checked = cfgSalva.est5_ativo;
        }
        if (cfgSalva.reversao_minimo) {
            document.getElementById("reversao_minimo").value = cfgSalva.reversao_minimo;
        }
    }

    function obterPadroesSelecionados() {
        return Array.from(document.querySelectorAll(".chk-padrao:checked")).map(el => el.value);
    }

    function salvarPreferencias() {
        const selecionados = obterPadroesSelecionados();
        localStorage.setItem("padroes_ativos", JSON.stringify(selecionados));

        let minRev = parseInt(document.getElementById("reversao_minimo").value || "3", 10);
        if (minRev < 2) minRev = 2;
        if (minRev > 8) minRev = 8;
        document.getElementById("reversao_minimo").value = minRev;

        localStorage.setItem("estrategias_cfg", JSON.stringify({
            usar_ema_filtro: document.getElementById("usar_ema_filtro").checked,
            est1_ativo: document.getElementById("est1_ativo").checked,
            est2_ativo: document.getElementById("est2_ativo").checked,
            est5_ativo: document.getElementById("est5_ativo").checked,
            reversao_minimo: minRev
        }));

        atualizarContagem();
    }

    function atualizarContagem() {
        const qtdPadroes = obterPadroesSelecionados().length;
        document.getElementById("qtd-padroes").innerText = qtdPadroes;
        document.getElementById("resumo-padroes").innerText = `${qtdPadroes} / ${PADROES_INFO.length}`;

        const conjuntosAtivos = [
            document.getElementById("est1_ativo").checked,
            document.getElementById("est2_ativo").checked,
            document.getElementById("est5_ativo").checked
        ].filter(Boolean).length;
        document.getElementById("resumo-conjuntos").innerText = `${conjuntosAtivos} / 3`;

        const emaOn = document.getElementById("usar_ema_filtro").checked;
        document.getElementById("resumo-ema").innerText = emaOn ? "ATIVADO" : "DESATIVADO";

        const minRev = document.getElementById("reversao_minimo").value || "3";
        document.getElementById("resumo-minrev").innerText = `${minRev} velas`;
    }

    function marcarTodos(marcar) {
        document.querySelectorAll(".chk-padrao").forEach(el => {
            el.checked = marcar;
        });
        salvarPreferencias();
    }

    async function iniciar() {
        salvarPreferencias();
        const padroes = obterPadroesSelecionados();
        const est1 = document.getElementById("est1_ativo").checked;
        const est2 = document.getElementById("est2_ativo").checked;
        const est5 = document.getElementById("est5_ativo").checked;

        const payload = {
            email: document.getElementById("email").value,
            senha: document.getElementById("senha").value,
            conta: document.getElementById("conta").value,
            ativo: document.getElementById("ativo").value.toUpperCase(),
            valor: document.getElementById("valor").value,
            timeframe: document.getElementById("timeframe").value,
            expiracao_velas: 1,
            stop_win: document.getElementById("stop_win").value,
            stop_loss: document.getElementById("stop_loss").value,
            gale: document.getElementById("gale").value === "true",
            gale_max: document.getElementById("gale_max").value,
            gale_mult: document.getElementById("gale_mult").value,
            usar_ema_filtro: document.getElementById("usar_ema_filtro").checked,
            est1_ativo: est1,
            est2_ativo: est2,
            est5_ativo: est5,
            reversao_minimo: document.getElementById("reversao_minimo").value,
            padroes: padroes
        };

        if (!payload.email || !payload.senha) {
            alert("Preencha e-mail e senha!");
            return;
        }
        if (padroes.length === 0 && !est1 && !est2 && !est5) {
            alert("Ative pelo menos um padrão ou estratégia de conjunto de candles!");
            return;
        }

        const res = await fetch("/iniciar", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        sessaoId = data.sessao_id;
        localStorage.setItem("sessao_id", sessaoId);

        alternarBotoes(true);
        iniciarMonitoramento();
    }

    async function parar() {
        if (!sessaoId) return;
        await fetch("/parar", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ sessao_id: sessaoId })
        });
        alternarBotoes(false);
    }

    function alternarBotoes(rodando) {
        document.getElementById("btn-start").style.display = rodando ? "none" : "block";
        document.getElementById("btn-stop").style.display = rodando ? "block" : "none";
    }

    function iniciarMonitoramento() {
        if (intervalo) clearInterval(intervalo);
        intervalo = setInterval(async () => {
            if (!sessaoId) return;
            const res = await fetch(`/status?sessao_id=${sessaoId}`);
            if (!res.ok) return;
            const data = await res.json();

            document.getElementById("saldo").innerText = data.saldo;
            document.getElementById("lucro").innerText = data.lucro;

            const logDiv = document.getElementById("logs");
            logDiv.innerHTML = data.logs.join("<br>");
            logDiv.scrollTop = logDiv.scrollHeight;

            if (!data.rodando) {
                alternarBotoes(false);
                clearInterval(intervalo);
            }
        }, 2000);
    }

    renderizarPadroes();
    if (sessaoId) {
        iniciarMonitoramento();
    }
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_PAGE)

@app.route("/iniciar", methods=["POST"])
def iniciar():
    cfg = request.json
    sessao_id = str(uuid.uuid4())
    SESSOES[sessao_id] = {
        "rodando": True,
        "saldo": "--",
        "lucro": 0.0,
        "logs": []
    }
    t = threading.Thread(target=rodar_robo, args=(sessao_id, cfg), daemon=True)
    t.start()
    return jsonify({"sessao_id": sessao_id})

@app.route("/parar", methods=["POST"])
def parar():
    sessao_id = request.json.get("sessao_id")
    if sessao_id in SESSOES:
        SESSOES[sessao_id]["rodando"] = False
    return jsonify({"ok": True})

@app.route("/status")
def status():
    sessao_id = request.args.get("sessao_id")
    if sessao_id not in SESSOES:
        return jsonify({"erro": "Sessão não encontrada"}), 404
    return jsonify(SESSOES[sessao_id])


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
