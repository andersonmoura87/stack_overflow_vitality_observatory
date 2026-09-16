# ADR-005: Sentimento é secundário

- **Status:** accepted

## Contexto
VADER interpreta `error`, `failed`, `exception` e termos técnicos como negatividade, confundindo linguagem de código com satisfação.

## Decisão
Sentimento é apenas experimento exploratório. Vitalidade prioriza atividade, resolubilidade, resposta, sobrevivência, complexidade estrutural, tópicos, sinais de IA e concentração.

## Alternativas
Usar sentimento como qualidade/maturidade; rejeitada por validade de construto insuficiente. Remover toda análise; adiado, não necessário.

## Consequências
Qualquer uso futuro exigirá validação de domínio e análise de erro.

## Riscos
Leitores podem superinterpretar score; relatórios devem explicitar limitações.
