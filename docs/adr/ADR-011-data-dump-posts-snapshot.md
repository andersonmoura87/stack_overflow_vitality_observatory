# ADR-011: Snapshot local de Posts.xml como Bronze próprio

- **Status:** accepted
- **Fase:** 5.0 definiu o desenho. A Fase 5.1 implementa o Bronze offline com `ingest-data-dump`. A Fase 5.2 publica a Silver própria do dump; ver [ADR-012](ADR-012-data-dump-silver-history.md). Não há download nem modo completo.

## Contexto

A API observa o sítio desde o início da coleta. Ela não reconstrói o passado anterior a essa coleta. O Data Dump publica um `Posts.xml` de um corte: os posts incluídos naquela publicação, no estado daquele corte. Não é um log de criações, edições, aceites ou exclusões.

O Bronze da API usa `source=stackexchange_api`. Páginas de perguntas são um objeto JSON publicado por `publish`. JSONL de respostas e da Silver usa `write_immutable`. Os dois exigem hard link, comparam bytes e não substituem destino divergente. Nenhum dos dois oferece staging privado, manifesto preparado separado do diretório consumível e promoção de geração. `exclusive_lock` cria um diretório com `mkdir`, falha na hora se ele existe e não tem prazo. O coletor não remove lock órfão. A leitura da Silver de perguntas recusa symlink no próprio leitor; `publish` e `write_immutable` não recusam symlink na origem.

Reutilizar o contrato ou o caminho da API apagaria `source_kind` e a diferença entre observação operacional e corte publicado.

## Decisões preservadas

1. A Fase 5.1 lê somente um `Posts.xml` local, não comprimido, no caminho informado pelo operador. Não há download, URL remota nem shell para interpretar ou extrair a fonte. Arquivo compactado fica fora.
2. O parser é `xml.etree.ElementTree.iterparse`, um `row` por vez, com `clear` do elemento e da raiz após cada linha. O arquivo não entra inteiro na memória. Antes do parser, a leitura percorre o arquivo inteiro, com sobreposição entre blocos, e recusa `<!DOCTYPE` e `<!ENTITY`. Entidade externa não é resolvida. Se isso não puder ser confirmado, a execução falha fechada. Checksum, parse e checksum final usam o mesmo descritor aberto depois de `lstat` e `fstat`.
3. O modo inicial é restrito: perguntas cujo `Id` está na seleção e respostas cujo `ParentId` está na seleção. O XML inteiro é lido, porque a resposta pode vir antes da pergunta. Post fora da seleção é irrelevante, não erro. O modo completo fica adiado.
4. O contrato é `bronze_stackexchange_data_dump_posts.v1`, com `source` e `source_kind` iguais a `stackexchange_data_dump`. O Bronze da API não é migrado.
5. Não há watermark nem checkpoint de posts. A execução não avança cursor de perguntas nem de respostas.
6. A Fase 5.1 não escreve Silver, Gold nem os datasets já existentes. Não calcula tempo até resposta, sobrevivência, ranking ou efeito causal.

## Identidade

Três nomes distintos:

| Nome | Papel |
|---|---|
| `source_identity` | SHA-256 canônico de `site`, SHA-256 da fonte e tamanho. Não inclui caminho nem nome. |
| `ingestion_identity` | SHA-256 canônico da fonte mais o que muda o dataset publicado. |
| `run_id` | UUID operacional de uma tentativa. Não é a chave de idempotência. |

A função canônica é a de `canonical`: JSON UTF-8, chaves ordenadas, separadores compactos, `ensure_ascii` e sem NaN. `source_identity` e `ingestion_identity` são o SHA-256 dessa serialização. O objeto de `source_identity` tem `site`, `source_sha256` e `source_file_size`. O de `ingestion_identity` tem `source_identity`, `contract_version`, `parser_version`, `mode`, `selection_sha256`, `max_attribute_bytes`, `max_body_chars`, `max_shard_records`, `max_shard_bytes`, `dump_publication_id`, `dump_cutoff` e `dump_cutoff_status`. `selection_sha256` é o SHA-256 do array JSON dos IDs únicos, ordenados antes do hash. A ordem informada não muda o valor.

Esses limites entram porque mudam a aceitação da linha ou os bytes de cada shard. Não entram prazo de lock, frequência de log, teto de RSS, reserva de disco, nível de log, diretório de entrada nem `run_id`. Um teto que só aborta a tentativa, como tamanho máximo da fonte, máximo de registros ou máximo de saída, também fica de fora: duas execuções que publicam o mesmo conteúdo não viram duas gerações.

