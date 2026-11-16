"""regras e custos pedidos no enunciado."""

import load_previous
from ast_nodes import (
    BinaryExpr, BinaryOperator, BoolLiteral, CallExpr, IdentifierExpr,
    TypeName, UnaryExpr, UnaryOperator,
)
from patterns import (
    COND, EFFECT, IMM12, POW2, REG, Args, Need, Pattern, Rule,
    imm12, neg_imm12, pow2, signed_literal,
)


def _integer_literal(node):
    value = signed_literal(node)
    return value is not None and -(1 << 63) <= value < (1 << 63)


def _boolean(node):
    return node.metadata.get("type") is TypeName.BOOL


def _returns_value(node):
    return node.metadata.get("type") in (TypeName.INT, TypeName.BOOL)


def catalogue():
    """cada regra informa o padrao, o custo proprio e a acao do gerador."""
    rules = [
        Rule("integer", REG, Pattern(predicate=_integer_literal), 1, "literal"),
        Rule("boolean", REG, Pattern(BoolLiteral), 1, "literal"),
        Rule("identifier", REG, Pattern(IdentifierExpr), 0, "identifier"),
        Rule("immediate", IMM12, Pattern(predicate=imm12), 0, "immediate"),
        Rule("power_of_two", POW2, Pattern(predicate=pow2), 0, "power_of_two"),
        Rule("negate", REG,
             Pattern(UnaryExpr, UnaryOperator.NEGATE, (Need(REG),)), 1, "negate"),
        Rule("not", REG,
             Pattern(UnaryExpr, UnaryOperator.NOT, (Need(REG),)), 1, "not"),
        Rule("not_cond", COND,
             Pattern(UnaryExpr, UnaryOperator.NOT, (Need(COND),)), 0, "not_cond"),
        Rule("bool_cond", COND, Pattern(BoolLiteral), 1, "bool_cond"),
        Rule("identifier_cond", COND,
             Pattern(IdentifierExpr, predicate=_boolean), 2, "identifier_cond"),
        Rule("call_reg", REG,
             Pattern(CallExpr, children=(Args(REG),), predicate=_returns_value),
             1, "call_reg"),
        Rule("call_effect", EFFECT,
             Pattern(CallExpr, children=(Args(REG),)), 1, "call_effect"),
        Rule("call_cond", COND,
             Pattern(CallExpr, children=(Args(REG),), predicate=_boolean),
             3, "call_cond"),
        Rule("addi_right", REG,
             Pattern(BinaryExpr, BinaryOperator.ADD, (Need(REG), Need(IMM12))),
             1, "addi_right"),
        Rule("addi_left", REG,
             Pattern(BinaryExpr, BinaryOperator.ADD, (Need(IMM12), Need(REG))),
             1, "addi_left"),
        Rule("subi", REG,
             Pattern(BinaryExpr, BinaryOperator.SUBTRACT,
                     (Need(REG), Pattern(predicate=neg_imm12))), 1, "subi"),
        Rule("slli_right", REG,
             Pattern(BinaryExpr, BinaryOperator.MULTIPLY, (Need(REG), Need(POW2))),
             1, "slli_right"),
        Rule("slli_left", REG,
             Pattern(BinaryExpr, BinaryOperator.MULTIPLY, (Need(POW2), Need(REG))),
             1, "slli_left"),
    ]

    # as operacoes aritmeticas gerais usam dois valores em registradores.
    for operator, action in (
        (BinaryOperator.ADD, "add"),
        (BinaryOperator.SUBTRACT, "sub"),
        (BinaryOperator.MULTIPLY, "mul"),
        (BinaryOperator.DIVIDE, "div"),
        (BinaryOperator.REMAINDER, "rem"),
    ):
        pattern = Pattern(BinaryExpr, operator, (Need(REG), Need(REG)))
        rules.append(Rule(action, REG, pattern, 1, action))

    # uma comparacao pode produzir um valor ou desviar para um destino.
    for operator, action, cost in (
        (BinaryOperator.LESS, "lt", 1),
        (BinaryOperator.GREATER, "gt", 1),
        (BinaryOperator.LESS_EQUAL, "le", 2),
        (BinaryOperator.GREATER_EQUAL, "ge", 2),
        (BinaryOperator.EQUAL, "eq", 2),
        (BinaryOperator.NOT_EQUAL, "ne", 2),
    ):
        pattern = Pattern(BinaryExpr, operator, (Need(REG), Need(REG)))
        rules.append(Rule(action, REG, pattern, cost, action))
        branch = "branch_" + action
        rules.append(Rule(branch, COND, pattern, 2, branch))

    for operator, action in (
        (BinaryOperator.LOGICAL_AND, "and"),
        (BinaryOperator.LOGICAL_OR, "or"),
    ):
        pattern = Pattern(BinaryExpr, operator, (Need(COND), Need(COND)))
        rules.append(Rule(action + "_cond", COND, pattern, 0, action + "_cond"))
        rules.append(Rule(action + "_reg", REG, pattern, 3, action + "_reg"))

    return rules
