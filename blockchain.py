#!/usr/bin/env python3
"""
blockchain.py — Blockchain Local ERC-20 com suporte MetaMask
Armazena dados em SQLite (banco light, nativo do Python)
"""

import os
import json
import time
import hashlib
import secrets
import threading
import sqlite3
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from ecdsa import SigningKey, VerifyingKey, SECP256k1

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
DB_PATH     = os.path.join(BASE_DIR, "chaindata.db")

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# ─── SQLite helpers (key-value store, thread-safe) ────────────────────────────
_local = threading.local()

def get_db():
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.execute(
            "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)"
        )
        _local.conn.commit()
    return _local.conn

def db_get(key: str):
    row = get_db().execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None

def db_put(key: str, value):
    get_db().execute(
        "INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)",
        (key, json.dumps(value))
    )
    get_db().commit()

def db_delete(key: str):
    get_db().execute("DELETE FROM kv WHERE key=?", (key,))
    get_db().commit()

def db_prefix(prefix: str):
    rows = get_db().execute(
        "SELECT key, value FROM kv WHERE key LIKE ?", (prefix + "%",)
    ).fetchall()
    return {r[0]: json.loads(r[1]) for r in rows}

# ─── Config ───────────────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "name":        "MyCoin",
    "symbol":      "MYC",
    "decimals":    18,
    "total_supply":"1000000",
    "network_fee": False,
    "fee_amount":  "0",
    "chain_id":    1337,
    "port":        8545
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

# ─── Crypto helpers ───────────────────────────────────────────────────────────
def keccak256(data: bytes) -> bytes:
    from hashlib import sha3_256
    # Use sha3_256 as substitute (true keccak requires extra lib)
    # For real ETH compat: pip install pysha3
    try:
        import sha3
        k = hashlib.new('sha3_256')
    except Exception:
        pass
    # Use sha3_256 from hashlib (Python 3.6+)
    return hashlib.sha3_256(data).digest()

def private_key_to_address(priv_hex: str) -> str:
    sk = SigningKey.from_string(bytes.fromhex(priv_hex), curve=SECP256k1)
    vk = sk.get_verifying_key()
    pub = vk.to_string()          # 64 bytes (x+y, no 04 prefix)
    addr_bytes = keccak256(pub)[-20:]
    return "0x" + addr_bytes.hex()

def generate_wallet():
    priv = secrets.token_bytes(32)
    priv_hex = priv.hex()
    address = private_key_to_address(priv_hex)
    return {"address": address, "private_key": priv_hex}

# ─── Block structure ──────────────────────────────────────────────────────────
GENESIS_ADDRESS = "0x0000000000000000000000000000000000000000"

def make_block(index, transactions, prev_hash, miner, difficulty=4):
    nonce = 0
    timestamp = int(time.time())
    while True:
        header = json.dumps({
            "index": index, "timestamp": timestamp,
            "transactions": transactions, "prev_hash": prev_hash,
            "miner": miner, "nonce": nonce
        }, sort_keys=True)
        block_hash = hashlib.sha256(header.encode()).hexdigest()
        if block_hash.startswith("0" * difficulty):
            break
        nonce += 1
    return {
        "index": index, "timestamp": timestamp,
        "transactions": transactions, "prev_hash": prev_hash,
        "miner": miner, "nonce": nonce, "hash": block_hash
    }

def get_chain_height() -> int:
    v = db_get("meta:height")
    return v if v is not None else -1

def get_block(index: int):
    return db_get(f"block:{index}")

def append_block(block):
    idx = block["index"]
    db_put(f"block:{idx}", block)
    db_put("meta:height", idx)

# ─── Balance / ERC-20 state ───────────────────────────────────────────────────
def get_balance(address: str) -> int:
    v = db_get(f"balance:{address.lower()}")
    return int(v) if v is not None else 0

def set_balance(address: str, amount: int):
    db_put(f"balance:{address.lower()}", amount)

def apply_transactions(txs, cfg):
    fee_int = int(float(cfg.get("fee_amount","0")) * (10 ** int(cfg["decimals"]))) if cfg.get("network_fee") else 0
    miner_fees = 0
    for tx in txs:
        if tx.get("type") == "coinbase":
            bal = get_balance(tx["to"])
            set_balance(tx["to"], bal + int(tx["amount"]))
        else:
            sender = tx["from"].lower()
            receiver= tx["to"].lower()
            amount  = int(tx["amount"])
            total   = amount + fee_int
            bal_s   = get_balance(sender)
            if bal_s >= total:
                set_balance(sender,   bal_s - total)
                set_balance(receiver, get_balance(receiver) + amount)
                miner_fees += fee_int
    return miner_fees

# ─── Pending tx pool ──────────────────────────────────────────────────────────
_pending_lock = threading.Lock()
_pending: list = []

def add_pending(tx):
    with _pending_lock:
        _pending.append(tx)

def flush_pending():
    with _pending_lock:
        txs = list(_pending)
        _pending.clear()
    return txs

