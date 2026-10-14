# Gold vitality v1

Indicadores descritivos de perguntas. A Gold não estima tempo até resposta, sobrevivência, efeito de ferramenta ou qualquer causa. Conclusão causal exige desenho estatístico, controles, cobertura histórica adequada e fontes complementares.

## Entrada

A única fonte é a geração Silver `silver_questions.v1` selecionada pelo maior `commit_sequence` com status `completed` ou `empty`. O leitor confirma os checksums. Bronze, watermark e a própria Silver não são alterados.

Cada pergunta entra uma vez, pela chave `(site, question_id)`. A materialização `questions.jsonl` não é a série de métricas: o corte usa as observações.

## Corte `as_of`

`as_of` é o instante UTC, com sufixo `Z`, até o qual o estado é descrito. Publicação real exige `--as-of`. Sem esse argumento o comando falha antes de criar arquivos. Não é o relógio da parede nem a data em que a plataforma parou.

`--dry-run` sem `--as-of` pode descobrir o maior `collected_at` e imprimi-lo com `as_of_source=derived_max_collected_at`. Essa descoberta não publica. `--dry-run` com `--as-of`, e toda publicação real, usam o valor informado e gravam ou imprimem `as_of_source=explicit`. Timestamp sem sufixo `Z`, sem timezone ou inválido falha antes da escrita.

As métricas vêm da observação de maior `collected_at` entre as que satisfazem `collected_at <= as_of`. No mesmo instante, o desempate é o da Silver: `record_hash` e depois `observation_id`. Score, visualizações, `answer_count`, `is_answered` e `accepted_answer_id` vêm dessa observação.

Tags, `creation_at` e `closed_at` vêm da versão de conteúdo vigente: `valid_from <= as_of` e (`valid_to` nulo ou `as_of < valid_to`). A materialização do intervalo pode carregar métricas de uma observação posterior ao corte; a Gold não as usa.

`creation_month` é o mês UTC de `creation_at`, não o mês da coleta nem `valid_from`. Pergunta com observação elegível e `creation_at > as_of` fica de fora e incrementa `questions_created_after_as_of`. Pergunta cujas observações são todas posteriores ao corte incrementa `questions_without_observation`. Nenhuma das duas vira zero nas distribuições.

`exposure_seconds_min`, `exposure_seconds_p50` e `exposure_seconds_max` são os segundos inteiros entre `creation_at` e `as_of`. Meses recentes tiveram menos tempo para receber respostas. Uma queda no fim da série, lida sem a exposição, mistura censura à direita com variação de volume.

## Três sinais de resposta

Eles não são uma definição única de “respondida”.

| Sinal | Regra |
| --- | --- |
| `source_is_answered_true` / `_false` / `_unknown` | Valor informado pela fonte: `true`, `false` ou `null`. |
| `has_answers_true` | `answer_count` conhecido e maior que zero. |
| `has_answers_false` | `answer_count` conhecido e igual a zero. |
| `has_answers_unknown` | `answer_count` nulo. |
| `has_accepted_answer_true` | `accepted_answer_id` presente. |
| `has_accepted_answer_false` | `accepted_answer_id` nulo ou ausente. |

`null` não vira zero nem `false` em `is_answered`, `answer_count`, score, visualizações ou tags. A única ausência que produz `false` é a do identificador de resposta aceita: o sinal registra se um id foi observado. Isso não inventa timestamp de resposta e não substitui `answer_count`.

Divergência conhecida é contada e não corrigida:

- `is_answered_false_with_answers`: `is_answered` é `false` e `answer_count > 0`.
- `is_answered_true_without_answers`: `is_answered` é `true` e `answer_count` é zero.
- `accepted_without_answers`: há `accepted_answer_id` e `answer_count` conhecido igual a zero.

Se um dos lados for nulo, a divergência correspondente não é contada. `is_answered=false` com `answer_count>0` permanece ao mesmo tempo em `source_is_answered_false` e em `has_answers_true`.

