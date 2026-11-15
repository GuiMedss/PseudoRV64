"""testa o gerador com coberturas explicitas, sem depender do seletor pendente."""

from dataclasses import fields
from types import SimpleNamespace

import pytest

import codegen
import ir as I
from ast_nodes import (
    BinaryExpr, BoolLiteral, CallExpr, IdentifierExpr, IntLiteral, Node,
    ast_to_dict,
)
from executor import Interpreter, validate
from Lexer import Lexer
from parser import Parser
from patterns import COND, EFFECT, IMM12, POW2, REG, Choice, Pattern, Rule, SelectionTable
from semantic import SemanticAnalyzer


def walk(node):
    yield node
    for field in fields(node):
        if field.name == "metadata":
            continue
        value = getattr(node, field.name)
        if isinstance(value, Node):
            yield from walk(value)
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, Node):
                    yield from walk(child)


@pytest.fixture
def emission(monkeypatch):
    table = SelectionTable()
    monkeypatch.setattr(codegen, "catalogue", lambda: ())
    monkeypatch.setattr(
        codegen, "Selector", lambda rules: SimpleNamespace(analyze=lambda node: table)
    )

    def choose(node, action, result=REG, needs=()):
        rule = Rule(action, result, Pattern(), 0, action)
        table.put(node, result, Choice(0, rule, tuple(needs)))

    def parse(source):
        program = Parser(Lexer(source).scan()).parse()
        SemanticAnalyzer().analyze(program)
        # apenas as folhas recebem escolhas padrao; cada teste cobre os operadores.
        for node in walk(program):
            if isinstance(node, (IntLiteral, BoolLiteral)):
                choose(node, "literal")
            elif isinstance(node, IdentifierExpr):
                choose(node, "identifier")
        return program

    def generate(program):
        result = codegen.CodeGenerator().generate(program)
        validate(result)
        return result

    return SimpleNamespace(parse=parse, choose=choose, generate=generate, table=table)


@pytest.mark.parametrize(
    "expression,action,expected",
    [
        ("7+3", "add", 10), ("7-3", "sub", 4), ("7*3", "mul", 21),
        ("7/3", "div", 2), ("7%3", "rem", 1),
        ("2<5", "lt", True), ("5<2", "lt", False),
        ("5>2", "gt", True), ("2>5", "gt", False),
        ("5<=5", "le", True), ("5<=2", "le", False),
        ("5>=5", "ge", True), ("2>=5", "ge", False),
        ("5==5", "eq", True), ("2==5", "eq", False),
        ("5!=5", "ne", False), ("2!=5", "ne", True),
        ("true==false", "eq", False), ("true!=false", "ne", True),
    ],
)
def test_binary_actions(emission, expression, action, expected):
    program = emission.parse(f"int main() {{ print({expression}); return 0; }}")
    node = program.functions[0].body.statements[0].items[0]
    emission.choose(node, action, needs=((node.left, REG), (node.right, REG)))
    result = Interpreter().run(emission.generate(program))
    text = str(expected).lower()
    assert (result.stdout, result.main_return) == (text + "\n", 0)


@pytest.mark.parametrize(
    "operator,action,expected",
    [
        ("<", "branch_lt", True), ("<=", "branch_le", True),
        (">", "branch_gt", False), (">=", "branch_ge", False),
        ("==", "branch_eq", False), ("!=", "branch_ne", True),
    ],
)
@pytest.mark.parametrize("equal", [False, True])
def test_comparison_conditions(emission, operator, action, expected, equal):
    right = 2 if equal else 5
    program = emission.parse(
        f"int main() {{ if (2 {operator} {right}) {{ return 1; }} return 0; }}"
    )
    node = program.functions[0].body.statements[0].condition
    emission.choose(node, action, COND, ((node.left, REG), (node.right, REG)))
    if equal:
        expected = operator in ("<=", ">=", "==")
    assert Interpreter().run(emission.generate(program)).main_return == int(expected)


