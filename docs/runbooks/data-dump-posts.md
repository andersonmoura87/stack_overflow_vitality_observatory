# Runbook: Posts.xml local

O comando é `ingest-data-dump`. Ele lê um `Posts.xml` local e não comprimido e publica o Bronze `bronze_stackexchange_data_dump_posts.v1`. Não baixa o dump e não escreve Silver.

```powershell
python -m stackoverflow_vitality.cli ingest-data-dump `
  --config configs/data_dump.yml `
  --source C:\dados\Posts.xml `
  --question-ids-file questions.json `
  --publication-id 2026-03 `
  --dry-run
```

Não baixe o dump por este repositório. Não versione `Posts.xml`, compactado ou extração. `data/raw/`, `data/bronze/`, `data/quarantine/` e `data/local/` ficam fora do Git.

## Antes de executar

O caminho é um arquivo regular local. O processo não grava esse caminho absoluto no manifesto nem no log. Informe site, identificador da publicação e, se conhecido, a data de corte em UTC. Corte ausente deixa `dump_cutoff_status=unknown`. Corte inválido ou posterior à inspeção é erro de configuração. Não deduza o corte pela maior data do arquivo e não trate o identificador da publicação como data.

A seleção lista perguntas. Respostas entram pelo `ParentId`. Post fora da seleção não é erro. Não há modo completo.

Uma execução real lê o checksum completo antes e depois, no mesmo descritor do parse. O dry-run percorre a fonte só para recusar DTD ou entidade e não calcula esse checksum. A saída do dry-run contém:

```text
source_identity_status=pending_source_checksum
ingestion_identity_status=pending_source_checksum
```

Ele não imprime as identidades finais. Não cria diretório, geração, lock nem manifesto.

Os shards seguem a ordem de leitura do XML. Não estão ordenados por ID. A Silver histórica é quem poderá ordenar. Frequência de log, reserva de disco, diretório de entrada e `run_id` não criam outra geração.

## Fonte recusada

A execução para sem manifesto de geração. Causas: caminho ausente, diretório, arquivo especial, byte nulo, symlink, DTD, XML malformado, tamanho acima do limite ou identidade de arquivo indisponível. A busca por `<!DOCTYPE`, `<!ENTITY`, `<!ELEMENT`, `<!ATTLIST` e `<!NOTATION` percorre o arquivo inteiro. A sobra entre blocos é o tamanho do maior token menos um, calculado dessa coleção. No Windows, junction e reparse point dependem do atributo do sistema. Se a consulta falhar, a recusa é o resultado correto. Essa recusa não é `O_NOFOLLOW`.

## Interrupção

Não há retomada no meio do XML. A tentativa seguinte relê o arquivo desde o início, com outro `run_id`. Diretório `.staging/<run_id>` não é dataset. Sem `data_dump/manifests/<ingestion_identity>.json`, os shards não são consumo, mesmo que já tenham sido ligados na geração.

Manifesto `failed` ou `cancelled` fica em `manifests/runs/<run_id>.json` e não ocupa a geração. Staging órfão pode ser removido só quando não houver lock ativo. Não apague uma geração que já tenha manifesto terminal.

Lock existente em `data_dump/site=<site>/.ingest.lock` faz a nova tentativa falhar na hora. Não há prazo automático. Remoção exige confirmar que não há escritor ativo.

## Fonte instável e duplicata

DD-019: checksum ou tamanho final diferente. Não promova a tentativa. Repita com arquivo estável.

DD-017: o mesmo post diverge, ou o mesmo ID aparece como pergunta e resposta. Não escolha uma cópia para publicar sucesso.

## Cobertura

DD-008 deixa o manifesto `partial`: a resposta foi gravada e a pergunta não está no arquivo. O manifesto diz `readable=true` e `consumable=false`. Os shards ficam para inspeção e recuperação. A saída do comando é 3, não o sucesso operacional de `completed`. A Silver automática futura recusa esse manifesto por padrão. Isso não prova exclusão nem fornece o instante da primeira resposta. Não calcule tempo até resposta. `dump_cutoff_status=unknown` também não autoriza métrica histórica, ainda que o status da ingestão seja `completed`.

`empty` é sucesso, com código 0, zero shards e manifesto terminal. `failed`, inclusive limite excedido, não publica manifesto de geração. `completed_with_warnings` e `truncated` não são status desta fase. Warning de dono ou licença, sozinho, permanece `completed`.

DD-014 e DD-015 são warnings. Sozinhos, não impedem `completed`. O dono e a licença permanecem nulos.

## Replay e conflito

A mesma fonte, seleção, parser, contrato e limites reconhecem a geração pelo `ingestion_identity`. Bytes iguais são replay e o manifesto antigo permanece, inclusive `published_at`. Bytes diferentes são conflito e o objeto existente fica no lugar.

## O que não muda

Watermark de perguntas, checkpoint de respostas, Silver, Gold e Bronze da API. `published_at` não é data do post. DD-018 não é avaliado nesta ingestão; o manifesto diz `api_comparison=skipped`.
