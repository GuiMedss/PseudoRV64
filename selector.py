"""escolhe a cobertura de menor custo para cada no e classe."""

from ast_nodes import CallExpr
from patterns import Args, Choice, Need, SelectionTable, children


class Selector:
    """analisa primeiro os filhos e depois as regras do no atual."""

    def __init__(self, rules):
        self.rules = tuple(rules)

    def analyze(self, expr):
        """cria uma tabela nova para cada expressao."""
        table = SelectionTable()
        self._visit(expr, table)
        return table

    def _visit(self, node, table):
        for child in children(node):
            self._visit(child, table)

        for rule in self.rules:
            problems = self._match(rule.pattern, node, table)
            if problems is None:
                continue

            cost = rule.cost
            for child, result_class in problems:
                cost += table.lookup(child, result_class).cost

            current = table.lookup(node, rule.result)
            # em empate, mantem a primeira regra para ter uma escolha estavel.
            if current is None or cost < current.cost:
                table.put(node, rule.result, Choice(cost, rule, tuple(problems)))

    def _match(self, pattern, node, table):
        if isinstance(pattern, Need):
            if table.lookup(node, pattern.result) is None:
                return None
            return [(node, pattern.result)]

        if pattern.kind is not None and not isinstance(node, pattern.kind):
            return None
        if pattern.operator is not None:
            if getattr(node, "operator", None) != pattern.operator:
                return None
        if pattern.predicate is not None:
            if not pattern.predicate(node):
                return None
            # um predicado sem filhos reconhece a subarvore inteira.
            if not pattern.children:
                return []

        node_children = children(node)
        if len(pattern.children) == 1 and isinstance(pattern.children[0], Args):
            if not isinstance(node, CallExpr):
                return None
            result_class = pattern.children[0].result
            problems = []
            for argument in node_children:
                if table.lookup(argument, result_class) is None:
                    return None
                problems.append((argument, result_class))
            return problems

        if len(pattern.children) != len(node_children):
            return None
        problems = []
        for child_pattern, child in zip(pattern.children, node_children):
            matched = self._match(child_pattern, child, table)
            if matched is None:
                return None
            problems.extend(matched)
        return problems
