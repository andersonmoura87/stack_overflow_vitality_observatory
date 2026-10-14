# Stack Overflow Vitality Observatory

Observatório local-first para investigar mudanças nas funções de descoberta, aprendizagem, documentação, validação e resolução de problemas no Stack Overflow após a popularização da IA generativa. Associação temporal não é apresentada como causalidade.

## Funcionalidades

A implementação oferece coleta incremental de perguntas por tag, cliente HTTP com retries e backoff, CLI configurável, snapshots Bronze imutáveis, confirmação de integridade, manifestos de execução e watermark com compare-and-set local. Backfill preserva o estado operacional; dry-run permite conferir a configuração sem acesso à rede.

A transformação Bronze → Silver de perguntas inclui contrato versionado, limpeza de HTML/texto/código, quarentena, histórico de conteúdo, observações de métricas e linhagem. A Gold v1 publica indicadores descritivos mensais de perguntas, por site e por tag, a partir dessa Silver. NLP, respostas e comentários como entidades, tempo até resposta, Dump, Survey, dashboard e AWS não estão implementados. Terraform não possui recursos. Nenhuma coleta real é necessária para testes.

## Desenvolvimento

O projeto declara Python >=3.12. Para preparar um ambiente, quando necessário:

```powershell
uv sync --extra dev
```

Com o ambiente já preparado:

```powershell
uv run python -m ruff check .
uv run python -m ruff format --check .
uv run python -m mypy src
uv run python -m pytest -q
uv run python -m pytest --cov=src --cov-report=term-missing
```

O CI usa Python 3.12 e executa lint, formatação, tipagem e testes. Os testes bloqueiam sockets reais e usam MockTransport, relógio e sleeper injetáveis; não exigem API key ou credenciais AWS. A dependência YAML é PyYAML safe_load; `stubs/yaml` descreve apenas a interface usada pelo projeto.

## CLI e configuração

Execute da raiz do repositório. Caso o pacote ainda não esteja instalado, exponha `src` nesta sessão PowerShell:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m stackoverflow_vitality.cli collect-questions `
  --config configs/collection.yml `
  --tag python `
  --from 2026-09-01T00:00:00Z `
  --to 2026-09-02T00:00:00Z `
  --max-pages 2 `
  --output data/bronze `
  --dry-run
```

O dry-run valida e mostra somente configuração pública; não consulta watermark, cria diretórios, acessa rede ou espera. A janela exibida é a solicitada; a janela operacional efetiva é calculada após ler o watermark na execução real. Remover `--dry-run` executa coleta real na Stack Exchange API.

Precedência: defaults < arquivo YAML < ambiente permitido < CLI. `--config` aponta para outro arquivo; arquivo ausente é erro. `tags` lista candidatos; cada execução seleciona uma única tag, sem OR implícito. Site, endpoint, filtro, page-size, max-pages, timeout, retries, jitter, overlap, User-Agent, output e backfill são configuráveis. `--help` lista opções.

Ambiente permitido: `STACKEXCHANGE_SITE`, `STACKEXCHANGE_TAG`, `STACKEXCHANGE_USER_AGENT`, `STACKEXCHANGE_OUTPUT`. API key opcional vem separadamente de `STACKEXCHANGE_API_KEY`; nunca é incluída no YAML, dry-run ou manifestos. Tipos inválidos e chaves desconhecidas falham antes da rede.

Backfill histórico: acrescentar `--backfill` aos argumentos; preserva a janela explícita e não altera watermark operacional. Intervalo operacional anterior ao estado confirmado falha. `--no-backfill` sobrescreve configuração de backfill do YAML.

Respostas usam outro documento, `configs/answers.yml`, e não alteram a coleta de perguntas. O dry-run abaixo só resolve lotes:

```powershell
python -m stackoverflow_vitality.cli collect-answers `
  --config configs/answers.yml `
  --question-ids-file data/local/question_ids.json `
  --dry-run
```

Precedência da coleta de respostas: CLI, YAML, `STACKEXCHANGE_ANSWERS_SITE`, `STACKEXCHANGE_ANSWERS_ENDPOINT`, `STACKEXCHANGE_ANSWERS_USER_AGENT`, `STACKEXCHANGE_ANSWERS_BRONZE`, `STACKEXCHANGE_ANSWERS_SILVER`, default. A chave continua só em `STACKEXCHANGE_API_KEY`. `--mode incremental` avança o checkpoint só dos lotes ainda não concluídos. `--mode refresh` recolhe os lotes pedidos sem mover o checkpoint. A transformação offline:

```powershell
python -m stackoverflow_vitality.cli transform-answers `
  --bronze-path data/bronze `
  --silver-path data/silver `
  --quarantine-path data/quarantine `
  --dry-run
