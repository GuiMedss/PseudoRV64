# integracao do gerador com o seletor

`codegen.py` esta implementado. `selector.py` e `catalogue.py` continuam com o
colega. o frontend foi recuperado de `Semantic-Analysis-2`, primeiro de
`previous/` e depois da raiz, preservando os carregadores desta etapa.

o gerador chama `Selector(catalogue()).analyze(expressao)` e usa apenas
`SelectionTable.lookup(no, classe)`. cada `Choice` deve guardar a regra escolhida
e os subproblemas na ordem dos pontos `Need` do padrao. os nomes das regras sao
livres; o campo `Rule.action` deve usar os nomes abaixo.

| action | resultado | subproblemas, na ordem | emissao propria |
| --- | --- | --- | --- |
| `literal` | REG | nenhum | Li do inteiro com sinal ou bool |
| `identifier` | REG | nenhum | vreg do simbolo |
| `immediate` | IMM12 | nenhum | inteiro com sinal, sem instrucao |
| `power_of_two` | POW2 | nenhum | expoente, sem instrucao |
| `negate` | REG | operando REG | Neg |
| `add`, `sub`, `mul`, `div`, `rem` | REG | esquerda REG, direita REG | operacao correspondente |
| `addi_right` | REG | esquerda REG, direita IMM12 | Addi |
| `addi_left` | REG | esquerda IMM12, direita REG | Addi |
| `subi` | REG | esquerda REG | Addi com o oposto do literal direito |
| `slli_right` | REG | esquerda REG, direita POW2 | Slli |
| `slli_left` | REG | esquerda POW2, direita REG | Slli |
| `lt`, `gt` | REG | esquerda REG, direita REG | Slt |
| `le`, `ge` | REG | esquerda REG, direita REG | Slt e Seqz |
| `eq`, `ne` | REG | esquerda REG, direita REG | Xor e Seqz/Snez |
| `branch_lt`, `branch_le`, `branch_gt`, `branch_ge`, `branch_eq`, `branch_ne` | COND | esquerda REG, direita REG | branch e Jump |
| `not` | REG | operando REG | Seqz |
| `not_cond` | COND | operando COND | inverte os destinos |
| `and_cond`, `or_cond` | COND | esquerda COND, direita COND | organiza curto-circuito |
| `and_reg`, `or_reg` | REG | esquerda COND, direita COND | curto-circuito, duas Li e Jump |
| `bool_cond` | COND | nenhum | Jump para o destino do literal |
| `identifier_cond` | COND | nenhum | Bnez e Jump |
| `call_reg` | REG | cada argumento REG | Call com destino |
| `call_effect` | EFFECT | cada argumento REG | Call sem destino |
| `call_cond` | COND | cada argumento REG | Call, Bnez e Jump |

`subi` usa um padrao para o literal direito com o predicado `neg_imm12`, sem
`Need` nesse literal. `literal` reconhece inteiros com sinal pelo predicado
`signed_literal`, incluindo a negacao imediata de um literal. esse padrao nao
pede REG ao literal interno; assim o minimo inteiro de 64 bits pode ser emitido
diretamente. lembre que zero tambem e um literal valido ao escrever o predicado.

os predicados, restricoes de tipo e custos sao responsabilidade do catalogo.
chamadas void oferecem somente EFFECT. identificadores e chamadas oferecem COND
somente quando o tipo anotado e bool. as regras logicas pedem COND nos filhos.

os testes de `tests/test_codegen_actions.py` fornecem coberturas explicitas para
testar a emissao sem implementar o seletor. execute:

```sh
python -m pytest -q tests/test_infrastructure.py tests/test_codegen_actions.py
```

depois de implementar o seletor e o catalogo, execute toda a suite publica:

```sh
python -m pytest -q
python runner.py examples/selecao.mc --dump-ir
python runner.py examples/curto_circuito.mc --run
python runner.py examples/integrado.mc --run
```