# ─── Genesis block ────────────────────────────────────────────────────────────
def init_chain(cfg):
    if get_chain_height() >= 0:
        return
    supply = int(float(cfg["total_supply"]) * (10 ** int(cfg["decimals"])))
    genesis_tx = {"type":"coinbase","to": GENESIS_ADDRESS,"amount": supply,"hash":"0x0"}
    genesis = make_block(0, [genesis_tx], "0"*64, GENESIS_ADDRESS, difficulty=1)
    append_block(genesis)
    set_balance(GENESIS_ADDRESS, supply)
    db_put("meta:config", cfg)
    print(f"[GENESIS] Bloco 0 criado. Supply: {cfg['total_supply']} {cfg['symbol']}")

# ─── JSON-RPC 2.0 (MetaMask compat) ──────────────────────────────────────────
def rpc_dispatch(method, params, cfg):
    decimals = int(cfg["decimals"])
    chain_id  = cfg.get("chain_id", 1337)

    if method == "eth_chainId":
        return hex(chain_id)

    elif method == "net_version":
        return str(chain_id)

    elif method == "eth_blockNumber":
        return hex(max(get_chain_height(), 0))

    elif method == "eth_getBalance":
        addr = params[0].lower()
        bal  = get_balance(addr)
        return hex(bal)

    elif method == "eth_accounts":
        wallets = db_prefix("wallet:")
        return [v["address"] for v in wallets.values()]

    elif method == "eth_sendTransaction":
        tx_data = params[0]
        value   = int(tx_data.get("value","0x0"), 16)
        tx = {
            "type":   "transfer",
            "from":   tx_data.get("from",""),
            "to":     tx_data.get("to",""),
            "amount": value,
            "hash":   "0x" + secrets.token_hex(32),
            "timestamp": int(time.time())
        }
        add_pending(tx)
        # Persist tx to history
        db_put(f"tx:{tx['hash']}", tx)
        return tx["hash"]

    elif method == "eth_getTransactionByHash":
        h = params[0]
        return db_get(f"tx:{h}")

    elif method == "eth_getTransactionCount":
        return hex(0)

    elif method == "eth_gasPrice":
        if cfg.get("network_fee") and float(cfg.get("fee_amount","0")) > 0:
            gwei = int(float(cfg["fee_amount"]) * 1e9)
            return hex(gwei)
        return hex(0)

    elif method == "eth_estimateGas":
        return hex(21000)

    elif method == "eth_getBlockByNumber":
        num_str = params[0]
        if num_str in ("latest","pending"):
            idx = get_chain_height()
        else:
            idx = int(num_str, 16)
        blk = get_block(idx)
        if not blk:
            return None
        return {
            "number":     hex(blk["index"]),
            "hash":       "0x" + blk["hash"],
            "parentHash": "0x" + blk["prev_hash"],
            "timestamp":  hex(blk["timestamp"]),
            "transactions": [t.get("hash","0x0") for t in blk.get("transactions",[])],
            "miner":      blk["miner"],
            "difficulty": hex(4),
            "gasLimit":   hex(8000000),
            "gasUsed":    hex(0),
        }

    elif method == "eth_call":
        # ERC-20: totalSupply, balanceOf, name, symbol, decimals
        tx_data = params[0]
        data    = tx_data.get("data","")
        sel     = data[:10].lower()

        def pad32(val_hex):
            return val_hex.zfill(64)

        # totalSupply()  0x18160ddd
        if sel == "0x18160ddd":
            cfg_s = db_get("meta:config") or cfg
            supply = int(float(cfg_s["total_supply"]) * (10 ** int(cfg_s["decimals"])))
            return "0x" + pad32(hex(supply)[2:])

        # balanceOf(address)  0x70a08231
        elif sel == "0x70a08231":
            raw_addr = data[10+24:10+64]  # last 20 bytes of 32-byte param
            addr = "0x" + raw_addr
            bal  = get_balance(addr)
            return "0x" + pad32(hex(bal)[2:])

        # name()  0x06fdde03
        elif sel == "0x06fdde03":
            s = cfg.get("name","Token")
            enc = s.encode()
            offset = pad32("20")
            length = pad32(hex(len(enc))[2:])
            padded = enc.hex().ljust(64, '0')
            return "0x" + offset + length + padded

        # symbol()  0x95d89b41
        elif sel == "0x95d89b41":
            s = cfg.get("symbol","TKN")
            enc = s.encode()
            offset = pad32("20")
            length = pad32(hex(len(enc))[2:])
            padded = enc.hex().ljust(64, '0')
            return "0x" + offset + length + padded

        # decimals()  0x313ce567
        elif sel == "0x313ce567":
            d = int(cfg.get("decimals", 18))
            return "0x" + pad32(hex(d)[2:])

        return "0x"

    elif method == "web3_clientVersion":
        return f"LocalChain/{cfg.get('name','Token')}/v1.0"

    elif method == "eth_syncing":
        return False

    elif method == "net_listening":
        return True

    return None

# ─── Flask routes ─────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML_UI)

