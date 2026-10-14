# ADR-010: Coleta de respostas por lotes de perguntas

- **Status:** accepted

## Contexto

Respostas são uma entidade própria. A API expõe três caminhos plausíveis: lotes de IDs de perguntas, janela temporal de respostas e a combinação dos dois. A coleta de perguntas já usa tag e janela; o separador `;` desse filtro é AND e não deve ser reutilizado para IDs.

## Decisão

Coletar por `/questions/{question_ids}/answers`. Os IDs são inteiros positivos, únicos e ordenados. O lote é determinístico, não vazio, limitado por tamanho e pelo orçamento de URL. O path usa `;` somente como vetor de IDs. A lista completa fica no manifesto e no envelope da página; o diretório usa um prefixo curto do hash do lote.

Cada lote pagina até `has_more=false` ou até o limite configurado. Uma falha de lote, quota esgotada com páginas restantes ou truncamento impede sucesso global e não avança o checkpoint. O checkpoint identifica `source + site + endpoint + estratégia` (`question_id_batches`). Não há watermark por `answer_id`.

O modo `incremental` busca só lotes ausentes do checkpoint e avança o cursor quando a lista solicitada termina por completo. O modo `refresh` busca todos os lotes solicitados, inclusive os já checkpointados, grava outro snapshot e não move o cursor.

A Silver trata a resposta por `site + answer_id` e o relacionamento por `site + question_id`. Score e aceite são observações; não abrem versão de conteúdo. História fora de ordem é reconstruída por `collected_at`, com desempate por hash e observação. Divergência com a pergunta é evento de qualidade, não correção.

## Alternativas

Janela temporal de `/answers` cobre atividade recente, mas não amarra a cobertura às perguntas já conhecidas e mistura criação, edição e aceite no mesmo relógio. A combinação das duas estratégias aumenta quota e complica o cursor; fica adiada até haver necessidade explícita de descobrir respostas órfãs.

## Consequências

A cobertura é auditável para as perguntas fornecidas. Respostas de perguntas ausentes da lista não entram. Ausência numa coleta não prova ausência histórica. Data Dump continua necessário para backfill confiável de longo prazo. Cada run tem diretório próprio, então uma observação nova não substitui a anterior. A atomicidade local tem a mesma janela das perguntas: o checkpoint pode avançar instantes antes do manifesto terminal.

## Riscos

Um lote grande estoura a URL; o limite configurável recusa o ID que não cabe sozinho. Refresh não atualiza o cursor incremental; uma falha no meio do refresh deixa os snapshots parciais daquele run sem marcar os lotes como concluídos.