| Situação | Resultado |
|---|---|
| Mesma identidade e os mesmos bytes de shard | replay: não reescreve a geração nem o manifesto |
| Tentativa anterior sem manifesto terminal | nova tentativa com outro `run_id`; a leitura recomeça no início do XML |
| Mesma fonte e outra seleção, outro limite semântico ou outro corte | outra `ingestion_identity` e outra geração |
| Outra versão de parser ou de contrato | outra geração; a anterior permanece |
| Outro conteúdo com o mesmo nome | outro SHA-256 e outra `source_identity` |
| Os mesmos bytes em outro caminho | a mesma `source_identity` |
| Geração existente com bytes divergentes | conflito; o objeto antigo permanece |

O diretório da geração usa o hex completo de `ingestion_identity`. O hash da fonte sozinho não entra no path: ele não distingue seleção nem parser. O hex completo cabe no orçamento abaixo e evita colisão de prefixo.

```text
data_dump/site=<site>/g=<64 hex>/shard=NNNN.jsonl
data_dump/manifests/<64 hex>.json
data_dump/site=<site>/.staging/<run_id>/
data_dump/manifests/runs/<run_id>.json
quarantine/data_dump/g=<64 hex>/items.jsonl
```

`site` não contém separador, `..` nem byte nulo. O caminho resolvido permanece sob a raiz configurada. O relativo `data_dump/...` mais um site de até 32 caracteres permanece abaixo de 180 caracteres. Acima disso, a execução falha fechada.

O manifesto persistido não guarda caminho absoluto. Guarda o nome-base sanitizado, tamanho e SHA-256. O caminho absoluto existe só na memória do processo. Log e artefato não o imprimem. Caminho relativo só entra se o operador pedir e o arquivo estiver sob a raiz de entrada declarada, sem `..`. O padrão é omiti-lo.

## Tempos

| Campo | Semântica | Onde |
|---|---|---|
| `source_created_at` | `CreationDate` do post, se o texto for aceito | linha |
| `dump_cutoff` | corte declarado ou, no futuro, metadado conferido | manifesto |
| `dump_cutoff_status` | `operator_declared`, `unknown` ou `verified` | manifesto |
| `source_file_observed_at` | início da inspeção que mede a fonte | manifesto, só na publicação original |
| `run_started_at` | aquisição do lock da tentativa | manifesto de tentativa ou o manifesto original |
| `published_at` | publicação terminal do manifesto da geração | manifesto da geração |
| `transformation_run_id` | transformação futura | nulo na Fase 5.1 |

`published_at` não substitui criação, corte, inspeção do arquivo nem início da execução. `ingested_at` não é usado: seria um segundo nome para a publicação ou para o início. Relógios de uma tentativa repetida vão para o evento de replay, não para um manifesto novo.

Datas do dump sem fuso só viram UTC quando o texto casa `YYYY-MM-DDTHH:MM:SS` com fração opcional. Fora disso, a linha é rejeitada. A convenção não prova o fuso original.

`dump_cutoff_status=verified` só poderá ser emitido quando um metadado local, com checksum próprio, trouxer o corte e a implementação conferir esse metadado. A Fase 5.1 não consulta a Stack Exchange e não emite `verified`.

| Situação do corte | Efeito |
|---|---|
| Informado e válido | `operator_declared`; o Bronze pode ser publicado |
| Ausente | `unknown`; o Bronze pode ser publicado |
| Inválido, ou posterior a `source_file_observed_at` | erro de configuração, antes da geração |
| Diferente do identificador de publicação | os dois campos permanecem; o identificador não é uma data e não é confrontado |

Reconciliação temporal e métrica histórica recusam `unknown` e `operator_declared` não revisado. Nenhuma das duas trata essa fonte como temporalmente determinada. O identificador de publicação não substitui o corte.

## Unidade de publicação

Envelope JSON por lote usaria `publish`, que exige um único objeto JSON e confirma o arquivo inteiro. Um lote grande deixaria de ser streaming e repetiria o custo de memória que o parser evita. Parquet, Pandas, Spark e DuckDB ficam fora: não há dependência nova e a migração posterior não muda a chave lógica.

A unidade escolhida é JSONL em shards:

- UTF-8, sem BOM, terminador `\n`, inclusive na última linha;
- um objeto por linha, chaves ordenadas, separadores compactos, `ensure_ascii` e sem NaN, como `canonical`;
- a ordem dos shards é a ordem em que os registros são emitidos durante a leitura do `Posts.xml`;
- cada `<row>` lido recebe `source_row_number`, crescente a partir de 1, inclusive quando a linha é ignorada ou rejeitada;
- o arquivo não é tratado como ordenado por ID;
- no máximo 5.000 registros e 64 MiB por shard; o último pode ser menor e não é vazio;
- nome `shard=NNNN.jsonl`, seis dígitos, a partir de 000001, sem buracos;
- SHA-256 e contagens de cada shard no manifesto;
- seleção vazia não cria shard; o manifesto `empty` é a publicação.

