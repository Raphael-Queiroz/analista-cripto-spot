# Regras executáveis — Cripto v3.1.0

Revisão de 16/09/2026 para três análises completas por dia. Substitui a rotina única da v3.0. O protocolo e os resultados antigos continuam identificados como v3.0; não foram reclassificados como evidência desta versão. Os parâmetros são hipóteses de pesquisa, não valores otimizados ou comprovados. A implementação de referência é `motor_cripto.py`; divergência entre ela, este documento e o relatório deve bloquear a utilização até correção.

## 1. Dados, horários e validade

- Rodadas completas TODOS os dias às **08:00, 17:30 e 21:10**, no fuso **America/Sao_Paulo**. Em cada uma, primeiro revisar TODAS as posições abertas e depois procurar novas entradas em todos os ativos configurados. Compras e recomendações de venda são possíveis nos três horários.
- Universo preservado: BTC, ETH, BNB, SOL, XRP, ADA, DOGE, LINK, AVAX, DOT e LTC, Binance spot/USDT. A escolha atual do universo pode produzir viés de seleção em pesquisa histórica.
- Os indicadores e setups usam OHLCV diário UTC (`interval=1d`, `timeZone=0`). Excluir candle diário aberto; verificar OHLC, sequência sem lacunas e último fechamento esperado. Histórico desde 01/01/2019 ou primeira listagem posterior; mínimo de 250 candles fechados.
- A base técnica de um setup vem de um único fechamento diário e pode ser reavaliada nas três rodadas até o próximo fechamento. Às 08h e 17h30, normalmente se usa o fechamento das 21h da noite anterior. Às 21h10, usar o fechamento das 21h que acabou de ocorrer.
- Consultar novamente cotações e candles de **cinco minutos** em cada rodada. Os 5m verificam se stop/alvo foram tocados entre observações. Não recalcular SMA/RSI/ATR diários com esses candles. O trecho já observado do candle 5m atual pode revelar um toque, mas não é tratado como candle diário fechado nem prova preenchimento.
- `base_signal_id` identifica a base diária. `round_id` identifica data local e horário agendado. `signal_id` identifica a proposta dessa base naquela rodada. Reexecutar a mesma rodada mantém a identidade, com cotações atualizadas, sem fabricar oportunidade independente.
- Validade padrão: **[08:00,09:00), [17:30,18:30), [21:10,22:10)**. Os horários finais são excluídos. Atraso não prorroga a janela. A validade de 60 minutos é hipótese operacional; não é garantia de lucro. A rodada seguinte exige novo sinal e nova verificação.
- Fora das janelas, o motor pode atualizar dados e revisar posições; bloqueia novas entradas. Nunca falsificar horário para autorizar sinal vencido.
- Pré-checagem manual vale no máximo **60 segundos**, sem ultrapassar a validade do sinal. Recotar ao executar. Snapshot arquivado não substitui pré-checagem atual.
- Falha de dados: informar precisamente o erro. Não preencher lacunas por estimativa, página de preço, captura de gráfico, outra exchange ou memória do modelo. Cache tem origem, data de coleta e hash de integridade, que não é assinatura da exchange.

## 2. Indicadores

SMA 50 e 200 usam médias aritméticas de janelas completas de fechamentos. EMA 10 inicia pela média dos primeiros dez fechamentos e depois usa alfa 2/11. RSI 14 inicia pelas médias dos primeiros 14 ganhos/perdas entre fechamentos; depois usa Wilder, alfa 1/14. Série sem ganhos nem perdas resulta em RSI 50; sem perdas, mas com ganhos, RSI 100. ATR 14 usa true range; primeiro TR = máxima − mínima, primeira média com 14 TR, depois Wilder. Média de volume usa os 20 candles ANTERIORES, excluindo o candle avaliado.

Pivô de máxima: máxima estritamente maior que as duas anteriores e as duas posteriores. O pivô só existe operacionalmente depois que esses dois candles posteriores fecham. Empates não geram pivô. O código não consulta dados futuros para confirmar pivôs históricos.

## 3. Setup 1: pullback

Todos os critérios devem ser verdadeiros no candle t fechado:

