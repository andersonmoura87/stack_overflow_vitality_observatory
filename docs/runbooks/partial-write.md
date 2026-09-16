# Runbook: falha parcial

1. Consultar status, `failure_stage`, `errors` e run_id no retorno/logs; mensagens de exceções não são persistidas.
2. Antes do CAS, falha deve preservar watermark; páginas confirmadas podem permanecer e terminal deve indicar failed/truncated. Se a própria publicação terminal falhar, não assumir que existe manifesto.
3. `prepared.json` sem terminal pode indicar interrupção antes ou depois do CAS. Comparar janela, páginas/checksums e estado corrente. Não concluir sucesso somente pela existência de prepared.
4. Depois do CAS, não retroceder watermark automaticamente. Reexecutar o intervalo como backfill explícito com novo UUID; registrar decisão de recuperação. Para replay pela porta Python, fornecer envelopes verificados a `replay_pages`; contexto/parâmetros precisam coincidir, e o instante original é preservado.
5. Cada nova coleta recebe novo run_id. Não tentar substituir manifesto terminal anterior. Objetos idênticos podem ser reapresentados ao store sem reescrita.
6. Temporários não são páginas confirmadas. Remover resíduos somente após confirmar que nenhum processo os utiliza, preservando evidências. Não há limpeza automática nesta fase.

Filesystem cheio, falta de permissão e ausência de suporte a hard links são falhas explícitas. Não existe transação atômica entre Bronze, manifesto e watermark.