A mesma fonte, a mesma configuração semântica e a mesma versão do parser produzem os mesmos shards e checksums. Não há ordenação externa nem materialização integral para reordenar. A ordem por identificador ou por tempo fica para a Silver histórica. O modo completo continua adiado pelo tamanho da saída, não por uma ordenação.

## Publicação e crash

`publish` não serve para JSONL: `matches` exige um objeto JSON. `write_immutable` grava no destino final, sem staging nem manifesto preparado. Sozinho, ele deixaria shards visíveis antes do commit. A Fase 5.1 precisa de um publicador próprio do dump, reutilizando hard link, `fsync` e comparação de bytes. Ele não chama `publish` para shard.

```text
staging privado da tentativa
    -> shards confirmados por releitura
    -> manifesto preparado só dentro do staging
    -> hard link para a geração
    -> manifesto terminal
    -> limpeza do staging
```

Temporários nascem no mesmo volume do destino, senão o hard link falha e a execução para. Não há fallback que copie por cima. Antes do manifesto terminal, glob de `shard=*.jsonl` na geração não é dataset. Consumo automático lê os shards nomeados no manifesto `completed` ou `empty`. O manifesto `partial` permanece legível para inspeção, com `consumable=false`, e a Silver automática futura o recusa por padrão. `completed_with_warnings` e `truncated` não fazem parte desta fase. A CLI devolve 0 para sucesso operacional, 1 para falha e 3 para conclusão parcial. O código 2 continua com o argparse.

Crash antes do manifesto terminal não publica a geração. Outra tentativa usa outro `run_id`, relê o XML desde o início e só promove se os bytes conferem. Crash depois do manifesto deixa a geração publicada; staging restante é redundante e a limpeza não apaga a geração. Manifesto de falha ou cancelamento fica em `manifests/runs/<run_id>.json` e não ocupa a chave da geração. Divergência na geração existente é conflito e não substitui o arquivo.

O manifesto terminal é o último artefato. `published_at` é o instante dessa escrita, depois da confirmação dos shards.

## Retomada

Não há retomada. Offset de byte em XML não é seguro: a fronteira do elemento não é um offset estável. Checkpoint por número de `row` ou por shard confirmado ainda exigiria provar que o arquivo não mudou e que o prefixo produz os mesmos registros. A Fase 5.1 não faz essa prova.

Uma interrupção relê o arquivo desde o começo. Staging impede consumo de shard incompleto. A geração já publicada continua replay idempotente. O custo é duas leituras de checksum e uma de parse, repetidas a cada tentativa. A estratégia deixa de ser aceitável quando o operador precisa analisar um arquivo maior que o limite padrão, ou quando uma tentativa completa deixa de caber na janela operacional. Aí a fase seguinte desenha checkpoint, não um offset informal.

## API e Data Dump

Não há precedência global. As duas fontes permanecem. A comparação abaixo é regra da reconciliação futura; a Fase 5.1 não a executa e não emite DD-018.

| Campo ou evento | Data Dump | API | Regra |
|---|---|---|---|
| identidade `site + post_id` | snapshot | observação | igualdade obrigatória do par; valores divergentes não são mesclados |
| criação | valor publicado | valor retornado | conflito explícito; nenhuma fonte é apagada |
| conteúdo | estado no corte | versões observadas | preservar a origem de cada versão |
| score | estado no corte | série observada | o dump não fabrica pontos intermediários |
| aceite | estado no corte | observações | não inferir o instante |
| atividade | estado publicado | observações | não tratar atividade como edição |
| exclusão | publicação incompleta | ausência não prova exclusão | não inferir exclusão |

Snapshot que chega depois de observações recentes não reescreve a Silver da API. Discordância fica registrada, com as duas proveniências. Deduplicação só ocorre quando identidade, hash de conteúdo e origem são os mesmos.

## Silver

A alternativa A, escrever nos datasets Silver atuais, mistura corte e observação e pode alterar a história já usada pela Gold. A alternativa C, uma camada canônica agora, exige a reconciliação que esta fase ainda não tem. A alternativa B é a escolhida: uma Silver histórica só do Data Dump, reconciliada depois com a Silver da API. A Fase 5.1 não cria essa Silver. A Fase 5.2 a publica em `transform-data-dump`, como observações discretas de um snapshot, sem intervalos e sem reescrever a Silver da API. Métrica histórica causal continua bloqueada enquanto `dump_cutoff_status` não for utilizável pelo contrato da reconciliação. `unknown` e `operator_declared` continuam sendo o que o manifesto Bronze gravou; esta fase não emite `verified`.

## Qualidade, observação e limites

A taxonomia `DD-*`, os shards, os tempos, os limites e o manifesto estão no [contrato](../data-contracts/bronze_stackexchange_data_dump_posts.md). O procedimento está no [runbook](../runbooks/data-dump-posts.md).