1. Fechamento acima da SMA 200; SMA 50 acima da SMA 200; fechamento também acima da SMA 50. Esta última condição corrige a entrada já invalidada da v2.
2. Em pelo menos um candle j entre t−2 e t, `abs(mínima_j − EMA10_j) < ATR14_j` OU `abs(mínima_j − SMA50_j) < ATR14_j`. Cada comparação usa o indicador do mesmo candle.
3. Pelo menos um RSI em t−5 a t−1 está entre 40 e 50, inclusive; RSI_t > RSI_t−1.
4. Fechamento_t > abertura_t.
5. Existe um topo anterior confirmado até t−3, dentro da janela de 250 candles. Usar o pivô confirmado mais recente; ele deve estar acima da entrada.

Mínima do pullback = menor mínima de t−2 a t. Stop preliminar = essa mínima − 0,5 ATR_t. Se a distância entre fechamento_t e esse stop for inferior a 1 ATR_t, stop = fechamento_t − 1,5 ATR_t. A descontinuidade dessa regra foi preservada da v2 para não otimizar silenciosamente. Alvo = máxima do pivô anterior. Não usar alvo projetado no setup 1. Invalidação = fechamento diário abaixo da SMA 50 daquele dia. Igualdade não invalida, mas não autoriza nova compra.

## 4. Setup 2: rompimento

1. Fechamento acima da SMA 200.
2. Resistência construída por dois pivôs confirmados até t−1, nos 60 candles anteriores. Distância entre os testes de pelo menos cinco candles; diferença entre máximas de no máximo 0,25 ATR_t−1. Esses dois novos valores apenas eliminam ambiguidade e ainda precisam ser avaliados.
3. Nível da resistência = maior máxima dos dois testes. Não houve fechamento acima dela após o segundo teste e antes de t. Fechamento_t−1 ≤ resistência < fechamento_t. Entre resistências elegíveis, usar a maior.
4. Volume_t ≥ 1,5 × média dos 20 volumes anteriores.
5. RSI14_t < 75.

Stop preliminar = resistência − 0,5 ATR_t; aplicar a mesma regra de distância mínima do setup 1. Alvo = menor pivô confirmado acima do fechamento_t, procurando nos 250 candles anteriores. Se ausente e `synthetic_targets=true`, usar fechamento_t + 3 × (fechamento_t − stop). Identificar sempre como **alvo projetado em 3R**, sem alegar resistência, máxima histórica ou probabilidade de alcance. A ausência de pivô nessa janela não significa ausência de resistência em todo o histórico. Invalidação = fechamento diário estritamente abaixo da resistência fixada na entrada.

## 5. Sinal, preço executável e prioridade

Fechamento do candle é referência da base técnica, não preço garantido de compra. **Em cada um dos três horários**, reaplicar os critérios diários ao último candle fechado, buscar cotação atual e avaliar a entrada. Um R/R abaixo do mínimo no fechamento não elimina para sempre a base: um preço posterior pode melhorar a relação, desde que todos os demais requisitos sejam satisfeitos. O motor conserva essa base para reavaliação, identificando a insuficiência inicial.

Antes de aprovar uma nova compra:

1. Exigir janela ativa, cotação válida e histórico 5m desde o fechamento que gerou o setup.
2. Exigir `stop < bid ≤ ask < alvo`; estimativa de entrada inclui slippage. No pullback, bid > SMA 50 do último candle fechado; no rompimento, bid > resistência fixada. Esta verificação de entrada usa o preço atual e não muda a regra de saída por fechamento diário.
3. Se stop OU alvo já foram tocados desde aquele fechamento, esgotar a oportunidade desse candle, mesmo que o preço tenha voltado à faixa de entrada.
4. Bloquear ativo já comprado com esse candle de origem, mesmo após encerrar a posição. A regra vale entre setups e entre rodadas: **uma compra por ativo/candle**, sem piramidação. Sinal não executado pode ser reavaliado; recomendação não consome o setup.
5. Resolver saída, proteção ou execução pendente de posição existente antes de liberar compras novas. Se eventos da carteira mudarem depois do relatório, exigir nova análise; atualizar só saldo/âncoras de conciliação não altera os eventos.

Stop e alvo ficam fixados pelo sinal. Não afastar o alvo nem o stop para aprovar uma entrada cujo preço piorou.