# JSON-RPC endpoint (MetaMask connects here)
@app.route("/", methods=["POST"])
@app.route("/rpc", methods=["POST","OPTIONS"])
def rpc():
    if request.method == "OPTIONS":
        return "", 204
    cfg = load_config()
    body = request.get_json(force=True, silent=True) or {}

    # Batch support
    if isinstance(body, list):
        return jsonify([_handle_rpc(r, cfg) for r in body])
    return jsonify(_handle_rpc(body, cfg))

def _handle_rpc(body, cfg):
    method  = body.get("method","")
    params  = body.get("params",[])
    req_id  = body.get("id",1)
    try:
        result = rpc_dispatch(method, params, cfg)
        return {"jsonrpc":"2.0","id":req_id,"result":result}
    except Exception as e:
        return {"jsonrpc":"2.0","id":req_id,"error":{"code":-32603,"message":str(e)}}

# ─── REST API (used by UI) ────────────────────────────────────────────────────

@app.route("/api/config", methods=["GET"])
def api_get_config():
    return jsonify(load_config())

@app.route("/api/config", methods=["POST"])
def api_set_config():
    data = request.get_json()
    cfg  = load_config()
    cfg.update(data)
    save_config(cfg)
    return jsonify({"ok": True, "config": cfg})

@app.route("/api/wallets", methods=["GET"])
def api_wallets():
    raw = db_prefix("wallet:")
    cfg = load_config()
    dec = int(cfg["decimals"])
    result = []
    for key, w in raw.items():
        bal = get_balance(w["address"])
        result.append({
            "address":     w["address"],
            "balance_raw": bal,
            "balance":     bal / (10**dec),
            "symbol":      cfg["symbol"]
        })
    return jsonify(result)

@app.route("/api/wallets/create", methods=["POST"])
def api_create_wallet():
    w = generate_wallet()
    db_put(f"wallet:{w['address']}", w)
    cfg = load_config()
    return jsonify({
        "address":     w["address"],
        "private_key": w["private_key"],
        "balance":     0,
        "symbol":      cfg["symbol"]
    })