Eventos estruturados, sem corpo, sem XML e sem caminho absoluto: `dump_start`, `dump_source_validated`, `dump_checksum`, `dump_progress`, `dump_rows_read`, `dump_questions_selected`, `dump_answers_selected`, `dump_ignored`, `dump_rejected`, `dump_quarantine`, `dump_shard_prepared`, `dump_shard_confirmed`, `dump_generation_published`, `dump_replay`, `dump_conflict`, `dump_limit`, `dump_finished`, `dump_failed`.

Métricas: bytes lidos, bytes gravados, registros por segundo, tempo decorrido, pico de memória, RSS quando a plataforma o expuser, contagem por tipo, contagem por código, quantidade e tamanho dos shards. Progresso a cada 100.000 linhas lidas. A memória de trabalho é a linha corrente, o shard aberto e o mapa dos IDs já emitidos. Um teto de RSS, se existir, só protege o processo e não muda a geração.

## CLI

O comando `ingest-data-dump`, com `--dry-run`, existe desde a Fase 5.1. Um comando `inspect-data-dump` separado repetiria a validação. A precedência é CLI, ambiente `STACKEXCHANGE_DUMP_*`, YAML, padrão. Não há segredo nesse documento.

Argumentos: `--source`, `--site`, `--publication-id`, `--dump-cutoff`, `--question-ids-file`, `--output-dir`, `--dry-run` e os limites do contrato. `--publication-id` e o arquivo de IDs são obrigatórios. Corte ausente é `unknown`. Corte inválido, caminho com byte nulo, seleção duplicada ou limite contraditório falham antes da leitura. `--record-relative-path` é opt-in.

O dry-run valida configuração e fonte e informa que a execução real fará o checksum completo. Sem esse checksum ele não calcula `source_identity` nem `ingestion_identity`. A saída traz `source_identity_status=pending_source_checksum` e `ingestion_identity_status=pending_source_checksum`. Não cria diretório, geração, lock nem manifesto.

## Testes da Fase 5.1

Unidade: configuração e precedência; `source_identity` e `ingestion_identity`; arquivo regular, diretório e byte nulo; XML mínimo; campos e booleanos; filtro; ordem e shard; cada `DD-*` da ingestão; limites; replay; conflito; lock ocupado e liberado após exceção.

Contrato: linha Bronze, manifesto, proveniência sem caminho absoluto, nulos, tempos, determinismo da ordem de leitura e códigos `DD-*`.

Integração offline, com XML sintético: pergunta antes da resposta; resposta antes da pergunta; posts fora do filtro; XML malformado; fonte instável; crash antes do manifesto; repetição; replay; conflito; dry-run; manifesto por último.

Segurança: DTD; entidade externa; symlink; no Windows, reparse point ou a recusa quando o atributo não puder ser lido; diretório; arquivo especial; byte nulo; corpo excessivo; limite de saída; `site` com `..`. Nenhuma rede e nenhum dump real.

## Benchmark futuro

Harness separado, com XML sintético de 1.000, 10.000, 50.000 e 100.000 registros. Mede geração do arquivo, checksum, parse, filtro, validação, serialização, persistência, manifesto, throughput, memória, RSS quando existir, bytes de entrada, bytes de saída e amplificação. Não usa dump real e não entra no critério funcional da Fase 5.1.

## Plano da Fase 5.1

1. Implementar validação de caminho, arquivo regular, byte nulo, symlink ou reparse conforme a plataforma, espaço livre e limites. Falha fechada quando a garantia não for confirmada.
2. Implementar identidades, seleção e o publicador com staging. Não usar `publish` para JSONL.
3. Implementar o parser restrito, a emissão na ordem de leitura, os shards e os códigos `DD-*` da ingestão. DD-018 fica como função pura, sem varrer o Bronze da API.
4. Publicar o manifesto terminal por último. Replay não o reescreve. Falha grava só o manifesto da tentativa.
5. Implementar `ingest-data-dump` e o dry-run. Nenhuma rede e nenhum dump real nos testes.
6. Cobrir a matriz de testes deste ADR com fixtures pequenas. O harness de benchmark permanece separado e não é executado como critério desta fase.

## Consequências

A cobertura histórica fica limitada à seleção e ao que a publicação incluiu. A geração é reconhecível sem depender de um UUID. Crash antes do manifesto não parece sucesso. A API e o dump convivem sem que um apague o outro.

## Riscos

Checksum duplo custa duas leituras integrais. Reler após crash repete esse custo. `operator_declared` pode estar errado e ainda assim publicar Bronze. XML hostil, caminho no limite do Windows e lock órfão continuam possíveis. A proteção de junction não é `O_NOFOLLOW`.
