#!/usr/bin/env python3
"""
minerar.py — Cliente de Mineração para LocalChain ERC-20
O usuário informa o endereço do servidor e o endereço da carteira.
Mina continuamente e exibe o saldo acumulado.
"""

import os
import sys
import json
import time
import hashlib
import requests
import argparse
from datetime import datetime

# ─── Cores ANSI ───────────────────────────────────────────────────────────────
R  = "\033[0m"
B  = "\033[1m"
CY = "\033[96m"
GR = "\033[92m"
YL = "\033[93m"
RD = "\033[91m"
MG = "\033[95m"
DM = "\033[2m"

def clr(): os.system("cls" if os.name == "nt" else "clear")

def banner(cfg):
    name   = cfg.get("name","LocalChain")
    sym    = cfg.get("symbol","???")
    dec    = cfg.get("decimals",18)
    fee    = cfg.get("network_fee",False)
    fee_v  = cfg.get("fee_amount","0") if fee else "Sem taxa"
    height = cfg.get("height",0)
    rpc    = cfg.get("rpc_url","")
    print(f"""
{CY}{B}╔══════════════════════════════════════════════════════╗
║         ⛏  MINERADOR LOCAL  —  {name:<21}║
╠══════════════════════════════════════════════════════╣
║  Símbolo   : {sym:<39}{CY}{B}║
║  Decimais  : {str(dec):<39}{CY}{B}║
║  Taxa Rede : {fee_v:<39}{CY}{B}║
║  Bloco     : #{str(height):<38}{CY}{B}║
║  Servidor  : {rpc:<39}{CY}{B}║
╚══════════════════════════════════════════════════════╝{R}""")

def fmt_time():
    return datetime.now().strftime("%H:%M:%S")