@app.route("/api/wallets/import", methods=["POST"])
def api_import_wallet():
    data = request.get_json()
    priv = data.get("private_key","").strip()
    if len(priv) != 64:
        return jsonify({"error":"Chave privada inválida (64 hex chars)"}), 400
    try:
        addr = private_key_to_address(priv)
        w = {"address": addr, "private_key": priv}
        db_put(f"wallet:{addr}", w)
        cfg = load_config()
        bal = get_balance(addr)
        dec = int(cfg["decimals"])
        return jsonify({"address": addr, "balance": bal/(10**dec), "symbol": cfg["symbol"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/transfer", methods=["POST"])
def api_transfer():
    data   = request.get_json()
    cfg    = load_config()
    dec    = int(cfg["decimals"])
    sender = data.get("from","").lower()
    to     = data.get("to","").lower()
    amount = float(data.get("amount", 0))
    amount_raw = int(amount * (10**dec))

    # Fee
    fee_raw = 0
    if cfg.get("network_fee"):
        fee_raw = int(float(cfg.get("fee_amount","0")) * (10**dec))

    bal = get_balance(sender)
    if bal < amount_raw + fee_raw:
        return jsonify({"error":"Saldo insuficiente"}), 400

    tx = {
        "type":"transfer","from":sender,"to":to,
        "amount": amount_raw,"fee": fee_raw,
        "hash":"0x"+secrets.token_hex(32),
        "timestamp": int(time.time())
    }
    add_pending(tx)
    db_put(f"tx:{tx['hash']}", tx)
    return jsonify({"ok":True,"tx_hash":tx["hash"]})

@app.route("/api/mine", methods=["POST"])
def api_mine():
    """Mine a block — used by minerar.py and UI"""
    data  = request.get_json()
    miner = data.get("miner","").lower()
    if not miner:
        return jsonify({"error":"Endereço do minerador obrigatório"}), 400
    cfg = load_config()
    dec = int(cfg["decimals"])

    txs = flush_pending()
    # Recompensa de mineração: 50 moedas
    reward = int(50 * (10**dec))
    coinbase_tx = {
        "type":"coinbase","to":miner,
        "amount": reward,"hash":"0x"+secrets.token_hex(32)
    }
    txs.insert(0, coinbase_tx)

    height   = get_chain_height()
    prev_blk = get_block(height)
    prev_hash= prev_blk["hash"] if prev_blk else "0"*64

    block = make_block(height+1, txs, prev_hash, miner, difficulty=3)
    append_block(block)
    apply_transactions(txs, cfg)
    db_put(f"tx:{coinbase_tx['hash']}", coinbase_tx)

    new_bal = get_balance(miner)
    return jsonify({
        "ok":    True,
        "block": block["index"],
        "hash":  block["hash"],
        "reward": reward / (10**dec),
        "miner_balance": new_bal / (10**dec),
        "symbol": cfg["symbol"]
    })

@app.route("/api/chain", methods=["GET"])
def api_chain():
    height = get_chain_height()
    blocks = []
    for i in range(max(0, height-9), height+1):
        b = get_block(i)
        if b:
            blocks.append(b)
    return jsonify({"height": height, "blocks": blocks[::-1]})

@app.route("/api/tx/<tx_hash>", methods=["GET"])
def api_tx(tx_hash):
    tx = db_get(f"tx:{tx_hash}")
    return jsonify(tx or {"error":"não encontrada"})

@app.route("/api/status", methods=["GET"])
def api_status():
    cfg = load_config()
    return jsonify({
        "name":        cfg["name"],
        "symbol":      cfg["symbol"],
        "decimals":    cfg["decimals"],
        "chain_id":    cfg.get("chain_id",1337),
        "network_fee": cfg.get("network_fee",False),
        "fee_amount":  cfg.get("fee_amount","0"),
        "height":      get_chain_height(),
        "rpc_url":     f"http://localhost:{cfg.get('port',8545)}"
    })

# ─── HTML UI ──────────────────────────────────────────────────────────────────
HTML_UI = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LocalChain — Painel</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:ital,wght@0,400;0,700;1,400&family=Orbitron:wght@400;700;900&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#050810;--surface:#0b1120;--card:#0f1a30;--border:#1a3055;
  --accent:#00e5ff;--accent2:#7c3aed;--green:#00ff88;--red:#ff3366;
  --yellow:#ffd700;--text:#ccd6f6;--muted:#4a6080;
  --font-mono:'Space Mono',monospace;--font-display:'Orbitron',monospace;
}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:var(--font-mono);font-size:13px;min-height:100vh;overflow-x:hidden}
body::before{content:'';position:fixed;inset:0;background:radial-gradient(ellipse at 20% 0%,#00e5ff08 0%,transparent 60%),radial-gradient(ellipse at 80% 100%,#7c3aed0a 0%,transparent 60%);pointer-events:none;z-index:0}

/* Header */
header{position:relative;z-index:10;padding:20px 32px;display:flex;align-items:center;gap:16px;border-bottom:1px solid var(--border);background:rgba(5,8,16,.9);backdrop-filter:blur(12px)}
.logo{font-family:var(--font-display);font-size:22px;font-weight:900;letter-spacing:2px;color:var(--accent);text-shadow:0 0 20px var(--accent)}
.logo span{color:var(--accent2)}
.chain-badge{margin-left:auto;display:flex;gap:12px;align-items:center}
.badge{padding:4px 12px;border-radius:20px;font-size:11px;font-weight:700;letter-spacing:1px;border:1px solid}
.badge-green{color:var(--green);border-color:var(--green);background:rgba(0,255,136,.08)}
.badge-cyan{color:var(--accent);border-color:var(--accent);background:rgba(0,229,255,.08)}
#coin-name-display{font-family:var(--font-display);font-size:14px;color:var(--yellow)}

/* Layout */
main{position:relative;z-index:1;padding:24px 32px;display:grid;grid-template-columns:340px 1fr;gap:20px;max-width:1400px;margin:0 auto}
@media(max-width:900px){main{grid-template-columns:1fr}}

/* Sidebar */
.sidebar{display:flex;flex-direction:column;gap:16px}

/* Card */
.card{background:var(--card);border:1px solid var(--border);border-radius:8px;overflow:hidden}
.card-head{padding:14px 18px;border-bottom:1px solid var(--border);font-family:var(--font-display);font-size:11px;letter-spacing:2px;color:var(--accent);display:flex;align-items:center;gap:8px}
.card-head::before{content:'▶';font-size:8px;color:var(--accent2)}
.card-body{padding:18px}

/* Form */
.field{margin-bottom:14px}
.field label{display:block;font-size:11px;color:var(--muted);letter-spacing:1px;margin-bottom:6px;text-transform:uppercase}
.field input,.field select{width:100%;background:#080e1c;border:1px solid var(--border);color:var(--text);padding:9px 12px;border-radius:4px;font-family:var(--font-mono);font-size:13px;outline:none;transition:border-color .2s}
.field input:focus,.field select:focus{border-color:var(--accent)}
.field select option{background:#080e1c}
.toggle-row{display:flex;align-items:center;justify-content:space-between;padding:8px 0}
.toggle-row span{font-size:12px;color:var(--text)}
.toggle{position:relative;width:44px;height:24px}
.toggle input{opacity:0;width:0;height:0}
.slider{position:absolute;inset:0;background:#1a3055;border-radius:24px;cursor:pointer;transition:.3s}
.slider:before{content:'';position:absolute;height:18px;width:18px;left:3px;bottom:3px;background:var(--muted);border-radius:50%;transition:.3s}
input:checked+.slider{background:var(--accent2)}
input:checked+.slider:before{transform:translateX(20px);background:#fff}

/* Buttons */
.btn{display:inline-flex;align-items:center;gap:8px;padding:10px 18px;border:none;border-radius:4px;font-family:var(--font-mono);font-size:12px;font-weight:700;letter-spacing:1px;cursor:pointer;transition:.2s;text-transform:uppercase}
.btn-primary{background:linear-gradient(135deg,var(--accent2),#4f46e5);color:#fff}
.btn-primary:hover{filter:brightness(1.15);transform:translateY(-1px)}
.btn-success{background:linear-gradient(135deg,#047857,var(--green));color:#000}
.btn-success:hover{filter:brightness(1.1)}
.btn-danger{background:linear-gradient(135deg,#7f1d1d,var(--red));color:#fff}
.btn-sm{padding:7px 12px;font-size:11px}
.btn-block{width:100%;justify-content:center}
.btn-outline{background:transparent;border:1px solid var(--border);color:var(--muted)}
.btn-outline:hover{border-color:var(--accent);color:var(--accent)}

/* Wallets table */
.wallet-list{display:flex;flex-direction:column;gap:8px;max-height:380px;overflow-y:auto}
.wallet-item{background:#080e1c;border:1px solid var(--border);border-radius:6px;padding:12px;display:flex;align-items:center;gap:10px;transition:border-color .2s}
.wallet-item:hover{border-color:var(--accent2)}
.wallet-addr{font-size:11px;color:var(--accent);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.wallet-bal{font-size:13px;font-weight:700;color:var(--green);white-space:nowrap}
.wallet-sym{font-size:10px;color:var(--muted);margin-left:4px}

/* Main panel */
.main-panel{display:flex;flex-direction:column;gap:16px}

/* Stats row */
.stats-row{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
@media(max-width:1100px){.stats-row{grid-template-columns:repeat(2,1fr)}}
.stat-card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px;text-align:center}
.stat-val{font-family:var(--font-display);font-size:20px;font-weight:700;color:var(--accent)}
.stat-val.green{color:var(--green)}
.stat-val.purple{color:var(--accent2)}
.stat-val.yellow{color:var(--yellow)}
.stat-label{font-size:10px;color:var(--muted);letter-spacing:1px;margin-top:4px;text-transform:uppercase}

/* Tabs */
.tabs{display:flex;border-bottom:1px solid var(--border);gap:2px}
.tab{padding:10px 18px;font-family:var(--font-display);font-size:10px;letter-spacing:1.5px;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;transition:.2s;text-transform:uppercase}
.tab.active{color:var(--accent);border-color:var(--accent)}

/* Table */
.table-wrap{overflow-x:auto;max-height:320px;overflow-y:auto}
table{width:100%;border-collapse:collapse}
th{font-size:10px;color:var(--muted);letter-spacing:1px;text-align:left;padding:8px 12px;border-bottom:1px solid var(--border);text-transform:uppercase;position:sticky;top:0;background:var(--card)}
td{padding:9px 12px;border-bottom:1px solid #0d1d33;font-size:12px;vertical-align:middle}
tr:hover td{background:#0a1525}
.hash-cell{color:var(--accent);font-size:10px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.addr-cell{color:var(--text);font-size:10px;max-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.type-coinbase{color:var(--yellow);font-size:10px;letter-spacing:1px}
.type-transfer{color:var(--accent);font-size:10px;letter-spacing:1px}

/* Transfer form */
.transfer-grid{display:grid;gap:12px}

/* MetaMask section */
.mm-steps{display:flex;flex-direction:column;gap:10px;margin-top:8px}
.mm-step{display:flex;gap:12px;align-items:flex-start}
.mm-num{width:24px;height:24px;border-radius:50%;background:var(--accent2);color:#fff;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;flex-shrink:0;margin-top:1px}
.mm-text{font-size:12px;color:var(--text);line-height:1.6}
.mm-text code{background:#0a1525;color:var(--accent);padding:2px 6px;border-radius:3px;font-family:var(--font-mono)}
.rpc-box{background:#080e1c;border:1px solid var(--accent);border-radius:4px;padding:12px;margin:8px 0;font-size:12px;line-height:2}
.rpc-row{display:flex;justify-content:space-between;align-items:center}
.rpc-key{color:var(--muted)}
.rpc-val{color:var(--accent);font-weight:700}

/* Notification */
#notif{position:fixed;top:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px}
.notif-item{background:var(--card);border:1px solid var(--border);border-radius:6px;padding:12px 18px;font-size:12px;max-width:320px;animation:slideIn .3s ease;box-shadow:0 4px 20px rgba(0,0,0,.5)}
.notif-item.ok{border-color:var(--green);color:var(--green)}
.notif-item.err{border-color:var(--red);color:var(--red)}
@keyframes slideIn{from{transform:translateX(40px);opacity:0}to{transform:translateX(0);opacity:1}}

/* Scrollbar */
::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-track{background:var(--surface)}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
::-webkit-scrollbar-thumb:hover{background:var(--muted)}

/* Loading */
.spin{animation:spin 1s linear infinite;display:inline-block}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>

<div id="notif"></div>

<header>
  <div class="logo">LOCAL<span>CHAIN</span></div>
  <div id="coin-name-display">—</div>
  <div class="chain-badge">
    <span class="badge badge-green" id="status-dot">● ONLINE</span>
    <span class="badge badge-cyan" id="height-badge">BLOCO #0</span>
  </div>
</header>

<main>
  <!-- SIDEBAR -->
  <div class="sidebar">

    <!-- Configuração -->
    <div class="card">
      <div class="card-head">Configuração da Rede</div>
      <div class="card-body">
        <div class="field">
          <label>Nome da Moeda</label>
          <input id="cfg-name" type="text" placeholder="MyCoin">
        </div>
        <div class="field">
          <label>Símbolo</label>
          <input id="cfg-symbol" type="text" placeholder="MYC" maxlength="10">
        </div>
        <div class="field">
          <label>Decimais</label>
          <input id="cfg-decimals" type="number" value="18" min="0" max="18">
        </div>
        <div class="field">
          <label>Supply Total</label>
          <input id="cfg-supply" type="text" placeholder="1000000">
        </div>
        <div class="toggle-row">
          <span>Taxa de Rede</span>
          <label class="toggle"><input type="checkbox" id="cfg-fee-toggle"><span class="slider"></span></label>
        </div>
        <div id="fee-amount-wrap" class="field" style="display:none;margin-top:10px">
          <label>Valor da Taxa</label>
          <input id="cfg-fee-amount" type="text" placeholder="0.001">
        </div>
        <div style="margin-top:16px">
          <button class="btn btn-primary btn-block" onclick="saveConfig()">💾 SALVAR CONFIG</button>
        </div>
      </div>
    </div>

    <!-- Carteiras -->
    <div class="card">
      <div class="card-head">Carteiras</div>
      <div class="card-body">
        <div style="display:flex;gap:8px;margin-bottom:14px">
          <button class="btn btn-success btn-sm" onclick="createWallet()" style="flex:1">+ NOVA</button>
          <button class="btn btn-outline btn-sm" onclick="showImport()" style="flex:1">↑ IMPORTAR</button>
        </div>
        <div id="import-wrap" style="display:none;margin-bottom:12px">
          <div class="field"><label>Chave Privada (hex)</label>
            <input id="import-pk" type="text" placeholder="64 chars hex...">
          </div>
          <button class="btn btn-primary btn-sm btn-block" onclick="importWallet()">IMPORTAR</button>
        </div>
        <div id="wallet-list" class="wallet-list"></div>
      </div>
    </div>

  </div>

  <!-- MAIN PANEL -->
  <div class="main-panel">

    <!-- Stats -->
    <div class="stats-row">
      <div class="stat-card">
        <div class="stat-val" id="st-height">0</div>
        <div class="stat-label">Blocos Minerados</div>
      </div>
      <div class="stat-card">
        <div class="stat-val green" id="st-supply">—</div>
        <div class="stat-label">Supply Total</div>
      </div>
      <div class="stat-card">
        <div class="stat-val purple" id="st-wallets">0</div>
        <div class="stat-label">Carteiras</div>
      </div>
      <div class="stat-card">
        <div class="stat-val yellow" id="st-fee">Sem Taxa</div>
        <div class="stat-label">Taxa de Rede</div>
      </div>
    </div>

    <!-- Cards de tabs -->
    <div class="card">
      <div class="tabs">
        <div class="tab active" onclick="showTab('explorer')">⛓ Explorer</div>
        <div class="tab" onclick="showTab('transfer')">↔ Transferir</div>
        <div class="tab" onclick="showTab('mine')">⛏ Minerar</div>
        <div class="tab" onclick="showTab('metamask')">🦊 MetaMask</div>
      </div>

      <!-- Explorer -->
      <div id="tab-explorer" class="card-body">
        <div class="table-wrap">
          <table>
            <thead><tr>
              <th>#</th><th>Hash</th><th>Minerador</th><th>Txs</th><th>Hora</th>
            </tr></thead>
            <tbody id="blocks-tbody"></tbody>
          </table>
        </div>
      </div>

      <!-- Transfer -->
      <div id="tab-transfer" class="card-body" style="display:none">
        <div class="transfer-grid">
          <div class="field"><label>De (endereço)</label>
            <input id="tx-from" placeholder="0x...">
          </div>
          <div class="field"><label>Para (endereço)</label>
            <input id="tx-to" placeholder="0x...">
          </div>
          <div class="field"><label>Quantidade</label>
            <input id="tx-amount" type="number" placeholder="0.00">
          </div>
          <button class="btn btn-primary" onclick="doTransfer()">↔ ENVIAR</button>
        </div>
      </div>

      <!-- Mine -->
      <div id="tab-mine" class="card-body" style="display:none">
        <div class="field"><label>Endereço do Minerador</label>
          <input id="mine-addr" placeholder="0x...">
        </div>
        <button class="btn btn-success btn-block" onclick="mineBlock()" id="mine-btn">
          ⛏ MINERAR BLOCO
        </button>
        <div id="mine-result" style="margin-top:14px;font-size:12px;color:var(--green);display:none"></div>
      </div>

      <!-- MetaMask -->
      <div id="tab-metamask" class="card-body" style="display:none">
        <p style="color:var(--muted);font-size:12px;margin-bottom:16px">Configure sua MetaMask para conectar à rede local:</p>
        <div class="rpc-box" id="mm-info"></div>
        <div class="mm-steps">
          <div class="mm-step"><div class="mm-num">1</div><div class="mm-text">Abra a MetaMask → clique na rede no topo → <strong>Adicionar rede manualmente</strong></div></div>
          <div class="mm-step"><div class="mm-num">2</div><div class="mm-text">Preencha os campos acima</div></div>
          <div class="mm-step"><div class="mm-num">3</div><div class="mm-text">Salve e selecione a rede. Depois importe uma carteira pela chave privada.</div></div>
          <div class="mm-step"><div class="mm-num">4</div><div class="mm-text">Para ver o token ERC-20, clique em <strong>Importar tokens</strong> e insira o endereço: <code id="mm-contract">0x000...0001</code></div></div>
        </div>
      </div>

    </div>
  </div>
</main>

<script>
const API = '';
let CFG = {};

// ── helpers ─────────────────────────────────────────────────────
async function get(path){ const r=await fetch(API+path); return r.json(); }
async function post(path,body){ const r=await fetch(API+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); return r.json(); }

function notify(msg, type='ok'){
  const d=document.createElement('div');
  d.className='notif-item '+type; d.textContent=msg;
  document.getElementById('notif').appendChild(d);
  setTimeout(()=>d.remove(), 3500);
}

function showTab(name){
  ['explorer','transfer','mine','metamask'].forEach(t=>{
    document.getElementById('tab-'+t).style.display = t===name?'':'none';
  });
  document.querySelectorAll('.tab').forEach((el,i)=>{
    el.classList.toggle('active', ['explorer','transfer','mine','metamask'][i]===name);
  });
  if(name==='explorer') loadChain();
  if(name==='metamask') updateMM();
}

// ── Config ──────────────────────────────────────────────────────
async function loadConfig(){
  CFG = await get('/api/config');
  document.getElementById('cfg-name').value    = CFG.name||'';
  document.getElementById('cfg-symbol').value  = CFG.symbol||'';
  document.getElementById('cfg-decimals').value= CFG.decimals||18;
  document.getElementById('cfg-supply').value  = CFG.total_supply||'';
  document.getElementById('cfg-fee-toggle').checked = !!CFG.network_fee;
  document.getElementById('cfg-fee-amount').value = CFG.fee_amount||'0';
  document.getElementById('fee-amount-wrap').style.display = CFG.network_fee?'':'none';
  document.getElementById('coin-name-display').textContent = `${CFG.name} (${CFG.symbol})`;
  document.getElementById('st-supply').textContent = Number(CFG.total_supply).toLocaleString()+' '+CFG.symbol;
  document.getElementById('st-fee').textContent = CFG.network_fee ? CFG.fee_amount+' '+CFG.symbol : 'Sem Taxa';
}

async function saveConfig(){
  const cfg = {
    name:        document.getElementById('cfg-name').value,
    symbol:      document.getElementById('cfg-symbol').value,
    decimals:    parseInt(document.getElementById('cfg-decimals').value),
    total_supply: document.getElementById('cfg-supply').value,
    network_fee: document.getElementById('cfg-fee-toggle').checked,
    fee_amount:  document.getElementById('cfg-fee-amount').value,
  };
  const r = await post('/api/config', cfg);
  if(r.ok){ notify('✅ Configuração salva!'); await loadConfig(); }
  else notify('Erro ao salvar','err');
}

document.getElementById('cfg-fee-toggle').addEventListener('change', e=>{
  document.getElementById('fee-amount-wrap').style.display = e.target.checked?'':'none';
});

// ── Wallets ─────────────────────────────────────────────────────
async function loadWallets(){
  const ws = await get('/api/wallets');
  document.getElementById('st-wallets').textContent = ws.length;
  const el = document.getElementById('wallet-list');
  el.innerHTML = ws.length===0
    ? '<div style="color:var(--muted);font-size:12px;text-align:center;padding:20px">Nenhuma carteira criada</div>'
    : ws.map(w=>`
      <div class="wallet-item" title="${w.address}">
        <div style="width:8px;height:8px;border-radius:50%;background:var(--green);flex-shrink:0"></div>
        <div class="wallet-addr">${w.address}</div>
        <div><span class="wallet-bal">${w.balance.toFixed(4)}</span><span class="wallet-sym">${w.symbol}</span></div>
      </div>`).join('');
}

async function createWallet(){
  const r = await post('/api/wallets/create',{});
  notify(`✅ Carteira criada: ${r.address.slice(0,10)}...`);
  alert(`Nova carteira:\nEndereço: ${r.address}\nChave Privada: ${r.private_key}\n\n⚠️ Guarde a chave privada em local seguro!`);
  loadWallets();
}

function showImport(){
  const w = document.getElementById('import-wrap');
  w.style.display = w.style.display==='none'?'':'none';
}

async function importWallet(){
  const pk = document.getElementById('import-pk').value.trim();
  const r  = await post('/api/wallets/import',{private_key:pk});
  if(r.error){ notify(r.error,'err'); return; }
  notify(`✅ Importada: ${r.address.slice(0,10)}...`);
  document.getElementById('import-pk').value='';
  document.getElementById('import-wrap').style.display='none';
  loadWallets();
}

// ── Chain Explorer ───────────────────────────────────────────────
async function loadChain(){
  const data = await get('/api/chain');
  document.getElementById('st-height').textContent = data.height;
  document.getElementById('height-badge').textContent = `BLOCO #${data.height}`;
  const tbody = document.getElementById('blocks-tbody');
  tbody.innerHTML = (data.blocks||[]).map(b=>`
    <tr>
      <td style="color:var(--yellow);font-weight:700">${b.index}</td>
      <td class="hash-cell">${b.hash}</td>
      <td class="addr-cell">${b.miner}</td>
      <td>${b.transactions?.length||0}</td>
      <td style="color:var(--muted)">${new Date(b.timestamp*1000).toLocaleTimeString()}</td>
    </tr>`).join('') || '<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:20px">Nenhum bloco ainda</td></tr>';
}

// ── Transfer ────────────────────────────────────────────────────
async function doTransfer(){
  const from   = document.getElementById('tx-from').value.trim();
  const to     = document.getElementById('tx-to').value.trim();
  const amount = document.getElementById('tx-amount').value;
  if(!from||!to||!amount){ notify('Preencha todos os campos','err'); return; }
  const r = await post('/api/transfer',{from,to,amount:parseFloat(amount)});
  if(r.error){ notify(r.error,'err'); return; }
  notify(`✅ Tx enviada: ${r.tx_hash.slice(0,14)}...`);
  loadWallets();
}

// ── Mine ────────────────────────────────────────────────────────
async function mineBlock(){
  const addr = document.getElementById('mine-addr').value.trim();
  if(!addr){ notify('Insira um endereço','err'); return; }
  const btn = document.getElementById('mine-btn');
  btn.innerHTML='<span class="spin">⛏</span> MINERANDO...'; btn.disabled=true;
  const r = await post('/api/mine',{miner:addr});
  btn.innerHTML='⛏ MINERAR BLOCO'; btn.disabled=false;
  if(r.ok){
    const res = document.getElementById('mine-result');
    res.style.display='';
    res.innerHTML=`✅ Bloco #${r.block} minerado!<br>Recompensa: +${r.reward} ${r.symbol}<br>Saldo: ${r.miner_balance.toFixed(4)} ${r.symbol}`;
    notify(`⛏ Bloco #${r.block} minerado!`);
    loadWallets(); loadChain();
  } else {
    notify(r.error||'Erro ao minerar','err');
  }
}

// ── MetaMask info ───────────────────────────────────────────────
function updateMM(){
  const port = 8545;
  const info = document.getElementById('mm-info');
  info.innerHTML=`
    <div class="rpc-row"><span class="rpc-key">Nome da Rede</span><span class="rpc-val">${CFG.name||'LocalChain'}</span></div>
    <div class="rpc-row"><span class="rpc-key">URL RPC</span><span class="rpc-val">http://localhost:${port}</span></div>
    <div class="rpc-row"><span class="rpc-key">Chain ID</span><span class="rpc-val">${CFG.chain_id||1337}</span></div>
    <div class="rpc-row"><span class="rpc-key">Símbolo</span><span class="rpc-val">${CFG.symbol||'ETH'}</span></div>
    <div class="rpc-row"><span class="rpc-key">Decimais</span><span class="rpc-val">${CFG.decimals||18}</span></div>
  `;
}

// ── Status poll ─────────────────────────────────────────────────
async function refresh(){
  const s = await get('/api/status').catch(()=>null);
  if(s){ document.getElementById('st-height').textContent=s.height; }
}

// ── Init ────────────────────────────────────────────────────────
(async()=>{
  await loadConfig();
  await loadWallets();
  await loadChain();
  setInterval(()=>{ loadWallets(); refresh(); }, 5000);
})();
</script>
</body>
</html>
"""

# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cfg = load_config()
    if not os.path.exists(CONFIG_FILE):
        save_config(cfg)
        print(f"[CONFIG] Arquivo config.json criado.")

    print(f"""
╔══════════════════════════════════════════════════════╗
║           LOCAL BLOCKCHAIN — ERC-20                  ║
╠══════════════════════════════════════════════════════╣
║  Moeda    : {cfg['name']:<40} ║
║  Símbolo  : {cfg['symbol']:<40} ║
║  Decimais : {str(cfg['decimals']):<40} ║
║  Supply   : {cfg['total_supply']:<40} ║
║  Taxa     : {'Sim ('+str(cfg['fee_amount'])+')' if cfg.get('network_fee') else 'Não':<40} ║
╠══════════════════════════════════════════════════════╣
║  Painel   : http://localhost:{cfg.get('port',8545):<23} ║
║  JSON-RPC : http://localhost:{cfg.get('port',8545):<23} ║
║  Chain ID : {str(cfg.get('chain_id',1337)):<40} ║
╚══════════════════════════════════════════════════════╝
""")

    init_chain(cfg)
    port = cfg.get("port", 8545)
    app.run(host="0.0.0.0", port=port, debug=False)