## Fechamento e distribuições

`closed_known` conta `closed_at` presente no conteúdo do corte. `closed_unknown` conta `closed_at` nulo. Nulo não é tratado como pergunta aberta.

Score, `view_count` e `answer_count` usam somente valores conhecidos. Para cada um há `known`, `unknown`, `min`, `p50`, `p90`, `max` e `sum`. Sem valores conhecidos, as estatísticas são `null`, não zero. Score pode ser negativo. O percentil é nearest rank, 1-based, sobre a lista crescente: `rank = ceil(n * p)`, com `p` em milionésimos e aritmética inteira. `p50` usa 500000 e `p90` usa 900000.

## Tags

`gold_vitality_monthly.v1` conta a pergunta uma vez. `gold_vitality_tag_monthly.v1` tem grão `site + creation_month + tag + as_of` e conta a mesma pergunta uma vez em cada tag. Tags nulas incrementam `questions_missing_tags` e não criam tag vazia.

`tag_assignments` soma as tags das perguntas que as têm. `max_tag_question_share_ppm` é `floor(max_tag_questions * 1000000 / questions_with_tags)`: a tag mais frequente sobre as perguntas com ao menos uma tag. Como uma pergunta tem várias tags, essas frações podem somar mais de 1000000.

`tag_assignment_hhi_ppm` soma `floor(contagem^2 * 1000000 / assignments^2)` por tag. `assignment_share_ppm`, no dataset de tag, usa o mesmo denominador de assignments. Cada termo é truncado antes da soma; o resto pode fazer as frações somarem menos de 1000000. Não há ponto flutuante binário nas saídas.

## Fora de escopo

Tempo até a primeira resposta, sobrevivência, respostas e comentários como entidades, sentimento, NLP, detecção de IA, perfil além do que a Silver já reteve, completude do histórico da plataforma e qualquer efeito causal. Ausência de observação nova não significa que a pergunta foi removida.

## Datasets

`gold_vitality_monthly.v1`, grão `site + creation_month + as_of`:

`schema_version`, `site`, `creation_month`, `as_of`, `questions`, `source_is_answered_true`, `source_is_answered_false`, `source_is_answered_unknown`, `has_answers_true`, `has_answers_false`, `has_answers_unknown`, `has_accepted_answer_true`, `has_accepted_answer_false`, `is_answered_false_with_answers`, `is_answered_true_without_answers`, `accepted_without_answers`, `closed_known`, `closed_unknown`, `score_known`, `score_unknown`, `score_min`, `score_p50`, `score_p90`, `score_max`, `score_sum`, e os mesmos sete sufixos para `view_count` e `answer_count`, `questions_with_tags`, `questions_missing_tags`, `tag_assignments`, `distinct_tags`, `max_tag_questions`, `max_tag_question_share_ppm`, `tag_assignment_hhi_ppm`, `exposure_seconds_min`, `exposure_seconds_p50`, `exposure_seconds_max`.

`gold_vitality_tag_monthly.v1`: `schema_version`, `site`, `creation_month`, `tag`, `as_of`, `questions`, `assignment_share_ppm`.

## Publicação

`gold_vitality_publication.v1` é o commit. JSONL canônico, uma geração por hash do conteúdo e do corte, manifesto por último, hard link imutável. O mesmo Silver, o mesmo corte e a mesma raiz reproduzem os mesmos bytes. Não há ponteiro mutável de “última” publicação: o comando imprime `manifest_path`. Publicações de cortes diferentes convivem.

O manifesto repete as contagens Silver de entrada, aceitos, quarentena, erros e warnings. Também traz `questions_included`, `questions_without_observation`, `questions_created_after_as_of`, `as_of_source` (`explicit` na publicação; `derived_max_collected_at` só no dry-run sem corte informado), `descriptive_only=true` e `causal_claims=unsupported`.

A cobertura analítica não é o número de aceitos. Quarentena, warnings e campos nulos continuam visíveis e não são imputados.
