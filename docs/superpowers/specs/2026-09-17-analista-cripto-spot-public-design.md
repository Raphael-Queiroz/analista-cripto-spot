# Design: Analista Cripto Spot — open source leve

Date: 2026-09-17  
Owner: Raphael Queiroz (`Raphael-Queiroz`)  
Status: approved by user in chat

## Goal

Prepare the GitHub repository for public forks and PRs: MIT license, clearer bilingual docs, improved naming, and a contributor-friendly skill — without exposing private machine paths or live trading ledger data.

## Decisions

| Topic | Choice |
| --- | --- |
| Public name | Analista Cripto Spot |
| Repo slug | `analista-cripto-spot` (rename from `cripto-v3-1-0`) |
| Visibility | Public |
| License | MIT (copyright Raphael Queiroz, 2026) |
| Docs language | Bilingual PT + EN |
| Approach | Light open-source kit |
| Motor version | Keep 3.1.0 in code |

## Deliverables

1. `LICENSE` — MIT
2. `README.md` — bilingual showcase: what it is / is not, badges, mermaid flow, quick start, structure, contributing, risk disclaimer
3. `CONTRIBUTING.md` — bilingual fork/PR guide; accept rules/motor/docs; reject credentials, live ledger, order placement
4. `skills/analista-cripto-spot/SKILL.md` — rewritten; paths relative to `./cripto/`; no `/home/box/...`
5. Remove private `cripto/NOTAS_SETUP.md` (personal run logs) from the public tree
6. GitHub: rename repo, set description/topics, make public

## Out of scope

- CODE_OF_CONDUCT
- Issue/PR templates
- Deep `docs/` manuals beyond this design note
- Changing motor trading logic

## Success criteria

- Repo public at `https://github.com/Raphael-Queiroz/analista-cripto-spot`
- MIT license file present
- README + CONTRIBUTING bilingual and clear
- Skill readable without private paths
- Commit author is `Raphael-Queiroz` with noreply `241284243+Raphael-Queiroz@users.noreply.github.com`
- Analista Cripto bot files on the box remain untouched
