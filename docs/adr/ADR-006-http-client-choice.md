# ADR-006: Cliente HTTP da Fase 1

- **Status:** accepted

## Contexto

A Fase 1 precisa de timeout connect/read, retries limitados, transporte substituível e testes offline. Nenhuma biblioteca HTTP estava declarada no `pyproject.toml`.

## Decisão

Usar `httpx` como dependência HTTP. O cliente síncrono recebe `httpx.Client` opcional, permitindo `MockTransport` nos testes, e configura User-Agent e timeout de conexão/leitura explicitamente.

## Alternativas

`requests` é maduro e simples, mas exigiria uma fronteira de transporte própria para reproduzir respostas sem rede. A biblioteca padrão foi rejeitada por aumentar código de parsing, timeout e conexão. Nenhuma dependência foi rejeitada por não atender ao requisito de cliente real.

## Consequências

A Fase 1 ganha uma dependência pequena e uma API síncrona adequada ao CLI. O cliente não é assíncrono e não há pool distribuído; concorrência HTTP fica para fase futura.

## Riscos

Mudanças de schema da API, quota e comportamento de logging de dependência precisam continuar cobertos por testes e política de atualização.

Fase 1.1: PyYAML é dependência de configuração efetiva, com interface tipada local mínima. ADR-007 separa retry local de backoff mandatório.