@pytest.mark.parametrize(
    "expression,action,literal_class,left,expected,instruction",
    [
        ("x+5", "addi_right", IMM12, False, 12, I.Addi),
        ("5+x", "addi_left", IMM12, True, 12, I.Addi),
        ("x*8", "slli_right", POW2, False, 56, I.Slli),
        ("8*x", "slli_left", POW2, True, 56, I.Slli),
        ("x*1", "slli_right", POW2, False, 7, I.Slli),
        ("x-2048", "subi", None, False, -2041, I.Addi),
    ],
)
def test_immediate_actions(emission, expression, action, literal_class, left, expected, instruction):
    program = emission.parse(f"int main() {{ int x=7; return {expression}; }}")
    node = program.functions[0].body.statements[1].value
    literal = node.left if left else node.right
    if literal_class is not None:
        emission.choose(
            literal, "immediate" if literal_class is IMM12 else "power_of_two", literal_class
        )
        needs = ((node.left, literal_class if left else REG),
                 (node.right, REG if left else literal_class))
    else:
        needs = ((node.left, REG),)
    emission.choose(node, action, needs=needs)
    generated = emission.generate(program)
    instructions = generated.functions[0].instructions
    assert any(isinstance(item, instruction) for item in instructions)
    # o literal incorporado nao deve gerar uma carga rejeitada pelo seletor.
    assert len([item for item in instructions if isinstance(item, I.Li)]) == 1
    assert Interpreter().run(generated).main_return == expected


@pytest.mark.parametrize(
    "expression,action,expected",
    [("-7", "negate", -7), ("-9223372036854775808", "literal", -(1 << 63)),
     ("!true", "not", False), ("!false", "not", True)],
)
def test_unary_actions(emission, expression, action, expected):
    source_expression = "-7" if expected == -(1 << 63) else expression
    program = emission.parse(f"int main() {{ print({source_expression}); return 0; }}")
    node = program.functions[0].body.statements[0].items[0]
    if expected == -(1 << 63):
        # testa a emissao do limite diretamente na ast ja anotada.
        node.operand.value = 1 << 63
    needs = () if action == "literal" else ((node.operand, REG),)
    emission.choose(node, action, needs=needs)
    assert Interpreter().run(emission.generate(program)).stdout == str(expected).lower() + "\n"


@pytest.mark.parametrize("operator", ["&&", "||"])
@pytest.mark.parametrize("left", [False, True])
@pytest.mark.parametrize("as_condition", [False, True])
def test_short_circuit_paths(emission, operator, left, as_condition):
    expression = f"{str(left).lower()} {operator} mark()"
    body = (f"if ({expression}) {{ return 1; }} return 0;" if as_condition
            else f"print({expression}); return 0;")
    program = emission.parse(
        'bool mark() { print("m"); return true; } int main() {' + body + "}"
    )
    root = next(node for node in walk(program) if isinstance(node, BinaryExpr))
    emission.choose(root.left, "bool_cond", COND)
    emission.choose(root.right, "call_cond", COND)
    result_class = COND if as_condition else REG
    action = ("and" if operator == "&&" else "or") + ("_cond" if as_condition else "_reg")
    emission.choose(root, action, result_class, ((root.left, COND), (root.right, COND)))
    value = left if operator == "&&" else True
    called = left if operator == "&&" else not left
    output = "m\n" if called else ""
    if not as_condition:
        output += str(value).lower() + "\n"
    result = Interpreter().run(emission.generate(program))
    assert (result.stdout, result.main_return) == (output, int(value) if as_condition else 0)


@pytest.mark.parametrize("value", [False, True])
def test_not_and_identifier_conditions(emission, value):
    program = emission.parse(
        f"int main() {{ bool x={str(value).lower()}; if (!x) {{ return 1; }} else {{ return 2; }} }}"
    )
    node = program.functions[0].body.statements[1].condition
    emission.choose(node.operand, "identifier_cond", COND)
    emission.choose(node, "not_cond", COND, ((node.operand, COND),))
    assert Interpreter().run(emission.generate(program)).main_return == (2 if value else 1)


def test_calls_print_order_void_return_and_discard(emission):
    program = emission.parse('''
        int mark(int x) { print(x); return x; }
        int difference(int a, int b) { return a-b; }
        void done() { print("fim"); }
        void stop() { return; }
        int main() {
            print("a", mark(1), "b");
            done(); stop(); mark(3);
            return difference(mark(9), mark(2));
        }
    ''')
    for node in walk(program):
        if isinstance(node, BinaryExpr):
            emission.choose(node, "sub", needs=((node.left, REG), (node.right, REG)))
        if isinstance(node, CallExpr):
            needs = tuple((argument, REG) for argument in node.arguments)
            emission.choose(node, "call_reg", REG, needs)
            emission.choose(node, "call_effect", EFFECT, needs)
    generated = emission.generate(program)
    result = Interpreter().run(generated)
    assert (result.stdout, result.main_return) == ("a1\n1b\nfim\n3\n9\n2\n", 7)
    main_calls = [i for i in generated.functions[-1].instructions if isinstance(i, I.Call)]
    assert sum(i.dst is None for i in main_calls) == 3
    assert all(isinstance(f.instructions[-1], I.Return) for f in generated.functions[2:4])


