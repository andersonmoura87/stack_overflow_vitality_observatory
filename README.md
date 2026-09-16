# Stack Overflow Vitality Observatory

Observatório local-first para investigar mudanças nas funções de descoberta, aprendizagem, documentação, validação e resolução de problemas no Stack Overflow após a popularização da IA generativa. Associação temporal não é apresentada como causalidade.

## Funcionalidades

A implementação oferece coleta incremental de perguntas por tag, cliente HTTP com retries e backoff, CLI configurável, snapshots Bronze imutáveis, confirmação de integridade, manifestos de execução e watermark com compare-and-set local. Backfill preserva o estado operacional; dry-run permite conferir a configuração sem acesso à rede.

Silver, Gold, NLP, respostas/comentários, Dump, Survey, dashboard e AWS não estão implementados. Terraform não possui recursos. Nenhuma coleta real é necessária para testes.

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
- [Contrato Bronze](docs/data-contracts/bronze_stackexchange_questions_snapshot.md), [watermarks](docs/data-contracts/collection_watermarks.md) e [execuções](docs/data-contracts/ingestion_runs.md).
- [Perguntas de pesquisa](docs/research_questions.md), [metodologia](docs/methodology.md) e [data card](docs/data_card.md).
- [Recuperação de falhas parciais](docs/runbooks/partial-write.md).

## Roadmap público

1. Silver: normalização de perguntas, separação de HTML/texto/código e deduplicação por entidade e versão.
2. Métricas observacionais: atividade por período/tag e concentração das contribuições.
3. Respostas e resolução: tempo até resposta, aceite e análise com censura.
4. Histórico e contexto: Data Dump e Developer Survey como fontes independentes.
5. NLP avaliado: sinais de IA, tópicos e complexidade técnica do conteúdo.
6. Consumo analítico: Gold, dashboard e relatórios reproduzíveis.
7. Operação AWS: adaptadores, observabilidade e infraestrutura após definição de custos e controles.

Esses itens são planejados; o pipeline disponível termina na Bronze local. A análise não pressupõe declínio ou substituição da plataforma.
