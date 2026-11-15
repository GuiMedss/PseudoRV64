"""geracao de comandos e reconstrucao das expressoes selecionadas."""

import load_previous
import ir as I
from ast_nodes import (
    Assignment,
    Block,
    BoolLiteral,
    CallStmt,
    IfStmt,
    PrintStmt,
    ReturnStmt,
    StringLiteral,
    TypeName,
    VarDecl,
    WhileStmt,
)
from catalogue import catalogue
from patterns import COND, EFFECT, REG, signed_literal
from selector import Selector


BINARY_ACTIONS = {
    "add": I.Add,
    "sub": I.Sub,
    "mul": I.Mul,
    "div": I.Div,
    "rem": I.Rem,
}
BRANCH_ACTIONS = {
    "branch_lt": I.Blt,
    "branch_le": I.Bge,
    "branch_gt": I.Blt,
    "branch_ge": I.Bge,
    "branch_eq": I.Beq,
    "branch_ne": I.Bne,
}


class CodeGenerator:
    """percorre comandos e emite apenas a cobertura escolhida pelo seletor."""

    def generate(self, program):
        """gera um programa novo sem modificar a ast ou os simbolos."""
        self.program_ir = I.ProgramIR()
        self.selector = Selector(catalogue())
        for function in program.functions:
            function_ir = I.FunctionIR(function.metadata["symbol"])
            self.program_ir.functions.append(function_ir)
            self.context = I.Context(function_ir, self.program_ir)
            for index, parameter in enumerate(function.parameters):
                register = self.context.variable(parameter.metadata["symbol"])
                self.context.emit(I.Param(register, index, origin=parameter.span))
            self.statement(function.body)
            if function.return_type is TypeName.VOID:
                self.context.emit(I.Return(None, origin=function.span))
        return self.program_ir

    def expression(self, node, result_class, true_label=None, false_label=None):
        """consulta o seletor uma vez e reconstroi a cobertura da raiz."""
        table = self.selector.analyze(node)
        return self._reconstruct(node, result_class, table, true_label, false_label)

    def statement(self, node):
        """traduz cada comando na ordem em que aparece no fonte."""
        context = self.context
        if isinstance(node, Block):
            for statement in node.statements:
                self.statement(statement)
        elif isinstance(node, VarDecl):
            register = context.variable(node.metadata["symbol"])
            if node.initializer is not None:
                value = self.expression(node.initializer, REG)
                context.emit(I.Move(register, value, origin=node.span))
        elif isinstance(node, Assignment):
            value = self.expression(node.value, REG)
            register = context.variable(node.target.metadata["symbol"])
            context.emit(I.Move(register, value, origin=node.span))
        elif isinstance(node, CallStmt):
            self.expression(node.call, EFFECT)
        elif isinstance(node, ReturnStmt):
            value = None
            if node.value is not None:
                value = self.expression(node.value, REG)
            context.emit(I.Return(value, origin=node.span))
        elif isinstance(node, PrintStmt):
            for item in node.items:
                if isinstance(item, StringLiteral):
                    context.emit(
                        I.PrintString(context.string(item.value), origin=item.span)
                    )
                else:
                    value = self.expression(item, REG)
                    if item.metadata["type"] is TypeName.BOOL:
                        instruction = I.PrintBool
                    else:
                        instruction = I.PrintInt
                    context.emit(instruction(value, origin=item.span))
            context.emit(I.PrintString(context.string("\n"), origin=node.span))
        elif isinstance(node, IfStmt):
            then_label, end_label = context.label(), context.label()
            else_label = end_label
            if node.else_block is not None:
                else_label = context.label()
            self.expression(node.condition, COND, then_label, else_label)
            context.emit(I.LabelInstr(then_label))
            self.statement(node.then_block)
            if node.else_block is not None:
                context.emit(I.Jump(end_label, origin=node.span))
                context.emit(I.LabelInstr(else_label))
                self.statement(node.else_block)
            context.emit(I.LabelInstr(end_label))
        elif isinstance(node, WhileStmt):
            test_label, body_label, end_label = (
                context.label(),
                context.label(),
                context.label(),
            )
            context.emit(I.LabelInstr(test_label))
            self.expression(node.condition, COND, body_label, end_label)
            context.emit(I.LabelInstr(body_label))
            self.statement(node.body)
            context.emit(I.Jump(test_label, origin=node.span))
            context.emit(I.LabelInstr(end_label))
        else:
            self._error(node, f"comando desconhecido: {type(node).__name__}")

    def _reconstruct(
        self, node, result_class, table, true_label=None, false_label=None
    ):
        choice = table.lookup(node, result_class)
        if choice is None:
            self._error(node, f"expressao sem cobertura para {result_class.value}")
        if result_class is COND and (true_label is None or false_label is None):
            self._error(node, "condicao sem destinos verdadeiro e falso")

        action = choice.rule.action
        problems = choice.subproblems
        context = self.context

        # os filhos logicos precisam ser emitidos dentro dos seus caminhos.
        if action == "not_cond":
            child, child_class = problems[0]
            return self._reconstruct(child, child_class, table, false_label, true_label)
        if action in ("and_cond", "or_cond", "and_reg", "or_reg"):
            materialize = action in ("and_reg", "or_reg")
            if materialize:
                true_label, false_label = context.label(), context.label()
            next_label = context.label()
            left, left_class = problems[0]
            right, right_class = problems[1]
            if action in ("and_cond", "and_reg"):
                self._reconstruct(left, left_class, table, next_label, false_label)
            else:
                self._reconstruct(left, left_class, table, true_label, next_label)
            context.emit(I.LabelInstr(next_label))
            self._reconstruct(right, right_class, table, true_label, false_label)
            if materialize:
                register, end_label = context.temp(), context.label()
                context.emit(I.LabelInstr(true_label))
                context.emit(I.Li(register, 1, origin=node.span))
                context.emit(I.Jump(end_label, origin=node.span))
                context.emit(I.LabelInstr(false_label))
                context.emit(I.Li(register, 0, origin=node.span))
                context.emit(I.LabelInstr(end_label))
                return register
            return None

        # subproblems segue a ordem dos pontos need do padrao selecionado.
        values = []
        for child, child_class in problems:
            values.append(self._reconstruct(child, child_class, table))
        if action == "literal":
            if isinstance(node, BoolLiteral):
                value = int(node.value)
            else:
                value = signed_literal(node)
            register = context.temp()
            context.emit(I.Li(register, value, origin=node.span))
            return register
        if action == "identifier":
            return context.variable(node.metadata["symbol"])
        if action == "immediate":
            return signed_literal(node)
        if action == "power_of_two":
            return signed_literal(node).bit_length() - 1
        if action == "bool_cond":
            target = true_label if node.value else false_label
            context.emit(I.Jump(target, origin=node.span))
            return None
        if action == "identifier_cond":
            register = context.variable(node.metadata["symbol"])
            self._branch_value(register, true_label, false_label, node.span)
            return None
        if action in ("call_reg", "call_effect", "call_cond"):
            register = None
            if action != "call_effect":
                register = context.temp()
            context.emit(
                I.Call(register, node.metadata["symbol"], values, origin=node.span)
            )
            if action == "call_cond":
                self._branch_value(register, true_label, false_label, node.span)
                return None
            return register
        if action in BRANCH_ACTIONS:
            instruction = BRANCH_ACTIONS[action]
            left, right = values
            # a <= b equivale a b >= a; a > b equivale a b < a.
            if action in ("branch_le", "branch_gt"):
                left, right = right, left
            context.emit(instruction(left, right, true_label, origin=node.span))
            context.emit(I.Jump(false_label, origin=node.span))
            return None

        register = context.temp()
        if action in BINARY_ACTIONS:
            instruction = BINARY_ACTIONS[action]
            context.emit(instruction(register, values[0], values[1], origin=node.span))
        elif action in ("negate", "not"):
            instruction = I.Neg if action == "negate" else I.Seqz
            context.emit(instruction(register, values[0], origin=node.span))
        elif action in ("addi_right", "addi_left", "slli_right", "slli_left"):
            value, immediate = values
            if action in ("addi_left", "slli_left"):
                value, immediate = immediate, value
            if action in ("addi_right", "addi_left"):
                instruction = I.Addi
            else:
                instruction = I.Slli
            context.emit(instruction(register, value, immediate, origin=node.span))
        elif action == "subi":
            # o literal direito e coberto pelo predicado, sem um need extra.
            context.emit(
                I.Addi(register, values[0], -signed_literal(node.right), origin=node.span)
            )
        elif action in ("lt", "gt", "le", "ge"):
            left, right = values
            if action in ("gt", "le"):
                left, right = right, left
            context.emit(I.Slt(register, left, right, origin=node.span))
            if action in ("le", "ge"):
                # seqz inverte o resultado de slt para incluir a igualdade.
                context.emit(I.Seqz(register, register, origin=node.span))
        elif action in ("eq", "ne"):
            context.emit(I.Xor(register, values[0], values[1], origin=node.span))
            instruction = I.Seqz if action == "eq" else I.Snez
            context.emit(instruction(register, register, origin=node.span))
        else:
            self._error(node, f"acao desconhecida no catalogo: {action}")
        return register

    def _branch_value(self, register, true_label, false_label, origin):
        self.context.emit(I.Bnez(register, true_label, origin=origin))
        self.context.emit(I.Jump(false_label, origin=origin))

    def _error(self, node, message):
        span = node.span
        raise ValueError(f"{message} em {span.start_line}:{span.start_column}")