def test_loop_shadowing_and_uninitialized_declaration(emission):
    program = emission.parse('''
        int main() {
            int x=0; int y;
            while (x<3) { x=x+1; }
            { int x=8; print(x); }
            if (true) { y=x; } else { y=9; }
            return y;
        }
    ''')
    for node in walk(program):
        if isinstance(node, BinaryExpr):
            is_condition = node.operator.value == "<"
            emission.choose(node, "branch_lt" if is_condition else "add",
                            COND if is_condition else REG,
                            ((node.left, REG), (node.right, REG)))
        elif isinstance(node, BoolLiteral):
            emission.choose(node, "bool_cond", COND)
    generated = emission.generate(program)
    result = Interpreter().run(generated)
    assert (result.stdout, result.main_return) == ("8\n", 3)
    # apenas x tem inicializacao antes do teste do laco.
    prefix = generated.functions[0].instructions[:3]
    assert [type(i) for i in prefix] == [I.Li, I.Move, I.LabelInstr]


def test_recursion_and_parameter_order(emission):
    program = emission.parse('''
        int factorial(int n) {
            if (n<=1) { return 1; }
            return n * factorial(n-1);
        }
        int main() { return factorial(5); }
    ''')
    actions = {"<=": "branch_le", "*": "mul", "-": "sub"}
    for node in walk(program):
        if isinstance(node, BinaryExpr):
            emission.choose(node, actions[node.operator.value],
                            COND if node.operator.value == "<=" else REG,
                            ((node.left, REG), (node.right, REG)))
        elif isinstance(node, CallExpr):
            emission.choose(node, "call_reg", needs=tuple((a, REG) for a in node.arguments))
    assert Interpreter().run(emission.generate(program)).main_return == 120


@pytest.mark.parametrize("arity", [0, 9])
def test_call_arities(emission, arity):
    parameters = ",".join(f"int p{i}" for i in range(arity))
    arguments = ",".join(str(i + 1) for i in range(arity))
    program = emission.parse(
        f"int f({parameters}) {{ return {'p8' if arity else '42'}; }}"
        f"int main() {{ return f({arguments}); }}"
    )
    call = program.functions[1].body.statements[0].value
    emission.choose(call, "call_reg", needs=tuple((a, REG) for a in call.arguments))
    generated = emission.generate(program)
    assert [i.index for i in generated.functions[0].instructions if isinstance(i, I.Param)] == list(range(arity))
    assert Interpreter().run(generated).main_return == (9 if arity else 42)


def test_nested_short_circuit_with_negation(emission):
    program = emission.parse('''
        bool mark() { print("m"); return false; }
        int main() { print(!(false || (true && mark()))); return 0; }
    ''')
    root = program.functions[1].body.statements[0].items[0]
    outer, inner = root.operand, root.operand.right
    emission.choose(outer.left, "bool_cond", COND)
    emission.choose(inner.left, "bool_cond", COND)
    emission.choose(inner.right, "call_cond", COND)
    emission.choose(inner, "and_cond", COND, ((inner.left, COND), (inner.right, COND)))
    emission.choose(outer, "or_reg", REG, ((outer.left, COND), (inner, COND)))
    emission.choose(root, "not", needs=((outer, REG),))
    assert Interpreter().run(emission.generate(program)).stdout == "m\ntrue\n"


def test_generation_reuse_ast_origin_and_string_pool(emission):
    program = emission.parse('''
        void show() { print("x"); }
        int main() { print(1, true, ""); show(); print("\\n"); return 0; }
    ''')
    call = program.functions[1].body.statements[1].call
    emission.choose(call, "call_effect", EFFECT)
    before = ast_to_dict(program)
    metadata = [(node, node.metadata.copy()) for node in walk(program)]
    generator = codegen.CodeGenerator()
    first, second = generator.generate(program), generator.generate(program)
    assert first is not second
    assert I.format_ir(first) == I.format_ir(second)
    assert ast_to_dict(program) == before
    assert all(node.metadata == saved for node, saved in metadata)
    assert list(first.strings.values()).count("\n") == 1
    assert all(i.origin is not None for f in first.functions for i in f.instructions
               if not isinstance(i, I.LabelInstr))
    assert Interpreter().run(first).stdout == "1true\nx\n\n\n"


@pytest.mark.parametrize("unknown_action", [False, True])
def test_missing_coverage_or_action_has_source_position(emission, unknown_action):
    program = emission.parse("int main() { return 7; }")
    node = program.functions[0].body.statements[0].value
    if unknown_action:
        emission.choose(node, "unknown")
    else:
        emission.table.entries.clear()
    message = "acao desconhecida" if unknown_action else "sem cobertura"
    with pytest.raises(ValueError, match=f"{message}.*{node.span.start_line}:{node.span.start_column}"):
        emission.generate(program)
