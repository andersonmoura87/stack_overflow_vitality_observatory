# Runbook: respostas Bronze e Silver

Os testes e o dry-run não chamam a Stack Exchange API. O manifesto da execução é a evidência durável. Logs trazem run, site, lote, contagem de IDs, página, quota, backoff e status; não trazem API key, corpo nem perfil.

## Quota esgotada

Sintoma: manifesto `failed` e erro de quota com `has_more`. O checkpoint não avança. Espere a janela de quota e repita a mesma lista de IDs. Páginas já confirmadas com o mesmo payload são replay.

## Backoff prolongado

Sintoma: logs `answer_api_backoff`. O coletor espera o valor inteiro devolvido pela API e não o reduz. Se o prazo operacional estourar, interrompa o processo; o lock é liberado no `finally` apenas se o processo sair. Não apague páginas já publicadas.

## Lote parcial

Sintoma: manifesto `failed`, IDs pendentes preenchidos, checkpoint ausente ou inalterado. Um lote com erro invalida o sucesso global. As páginas já gravadas permanecem sob o `run_id` daquela execução e não contam como progresso. A repetição em `--mode incremental` busca de novo só os lotes que o checkpoint ainda não contém. `--mode refresh` busca outra vez os lotes pedidos, mesmo os já concluídos, e não move o cursor.

## Truncamento

Sintoma: manifesto `truncated` porque `has_more` sobreviveu a `max_pages` ou `--max-batches` cortou a lista. Isso não é sucesso e não avança o checkpoint. Aumente o limite e repita. Não trate a página truncada como população fechada.

## Checkpoint inconsistente

Sintoma: `CheckpointConflict` ou arquivo ilegível em `answers/checkpoint.json`. Não reconstrua o cursor à mão para destravar uma falha. O cursor só pode crescer e é a única lista de lotes concluídos. Backfill não deve alterá-lo. A janela entre o CAS e o manifesto pode deixar o cursor à frente de um manifesto ausente; a execução operacional seguinte não rebusa esses lotes.

## Lock concorrente

Sintoma: `local writer lock exists` em `answers/.writer.lock` ou `answers/.transform.lock`. Veja se há outro processo. Lock antigo depois de crash exige remoção manual, só quando não houver escritor ativo. O coletor não limpa lock stale.

## Divergência de aceite

Sintoma: SA-011 ou SA-012 na reconciliação. Não altere `is_accepted` nem a Silver de perguntas. Registre a divergência na análise. Mais de uma resposta aceita no mesmo instante observado permanece visível.

## Cobertura incompleta

Sintoma: SA-013 ou `coverage_complete=false`. A pergunta conhece um `accepted_answer_id` que esta coleta não viu. Isso pode ser lote incompleto, não prova de ausência. Não calcule tempo até a primeira resposta com essa cobertura.

## Falha de publicação

Sintoma: transformação termina sem manifesto `completed` ou `partial`. Confira checksum da página Bronze. Saídas órfãs em `answers/generation=*` não são a geração committed sem manifesto. Não publique sucesso a partir de um manifesto `failed` da coleta se a decisão for exigir cobertura fechada; a transformação lê páginas confirmadas e declara a cobertura no próprio manifesto.
