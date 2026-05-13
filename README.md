# 🔗 LocalChain — Blockchain ERC-20 Local com MetaMask

Sistema completo de blockchain local com padrão ERC-20, suporte a MetaMask, armazenamento LevelDB e cliente de mineração.

---

## 📦 Instalação

```bash
pip install flask flask-cors ecdsa plyvel requests
```

---

## 🚀 Uso

### 1. Iniciar a blockchain

```bash
python blockchain.py
```

Abre o painel em **http://localhost:8545**

### 2. Minerar moedas

```bash
python minerar.py
```

Ou com argumentos:

```bash
python minerar.py --server http://localhost:8545 --wallet 0xSEU_ENDERECO --interval 5
```

| Argumento    | Descrição                            |
|--------------|--------------------------------------|
| `--server`   | URL do servidor (padrão: localhost)  |
| `--wallet`   | Endereço da carteira                 |
| `--interval` | Segundos entre blocos (padrão: 5)    |
| `--blocks`   | Parar após N blocos (0 = infinito)   |

---

## 🦊 Configurar MetaMask

1. Abra MetaMask → Adicionar rede manualmente
2. Preencha:
   - **Nome**: MyCoin (ou o nome que você definiu)
   - **URL RPC**: `http://localhost:8545`
   - **Chain ID**: `1337`
   - **Símbolo**: `MYC`
3. Salve e selecione a rede
4. Importe uma carteira pela chave privada gerada no painel

---

## 📁 Arquivos

| Arquivo         | Função                                      |
|-----------------|---------------------------------------------|
| `blockchain.py` | Servidor da blockchain + painel web         |
| `minerar.py`    | Cliente de mineração interativo             |
| `config.json`   | Configuração da moeda (editável pelo painel)|
| `chaindata/`    | Banco LevelDB com blocos e saldos           |

---

## ⚙️ Configuração (config.json)

```json
{
  "name": "MyCoin",
  "symbol": "MYC",
  "decimals": 18,
  "total_supply": "1000000",
  "network_fee": false,
  "fee_amount": "0",
  "chain_id": 1337,
  "port": 8545
}
```

Você pode editar pelo painel web ou diretamente no arquivo.

---

## 🔌 API REST

| Método | Rota                    | Descrição                   |
|--------|-------------------------|-----------------------------|
| GET    | `/api/status`           | Status da rede              |
| GET    | `/api/config`           | Configuração atual          |
| POST   | `/api/config`           | Atualizar configuração      |
| GET    | `/api/wallets`          | Listar carteiras            |
| POST   | `/api/wallets/create`   | Criar nova carteira         |
| POST   | `/api/wallets/import`   | Importar por chave privada  |
| POST   | `/api/mine`             | Minerar um bloco            |
| POST   | `/api/transfer`         | Transferir tokens           |
| GET    | `/api/chain`            | Últimos blocos              |

---

## 📡 JSON-RPC (MetaMask)

Endpoint: `POST http://localhost:8545`

Métodos suportados:
- `eth_chainId`
- `eth_blockNumber`
- `eth_getBalance`
- `eth_accounts`
- `eth_sendTransaction`
- `eth_call` (ERC-20: totalSupply, balanceOf, name, symbol, decimals)
- `eth_gasPrice`
- `eth_estimateGas`
- `net_version`
- `net_listening`
- `web3_clientVersion`
