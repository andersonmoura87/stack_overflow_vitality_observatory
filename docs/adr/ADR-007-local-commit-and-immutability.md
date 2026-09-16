# ADR-007: Publicação imutável e transação lógica local

- **Status:** accepted

## Contexto

A versão anterior sobrescrevia Bronze, confirmava apenas existência e publicava sucesso antes do watermark. A chave não incluía site. Escritas atômicas isoladas não formam uma transação entre arquivos.

## Decisão

1. `RunContext(run_id, site, tag, window)` determina o diretório `site/tag/window_date/run_id`; a data é o início UTC da janela efetiva, nunca a recuperação. Todas as páginas e os dois manifestos usam essa função. Site/tag recebem percent-encoding para evitar colisões como `c++` e `c##`.
2. Páginas e manifestos são JSON canônico, publicado por hard link do temporário fechado e sincronizado para o destino. A criação do link é exclusiva: um destino existente nunca é substituído. Replay compara tamanho e SHA-256 dos bytes integrais. Conteúdo divergente causa `ImmutableConflict`. A confirmação faz parse, verifica os bytes/metadados e o checksum do payload.
3. Watermark usa `(source, site, endpoint, tag_or_group)`. `update(expected_previous, new)` compara o registro anterior integral e rejeita retrocesso. Um diretório `.lock` adquirido por mkdir protege leitura/comparação/publicação; a substituição atômica fica restrita ao arquivo de estado mutável. O lock é fail-fast, local e não distribuído.
4. Fluxo: planejamento -> coleta -> confirmação de todas as páginas -> `prepared.json` com `pages_persisted` -> CAS do watermark -> `manifest.json` terminal. `completed` e `empty` operacionais exigem CAS concluído. Falha no CAS publica `failed`, com `failure_stage` e tipo de erro, sem mensagem potencialmente sensível.
5. Backfill é explícito e não altera nem limita a janela pelo watermark operacional. A exceção ao CAS para sucesso é identificada por `backfill=true` e `watermark_after=null`.
6. Cada tentativa de coleta usa novo UUID. Replay exato de um envelope é idempotente no store. `collect_questions(replay_pages=...)` permite aproveitar páginas verificadas de uma tentativa anterior, em outra run, preservando recuperação e registrando `replayed_from_run_id`. Exige mesma janela, parâmetros, site, tag e endpoint. Essa porta Python não tem comando CLI de recuperação automática.
7. Retry HTTP limita apenas o atraso local com jitter. O atraso da API nunca é cortado. HTTP cuida de respostas de erro e o coletor consome backoff de payload válido antes de persistir, inclusive na última página, usando sleeper real. Testes injetam sleeper falso.
8. YAML usa PyYAML `safe_load`; defaults < YAML < ambiente permitido < CLI. Não há segredo em YAML. Um stub local estreito descreve somente a interface PyYAML utilizada; valores desserializados são validados em runtime.

## Alternativas

- Sobrescrever página por rename/replace: rejeitado, quebra imutabilidade.
- Publicar manifesto de sucesso antes do CAS: rejeitado, produz sucesso falso.
- SQLite: alternativa futura para transação e locks mais sofisticados, não necessária ao armazenamento mínimo por arquivos.
- Lock com expiração automática: rejeitado, pode remover lock de processo ativo e permitir perda de atualização.
- Formato novo de configuração sem YAML: rejeitado, romperia o contrato de configuração YAML.

## Consequências e limites reais

O suporte pressupõe filesystem local com mkdir exclusivo, hard links e rename atômicos (por exemplo NTFS). Filesystem sem hard link falha explicitamente; não há fallback que sobrescreva Bronze. Compartilhamentos de rede e escritores que ignorem o protocolo não são suportados.

Crash pode deixar temporário, lock órfão ou `prepared.json` sem terminal. Se ocorrer depois do CAS, o watermark pode estar avançado sem manifesto terminal. O coletor não desfaz esse avanço nem afirma atomicidade entre arquivos; a recuperação exige inspeção de páginas/prepared/watermark. Se falhar a própria escrita do manifesto de erro, logs/retorno são a evidência disponível. Uma confirmação terminal que falhe após publicação pode retornar falha mesmo com terminal no disco: o watermark já foi confirmado nesse caso.

`fsync` do arquivo não é promessa de durabilidade do diretório contra perda de energia. A integridade é verificada durante a execução; corrupção ou edição externa posterior não é impedida por permissões do SO nesta implementação.

Estados antigos de watermark sem site/endpoint são rejeitados; não se infere identidade. Bronze antiga permanece preservada e não é migrada automaticamente. Recoleta usa nova run; replay preserva o instante original e não significa atualização da fonte. Backoffs entre processos independentes não são coordenados.

## Riscos

Recuperação manual após crash; ausência de diário transacional compartilhado; crescimento de snapshots; API mutável entre páginas; resoluções futuras de dependências podem mudar, pois não há lockfile Python versionado. Silver continua fora do escopo.
