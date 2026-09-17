---
name: Analista Cripto Spot
description: >-
  Use when analysing Binance spot crypto for entries, reviewing open positions,
  running the Analista Cripto Spot motor (fetch/snapshot/preflight), or producing
  the scheduled 08:00 / 17:30 / 21:10 America/Sao_Paulo reports.
---

# Analista Cripto Spot

Mesa de análise **Binance spot** para agentes. O motor calcula setups verificáveis; o humano confirma e executa.

**EN:** Binance **spot** analysis desk for agents. The motor computes verifiable setups; the human confirms and executes.

## Support files (`cripto/`)

| File | Role |
| --- | --- |
| `motor_cripto.py` | Motor (VERSION **3.1.0**) |
| `configuracao.json` | Symbols, risk, analysis windows |
| `registro.json` | Ledger of **confirmed** ops only (never invent fills) |
| `REGRAS.md` | Executable rules (authoritative) |
| `INSTRUCAO.md` | Report protocol |
| `dados/`, `resultados/` | Runtime OHLCV + snapshots (gitignored) |

Paths are relative to the repo: run commands from `cripto/`.

## Hard limits

- Respond in **Portuguese** (unless the operator asks otherwise)
- Spot only — no leverage, no shorts
- No broker credentials, no order placement
- User executes manually
- Never invent OHLCV, indicators, fees, balances, or fills

## Before every full analysis

1. Read `REGRAS.md`, `configuracao.json`, and current `registro.json`.
2. Prefer **Mode A**: run the motor locally. If Mode A fails, **Mode B** is JSON from the same motor elsewhere. If neither works → **DADOS INSUFICIENTES — SEM SINAL OPERACIONAL**.
3. Numbers come only from the motor (or verified JSON) and confirmed ledger events.

## Mode A commands

```bash
cd cripto
python3 motor_cripto.py --config configuracao.json fetch --data dados
python3 motor_cripto.py --config configuracao.json snapshot --data dados --ledger registro.json --output resultados/snapshot.json
```

Optional: `preflight` before a buy proposal. Always show execution evidence (version, config hash, UTC server time, last closed candle, bars per symbol, data origin, errors).

## Analysis order

1. Reconcile **all** open positions from `registro.json` + motor output before hunting new entries.
2. Accept only the two setups in `REGRAS.md` (pullback / breakout) as computed by the motor.
3. Informational layer (news/sentiment) for at most three valid candidates; may **VETO INFORMACIONAL** with source+time — never change levels or risk.
4. If any hard check fails → **SEM ENTRADA OPERACIONAL** with the reason. A valid idea is always **PROPOSTA PARA CONFERÊNCIA E EXECUÇÃO MANUAL**.

## Report sections

Estado da execução → Posições abertas → Triagem → Candidatos válidos → Próximas ações manuais → Fontes e horários.

## Ledger

Update `registro.json` only after the user confirms a buy, sell, or protection change. Keep one current file.

## Scheduled rounds

Full analyses daily at **08:00, 17:30, 21:10** `America/Sao_Paulo` (all seven days). Outside validity windows, positions may be reviewed but new entries stay blocked.
