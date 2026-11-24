# -*- coding: utf-8 -*-
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
    "engulfing_bull": "call", "engulfing_bear": "put",
    "hammer": "call", "shooting_star": "put",
    "morning_star": "call", "evening_star": "put",
    "three_soldiers": "call", "three_crows": "put",
    "double_bottom": "call", "double_top": "put",
}

# ---------------- LÓGICA DE PADRÕES ----------------
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

DETECTORES = {
    "engulfing_bull": engulfing_bull, "engulfing_bear": engulfing_bear,
    "hammer": hammer, "shooting_star": shooting_star,
    "morning_star": morning_star, "evening_star": evening_star,
    "three_soldiers": three_soldiers, "three_crows": three_crows,
    "double_bottom": double_bottom, "double_top": double_top,
}

def detectar(cs, padroes):
    achados = [p for p in padroes if DETECTORES[p](cs)]
    direcoes = {DIRECAO[p] for p in achados}
    if len(direcoes) != 1:
        return None, achados
    return direcoes.pop(), achados

def converter(v):
    return {"time": v["from"], "open": v["open"], "close": v["close"], "high": v["max"], "low": v["min"]}

# ---------------- EXECUÇÃO DO ROBÔ EM THREAD ----------------
def log_msg(sessao_id, texto):
    if sessao_id in SESSOES:
        hora = time.strftime("%H:%M:%S")
        SESSOES[sessao_id]["logs"].append(f"[{hora}] {texto}")
        # Mantém apenas as últimas 50 linhas
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

    timeframe = int(cfg["timeframe"])
    expiracao_min = max(1, (timeframe // 60) * int(cfg["expiracao_velas"]))
    padroes = list(DETECTORES.keys())

    while sessao["rodando"]:
        agora = iq.get_server_timestamp()
        espera = timeframe - (agora % timeframe) + 1
        
        # Espera fracionada para permitir parar o robô rapidamente
        for _ in range(int(espera)):
            if not sessao["rodando"]:
                break
            time.sleep(1)
            
        if not sessao["rodando"]:
            break

        if not iq.check_connect():
            iq.connect()

        velas = [converter(v) for v in iq.get_candles(cfg["ativo"], timeframe, 60, time.time())]
        fechadas = velas[:-1]
        if len(fechadas) < 10:
            continue

        direcao, achados = detectar(fechadas, padroes)
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
    <title>PadrãoBot - IQ Option Web</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #121212; color: #e0e0e0; margin: 0; padding: 15px; }
        .container { max-width: 500px; margin: auto; background: #1e1e1e; padding: 20px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
        h2 { text-align: center; color: #00e676; margin-top: 0; }
        label { display: block; margin-top: 12px; font-size: 14px; color: #aaa; }
        input, select { width: 100%; padding: 10px; margin-top: 5px; border-radius: 6px; border: 1px solid #333; background: #2c2c2c; color: #fff; box-sizing: border-box; font-size: 16px; }
        .row { display: flex; gap: 10px; }
        .row > div { flex: 1; }
        button { width: 100%; padding: 14px; margin-top: 20px; border: none; border-radius: 8px; font-size: 16px; font-weight: bold; cursor: pointer; }
        .btn-start { background: #00c853; color: #000; }
        .btn-stop { background: #ff5252; color: #fff; display: none; }
        .status-box { display: flex; justify-content: space-between; background: #252525; padding: 12px; border-radius: 8px; margin-top: 15px; font-weight: bold; }
        .logs { background: #000; color: #00e676; font-family: monospace; font-size: 12px; height: 220px; overflow-y: auto; padding: 10px; border-radius: 8px; margin-top: 15px; border: 1px solid #333; }
    </style>
</head>
<body>
<div class="container">
    <h2>PadrãoBot Web</h2>
    
    <div id="form-config">
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
                <div class="row">
                    <input type="number" id="gale_max" value="1">
                    <input type="number" id="gale_mult" value="2.2" step="0.1">
                </div>
            </div>
        </div>
    </div>

    <button class="btn-start" id="btn-start" onclick="iniciar()">INICIAR ROBÔ</button>
    <button class="btn-stop" id="btn-stop" onclick="parar()">PARAR ROBÔ</button>

    <div class="status-box">
        <span>Saldo: <span id="saldo">--</span></span>
        <span>Lucro: <span id="lucro">0.00</span></span>
    </div>

    <div class="logs" id="logs">Aguardando início...</div>
</div>

<script>
    let sessaoId = localStorage.getItem("sessao_id") || null;
    let intervalo = null;

    async function iniciar() {
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
            gale_mult: document.getElementById("gale_mult").value
        };

        if (!payload.email || !payload.senha) {
            alert("Preencha e-mail e senha!");
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