Preço estimado de entrada = melhor oferta de venda (ask) × (1 + slippage estimado). Custos de entrada e saída entram no retorno/risco. Se E = entrada estimada, S = stop, A = alvo, f = taxa por lado e s = slippage por lado:

`RR_líquido = [A(1−s)(1−f) − E(1+f)] / [E(1+f) − S(1−s)(1−f)]`

Exigir 0 < S < E < A e RR líquido ≥ 2,0, inclusive após arredondamento de preços. Slippage é hipótese; uma perda real pode exceder a estimativa. A expressão considera taxa e impacto também na saída pelo alvo, conservadoramente, e não modela a fila de uma ordem limitada.

Prioridade estável: alvo em pivô histórico antes de alvo projetado; depois maior volume relativo; depois símbolo em ordem alfabética; depois setup 1 antes de 2. Essa prioridade não é uma classificação de probabilidade. Não priorizar apenas o R/R mais alto. Sem piramidação: no máximo uma posição por ativo.

## 6. Risco e configuração

Os valores fornecidos são um cenário de PAPER TRADING: capital fictício de 10.000 USDT; risco inicial máximo de 0,5% por operação; risco agregado máximo de 2%; até três posições; até 10% de alocação por ativo e 30% no total; suspensão de novas entradas em drawdown de 10%, perda diária de 2% ou semanal de 4%. Taxa por lado de 0,1% e slippage de 0,1% são hipóteses, não a tarifa da sua conta.

Quantidade é o menor valor permitido por: orçamento de risco dividido pela perda estimada por moeda; saldo livre incluindo taxa; limite por ativo; exposição total restante; regras da exchange. Arredondar sempre para baixo ao passo permitido. O risco agregado usa a perda estimada da marcação atual até os stops vigentes, incluindo margem de custo; mesmo uma posição com stop no preço de entrada ainda pode devolver lucro marcado e consumir orçamento.

Entradas reais não têm capital presumido. A pré-checagem exige saldo e posições reconciliados há no máximo 15 minutos, confirmação de custos e limites, proteção confirmada das posições abertas e âncoras de patrimônio válidas. Todos os saldos da subcarteira da estratégia devem ser informados; ativos externos a ela não entram implicitamente no cálculo. Depósitos e saques exigem ajuste documentado das âncoras para não simular lucro/perda.

O motor não recebe chaves e não envia, cancela ou altera ordens. Filtros de preço, lote e notional são verificações preliminares; regras dinâmicas e aceitação final da OCO dependem da Binance. A pré-checagem avalia os limites antes de cada possível entrada nos três horários. No simulador, patrimônio e drawdown são amostrados em 5m; as novas entradas só ocorrem nas rodadas. Isso não equivale a acompanhamento contínuo da conta real. Interromper novas entradas não cancela a necessidade de acompanhar posições existentes.

## 7. Posições, OCO e saídas

Guardar compras, vendas, alterações confirmadas de proteção e cancelamentos como eventos com ID e instante UTC. O stop original e a quantidade original permanecem imutáveis. Uma sugestão não modifica o estado. Quantidade de compra deve ser líquida e conciliada; taxas pagas em outro ativo devem ser registradas com sua equivalência em USDT e critério de conversão.

Revisar todas as posições antes de candidatos, **às 08:00, 17:30 e 21:10**. A compra confirmada após uma rodada deve aparecer nas seguintes. Ordem de trabalho:

1. Conciliar execuções reais e quantidade restante. Eventos duplicados, venda acima do saldo, piramidação e mudança temporal inválida são rejeitados.
2. Verificar existência e quantidade da proteção. Depois de venda parcial, o registro exige nova confirmação de OCO para o saldo remanescente.
3. Informar gatilhos tocados como execução **não confirmada**, quando só houver evidência de preço. Dados diários não resolvem a ordem dos eventos intradiários nem isolam trechos de candle antes/depois de uma alteração.
4. Emitir **VENDER** quando a tese estiver invalidada pelo último fechamento diário, o preço atual estiver no/abaixo do stop vigente ou no/acima do alvo. A indicação vale apenas se a quantidade ainda existir; confirmar antes se a OCO já vendeu. Registrar a venda somente após execução confirmada. Não colocar a opinião de um gestor acima de uma execução já ocorrida.
5. Se `stop_variant=steps`: ao preço observado ≥ entrada + 1R, propor stop na entrada; ≥ entrada + 2R, propor stop em entrada + 1R. R = entrada efetiva − stop original, sempre. Não reduzir stop nem tratá-lo como alterado antes da confirmação. Stop no preço de entrada não assegura empate líquido.
6. Emitir **MANTER** quando não houver critério de saída e as informações permitirem revisar a posição. Se um toque anterior puder ter executado a proteção, ou ela estiver ausente, emitir **CONFERIR_EXECUCAO**. Se faltarem dados, emitir **DADOS_INSUFICIENTES**. Não escolher manter/vender sem evidência para satisfazer um formato binário.

