# Runbook: transformar uma geração Data Dump em Silver

O comando lê um manifesto Bronze terminal e publica quatro JSONL. Não baixa dump, não abre a API, não altera Bronze, Gold, watermarks, checkpoints nem as Silvers `silver/questions` e `silver/answers`.

Se o pacote não estiver instalado:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m stackoverflow_vitality.cli transform-data-dump `
  --bronze-generation data/bronze/data_dump/manifests/<ingestion_identity>.json `
  --output-dir data/silver/data_dump `
  --transformation-run-id <run-id> `
  --max-body-chars 500000
```

`--bronze-generation` é o manifesto terminal, não um diretório. Os shards são os nomes declarados nele, sob `site=<site>/g=<ingestion_identity>/`, ao lado de `manifests/`. `--dry-run` valida manifesto, consumo, inventário, checksums, identidade e destinos. Não cria diretório, lock, staging, JSONL nem manifesto.

## Entrada

| Bronze | Efeito |
| --- | --- |
| `completed` e `consumable=true` | transforma |
| `empty` e `consumable=true` | quatro JSONL vazios, status `empty`, `readable=true`, `consumable=true`, saída 0 |
| `partial` ou `consumable=false` | `DS-002`, antes de qualquer escrita Silver, saída 1 |
| manifesto ausente ou sem `published_at` | `DS-001`, saída 1 |
| `failed` | não há geração transformável |

## Saída

```text
<output-dir>/g=<silver_generation_id>/question_content.jsonl
<output-dir>/g=<silver_generation_id>/question_observations.jsonl
<output-dir>/g=<silver_generation_id>/answer_content.jsonl
<output-dir>/g=<silver_generation_id>/answer_observations.jsonl
<output-dir>/manifests/<silver_generation_id>.json
<output-dir>/quarantine/g=<silver_generation_id>/items.jsonl
<output-dir>/.staging/<transformation_run_id>/
<output-dir>/.transform.lock
```

O staging e o lock não são dataset. O manifesto terminal é o último objeto. O manifesto de tentativa, quando a publicação falha depois do lock, fica em `manifests/runs/<transformation_run_id>.json` e não é geração consumível.

`silver_generation_id` é o SHA-256 do JSON canônico de `ingestion_identity`, das quatro versões de contrato, `transformer_version` `1.0.0`, `body_rules` `content_parser.v1`, `tag_rules` `angle_brackets_sorted_unique.v1` e `max_body_chars`. Não inclui tentativa, relógio, caminho, commit nem diretório.

JSONL usa UTF-8, chaves ordenadas, separadores `( ",", ":" )`, `ensure_ascii` e uma quebra `\n` por linha. O checksum de arquivo vazio é o SHA-256 de zero bytes. Replay dos mesmos bytes não reescreve o manifesto. Bytes diferentes no mesmo `silver_generation_id` geram `DS-015` e o destino permanece como estava.

Saídas: `completed` e `empty` retornam 0; `partial` retorna 3; falha, conflito e lock retornam 1; argumento inválido retorna 2.

## Corpo, tags e aceite

`body_html` e `tags_raw` são copiados. `body_text` e `code_blocks` seguem o parser de conteúdo já usado no repositório. `tags` guarda tokens `<nome>` únicos, em ordem lexicográfica. Resposta não recebe tags.

`is_accepted_in_snapshot` compara a resposta com o `accepted_answer_id` da pergunta emitida no mesmo snapshot. Sem pergunta disponível, o valor fica nulo. Não se cria `accepted_at`.

A ordem publicada é o grão de cada contrato. `source_row_number` não é tempo.

Criação posterior ao corte só rejeita a linha quando `dump_cutoff_status` é `operator_declared` e `dump_cutoff` está preenchido. `unknown` não autoriza essa comparação nem métrica causal.

Duplicata idêntica do mesmo `post_id` conserva a primeira linha. Duplicata divergente aborta com `DS-009`, sem escolher vencedor.

## Qualidade DS-*

| Código | Severidade | Efeito na linha | Efeito na execução | readable | consumable |
| --- | --- | --- | --- | --- | --- |
| DS-001 manifesto ausente ou não terminal | erro | nenhum | recusa antes de escrever, saída 1 | false | false |
| DS-002 Bronze não consumível, inclusive `partial` | erro | nenhum | recusa antes de escrever, saída 1 | false | false |
| DS-003 esquema incompatível | erro | aborta | `failed`, sem geração, saída 1 | false | false |
| DS-004 checksum, contagem, bytes ou nome de shard | erro | aborta | `failed`, sem geração, saída 1 | false | false |
| DS-005 JSON inválido | erro | aborta | `failed`, sem geração, saída 1 | false | false |
| DS-006 `source_kind`, site ou identidade divergente | erro | aborta | `failed`, sem geração, saída 1 | false | false |
| DS-007 `post_type` ou identificador inválido | erro | descarta a linha | `partial`, saída 3 | true | false |
| DS-008 resposta sem `parent_id` | erro | descarta a linha | `partial`, saída 3 | true | false |
| DS-009 duplicata conflitante | erro | aborta, sem vencedor | `failed`, sem geração, saída 1 | false | false |
| DS-010 timestamp inválido na linha | erro | descarta a linha | `partial`, saída 3 | true | false |
| DS-010 timestamp inválido no manifesto | erro | aborta | `failed`, sem geração, saída 1 | false | false |
| DS-011 criação posterior ao corte declarado | erro | descarta a linha | `partial`, saída 3 | true | false |
| DS-012 corpo acima de `max_body_chars` | erro | descarta a linha | `partial`, saída 3 | true | false |
| DS-013 resposta sem a pergunta no snapshot | aviso | conserva; aceite nulo | não força `partial` | true se `completed` | true se `completed` |
| DS-014 `accepted_answer_id` sem resposta no snapshot | aviso | conserva a pergunta | não força `partial` | true se `completed` | true se `completed` |
| DS-015 replay com bytes divergentes | erro | não substitui | `failed`, saída 1 | o que já existia | o que já existia |
| DS-016 falha de publicação | erro | sem manifesto terminal | `failed`, saída 1 | false | false |
| DS-017 lock ocupado | erro | não escreve | `failed`, saída 1 | false | false |

A quarentena registra código, severidade, `post_id` e campo. Não grava corpo, HTML nem caminho absoluto. Aviso junto de rejeição deixa a execução `partial`, porque a rejeição manda no status.

## Falha operacional

Lock órfão não é apagado pelo comando. O operador confirma que não há escritor ativo e remove `.transform.lock`. Staging restante não é consumível. Geração sem manifesto terminal não é consumível. Não há recuperação automática nem substituição de arquivo divergente.