```

O dry-run lê páginas locais, não escreve e não acessa a rede. Contratos e o procedimento estão em [ADR-010](docs/adr/ADR-010-answer-collection-and-history.md) e no [runbook](docs/runbooks/answers.md).

## Garantias locais e limites

Páginas, prepared e manifesto terminal usam o mesmo `RunContext`, sob `site/tag/window_date/run_id`. Publicação via hard link não substitui objeto existente. Replay idêntico não reescreve; divergência falha. A confirmação verifica JSON, tamanho, bytes/metadados e checksum de payload. Exemplo de payload exclusivamente sintético:

```json
{"items":[{"question_id":123,"title":"Synthetic question"}],"has_more":false,"quota_remaining":9999}
```

Watermark inclui fonte, site, endpoint e tag. CAS rejeita retrocesso/conflito sob lock local; backoff da API não é reduzido por limite de retry. Manifesto operacional de sucesso só é publicado após CAS. Cada tentativa usa UUID novo; a porta Python `replay_pages` admite reaproveitamento verificado com proveniência, sem CLI de recuperação automática.

Há uma janela residual entre CAS e terminal: crash pode deixar watermark avançado e apenas prepared. Não há rollback automático, transação distribuída ou proteção contra edição externa. Requer filesystem local com hard links; locks órfãos após crash exigem inspeção manual. Dados antigos não são migrados automaticamente. Detalhes em [ADR-007](docs/adr/ADR-007-local-commit-and-immutability.md) e [runbook](docs/runbooks/partial-write.md).


## Arquitetura e documentação

```text
CLI + YAML -> Stack Exchange API -> páginas Bronze imutáveis
                                -> manifesto preparado
                                -> CAS do watermark
                                -> manifesto terminal
```

- [Arquitetura local](docs/architecture.md) e [decisões de persistência](docs/adr/ADR-007-local-commit-and-immutability.md).
- [Contrato Bronze](docs/data-contracts/bronze_stackexchange_questions_snapshot.md), [watermarks](docs/data-contracts/collection_watermarks.md), [execuções](docs/data-contracts/ingestion_runs.md) e [respostas](docs/data-contracts/bronze_stackexchange_answers_snapshot.md).
- [Perguntas de pesquisa](docs/research_questions.md), [metodologia](docs/methodology.md), [data card](docs/data_card.md) e [contrato Gold](docs/data-contracts/gold_vitality.md).
- [Recuperação de falhas parciais](docs/runbooks/partial-write.md).

## Roadmap público

1. Escala Silver: evolução da persistência e migração versionada, após medição do volume local.
2. Métricas observacionais além dos agregados descritivos da Gold v1.
3. Tempo até resposta e aceite, com censura. A entidade de resposta já tem Bronze e Silver; essas métricas ainda não são calculadas.
4. Histórico e contexto: Data Dump e Developer Survey como fontes independentes.
5. NLP avaliado: sinais de IA, tópicos e complexidade técnica do conteúdo.
6. Consumo analítico: dashboard e relatórios reproduzíveis.
7. Operação AWS: adaptadores, observabilidade e infraestrutura após definição de custos e controles.

A Gold v1 descritiva faz parte do pipeline local. Os demais itens permanecem planejados. A análise não pressupõe declínio ou substituição da plataforma.

## Transformação local de perguntas

Com páginas Bronze locais existentes, execute sem rede:

```powershell
uv run stackoverflow-vitality transform-questions `
  --bronze-path data/bronze `
  --silver-path data/silver `
  --quarantine-path data/quarantine `
  --dry-run
```

Remova `--dry-run` para transformar arquivos locais. `--run-id <UUID>` seleciona uma execução Bronze; sem ele todas as páginas são consideradas. `--max-body-chars 500000` limita o corpo por registro. Também é possível usar `python -m stackoverflow_vitality.cli` com src no PYTHONPATH. O dry-run verifica caminhos, envelopes e checksums; não publica saídas nem substitui a validação de qualidade por item.

Conteúdo e métricas têm históricos separados. Saídas JSONL ficam em `data/silver/questions/generation=<hash>/`; o manifesto em `data/silver/manifests/` é o ponto de commit após confirmação de todas as saídas. A quarentena referencia o item Bronze sem duplicar corpos. Replay não duplica dados; cada tentativa recebe manifesto próprio. Testes criam apenas páginas sintéticas em diretórios temporários.

Consulte o [contrato Silver](docs/data-contracts/silver_questions.md), a [decisão de histórico](docs/adr/ADR-008-silver-history-and-publication.md) e o [runbook de transformação](docs/runbooks/silver-transformation.md). O adaptador reconstrói o histórico em memória; volume grande e armazenamento distribuído exigem evolução explícita.

## Indicadores descritivos

Com uma geração Silver local já publicada:

```powershell
uv run stackoverflow-vitality build-vitality `
  --silver-path data/silver `
  --gold-path data/gold `
  --as-of 2026-09-10T00:00:00Z `
  --dry-run
```

`--as-of` é obrigatório para publicar e deve terminar em `Z`. Sem ele, a execução real falha antes de gravar. O dry-run pode omitir `--as-of` e imprime o maior `collected_at`, marcado como derivado, sem criar arquivos. A saída descreve contagens, distribuições e exposição. Não estima causa, tempo até resposta ou sobrevivência. O contrato está em [gold_vitality.md](docs/data-contracts/gold_vitality.md) e o procedimento em [gold-vitality.md](docs/runbooks/gold-vitality.md).
