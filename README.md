# Cripto v3.1.0

Skill e arquivos de apoio da mesa de análise Binance spot (modo paper / execução manual).

## Conteúdo

- `skills/cripto-v3-1-0/SKILL.md` — skill do agente
- `cripto/` — motor, regras, instrução, configuração e registro
  - `motor_cripto.py` (VERSION 3.1.0)
  - `configuracao.json`
  - `REGRAS.md`
  - `INSTRUCAO.md`
  - `registro.json` — template vazio (não inclui ledger real)
  - `dados/` e `resultados/` — runtime; só `.gitkeep` no git

## Uso local

No diretório `cripto/`:

```bash
python3 motor_cripto.py --config configuracao.json fetch --data dados
python3 motor_cripto.py --config configuracao.json snapshot --data dados --ledger registro.json --output resultados/snapshot.json
```

Spot only. Sem alavancagem, sem shorts, sem credenciais de corretora e sem envio de ordens.

## Origem

Espelho dos arquivos usados pelo Analista Cripto em `/home/box/cripto/` e da skill `cripto-v3-1-0`. Este repositório é só backup/versionamento — não altera o bot.