A invalidação por fechamento diário é reapreciada nos três horários, mas seu indicador não é substituído pelo candle em formação. A verificação de stop/alvo usa cotação atual e histórico observado. Uma sugestão de subir stop só é apresentada quando a decisão é MANTER; nunca compete com uma recomendação de saída ou pendência de execução.

Opinião, nota de sentimento ou debate não geram venda ou parcial automática. A regra de reduzir 50% diariamente foi retirada. Uma parcial manual pode ser registrada uma vez por `partial_reason_id`; novo motivo deve ser uma nova decisão explícita. `STOP_LOSS_LIMIT` exige gatilho E preço limite e pode não executar após disparar. `STOP_LOSS` pode ter slippage. Nenhum deles garante a perda máxima projetada.

## 8. Simulação, evidência e memória

O simulador da v3.1 utiliza indicadores diários e histórico de execução em **5m**. São indispensáveis ambas as séries: o histórico diário deslocado da v3.0 não permite reconstruir três decisões por dia. Se os dados 5m necessários não existirem ou houver lacuna, o teste falha; não inventar preços às 08h/17h30.

Para uma execução UTC [início,fim), exigir também um dia anterior de 5m, para verificar o caminho desde a origem dos setups. Cada análise ocorre em 08:00,17:30,21:10 locais; o preço de execução simulado é a abertura cinco minutos depois (**08:05,17:35,21:15**). Esse atraso é hipótese, não promessa de latência no Grok. Indicadores só usam candles diários encerrados antes da análise. Cotação e gatilhos até a entrada são revalidados.

Antes de decidir em cada rodada, resolver gaps de ordens de proteção antigas, aplicar invalidação e propor/ativar o stop em etapas no modelo. Recalcular risco após saídas e antes de cada entrada. Uma compra consome o ativo/candle para as rodadas seguintes. Depois das decisões, avaliar máxima/mínima do novo intervalo; nunca usar sua máxima/mínima futura para escolher a entrada.

Proteção simulada: stop a mercado com slippage. Se stop e alvo aparecem no mesmo candle 5m, usar stop primeiro. Gap além do stop usa a abertura adversa. Isso não testa preenchimento de stop-limit real, fila, falhas de OCO, indisponibilidade, filtros históricos ou atraso manual variável. Quantidade histórica é fracionária. Drawdown em aberturas/fechamentos 5m ainda pode subestimar oscilação dentro do candle. Posições finais ficam abertas e marcadas; não contam como acerto/erro encerrado.

Benchmarks: caixa nominal de 0% e BTC comprado e mantido, bruto, no mesmo intervalo; a exposição é diferente. Histórico v3.0, com uma rodada, é **somente referência da versão anterior**. Testes funcionais e uma simulação curta de integração da v3.1 não comprovam rentabilidade. Não há estudo econômico extensivo da nova rotina nem medição prospectiva do valor do Grok nesta entrega.

Reflexão semanal usa eventos confirmados: PnL realizado vem de vendas e custos; R preserva o risco inicial monetário. Separar posições abertas, encerradas, sinais não executados e vetos. Três observações da mesma base não são três operações. Não mudar parâmetros por uma semana ruim; toda hipótese nova cria nova versão e exige avaliação prospectiva.

Para comparar a IA com o motor, arquivar ALLOW/VETO com `round_id`, `signal_id`, `base_signal_id`, `observed_at`, modelo, versão do prompt e fontes antes da tentativa de entrada. A decisão deve pertencer à rodada correspondente. `--vetoes` bloqueia sinal sem decisão contemporânea válida; nunca fabricar decisões históricas. A pré-checagem real não incorpora automaticamente esse diário: a skill aplica o veto separadamente.