def get_status(server: str) -> dict:
    try:
        r = requests.get(f"{server}/api/status", timeout=5)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def mine_block(server: str, miner_addr: str) -> dict:
    try:
        r = requests.post(
            f"{server}/api/mine",
            json={"miner": miner_addr},
            timeout=30
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_balance(server: str, address: str, cfg: dict) -> float:
    try:
        r = requests.get(f"{server}/api/wallets", timeout=5)
        wallets = r.json()
        for w in wallets:
            if w["address"].lower() == address.lower():
                return float(w["balance"])
        # Fallback via RPC
        payload = {
            "jsonrpc":"2.0","id":1,
            "method":"eth_getBalance",
            "params":[address,"latest"]
        }
        r2 = requests.post(server, json=payload, timeout=5)
        data = r2.json()
        raw  = int(data.get("result","0x0"), 16)
        dec  = int(cfg.get("decimals", 18))
        return raw / (10**dec)
    except:
        return 0.0

def input_server() -> str:
    print(f"{CY}{B}┌─────────────────────────────────────────────┐")
    print(f"│        MINERADOR LOCALCHAIN ERC-20          │")
    print(f"└─────────────────────────────────────────────┘{R}")
    print()
    print(f"{DM}Endereço padrão: http://localhost:8545{R}")
    server = input(f"{YL}▶ Endereço do servidor {DM}[Enter = localhost:8545]{R}{YL}: {R}").strip()
    if not server:
        server = "http://localhost:8545"
    if not server.startswith("http"):
        server = "http://" + server
    return server.rstrip("/")

def input_wallet(server: str) -> str:
    print()
    print(f"{DM}Deixe em branco para criar uma nova carteira automaticamente.{R}")
    addr = input(f"{YL}▶ Endereço da carteira {DM}[Enter = criar nova]{R}{YL}: {R}").strip()
    if not addr:
        try:
            r = requests.post(f"{server}/api/wallets/create", json={}, timeout=5)
            data = r.json()
            addr = data["address"]
            pk   = data.get("private_key","")
            print(f"\n{GR}{B}✅ Carteira criada!{R}")
            print(f"   Endereço    : {CY}{addr}{R}")
            print(f"   Chave Priv. : {YL}{pk}{R}")
            print(f"\n{RD}{B}⚠️  GUARDE a chave privada! Não será exibida novamente.{R}")
            input("\n   Pressione ENTER para continuar...")
        except Exception as e:
            print(f"{RD}Erro ao criar carteira: {e}{R}")
            sys.exit(1)
    return addr

def run_miner(server: str, miner: str, interval: int, max_blocks: int):
    clr()
    # Fetch initial config
    cfg = get_status(server)
    if "error" in cfg:
        print(f"{RD}❌ Não foi possível conectar ao servidor: {cfg['error']}{R}")
        sys.exit(1)

    sym     = cfg.get("symbol","???")
    mined   = 0
    earned  = 0.0
    errors  = 0
    start_t = time.time()

    while True:
        if max_blocks and mined >= max_blocks:
            print(f"\n{GR}✅ Meta atingida: {mined} blocos minerados.{R}")
            break

        clr()
        cfg = get_status(server)
        banner(cfg)

        elapsed = int(time.time() - start_t)
        h = elapsed//3600; m=(elapsed%3600)//60; s=elapsed%60

        print(f"\n{B}  Minerador   : {CY}{miner}{R}")
        print(f"{B}  Sessão      : {YL}{h:02d}:{m:02d}:{s:02d}{R}  |  "
              f"Blocos: {GR}{mined}{R}  |  "
              f"Erros: {RD}{errors}{R}")
        print()
        print(f"  {DM}[{fmt_time()}]{R} ⛏  Minerando bloco...")

        result = mine_block(server, miner)

        if result.get("ok"):
            mined  += 1
            reward  = float(result.get("reward", 0))
            earned += reward
            bal     = float(result.get("miner_balance", 0))
            blk     = result.get("block", "?")
            h_str   = result.get("hash","")[:16]+"..."

            print(f"  {GR}{B}✅ Bloco #{blk} minerado!{R}")
            print(f"     Hash      : {DM}{h_str}{R}")
            print(f"     Recompensa: {GR}+{reward:.4f} {sym}{R}")
            print(f"     Saldo     : {CY}{B}{bal:.4f} {sym}{R}")
            print()
            print(f"  Total ganho nesta sessão: {MG}{B}{earned:.4f} {sym}{R}")

        elif result.get("error"):
            errors += 1
            print(f"  {RD}❌ Erro: {result['error']}{R}")
        else:
            errors += 1
            print(f"  {RD}❌ Resposta inesperada do servidor{R}")

        # Progress bar para próximo bloco
        print()
        for i in range(interval, 0, -1):
            bar = "█" * (interval - i) + "░" * i
            bar = bar[:30]
            sys.stdout.write(f"\r  {DM}Próximo bloco em {i:2d}s  [{bar}]{R}   ")
            sys.stdout.flush()
            time.sleep(1)
        print()

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Minerador LocalChain ERC-20",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--server",   "-s", help="URL do servidor (ex: http://localhost:8545)")
    parser.add_argument("--wallet",   "-w", help="Endereço da carteira para receber recompensas")
    parser.add_argument("--interval", "-i", type=int, default=5,  help="Segundos entre blocos (padrão: 5)")
    parser.add_argument("--blocks",   "-b", type=int, default=0,  help="Parar após N blocos (0 = infinito)")
    args = parser.parse_args()

    print(f"\n{CY}{B}{'='*54}")
    print(f"  ⛏  MINERADOR LOCALCHAIN ERC-20")
    print(f"{'='*54}{R}\n")

    server = args.server or input_server()

    # Verificar conexão
    print(f"\n{DM}Conectando a {server}...{R}")
    status = get_status(server)
    if "error" in status:
        print(f"{RD}❌ Falha na conexão: {status['error']}{R}")
        print(f"{DM}Verifique se o blockchain.py está rodando.{R}")
        sys.exit(1)

    print(f"{GR}✅ Conectado! Rede: {status.get('name','?')} ({status.get('symbol','?')}){R}")

    miner = args.wallet or input_wallet(server)

    print(f"\n{GR}Iniciando mineração...{R}")
    time.sleep(1)

    try:
        run_miner(server, miner, args.interval, args.blocks)
    except KeyboardInterrupt:
        print(f"\n\n{YL}⚡ Mineração interrompida pelo usuário.{R}\n")

if __name__ == "__main__":
    main()
