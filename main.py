import re
import sys


SCHEMA = {
    "categoria":         ["idcategoria", "descricao"],
    "produto":           ["idproduto", "nome", "descricao", "preco", "quantestoque", "categoria_idcategoria"],
    "tipocliente":       ["idtipocliente", "descricao"],
    "cliente":           ["idcliente", "nome", "email", "nascimento", "senha",
                          "tipocliente_idtipocliente", "dataregistro"],
    "tipoendereco":      ["idtipoendereco", "descricao"],
    "endereco":          ["idendereco", "enderecopadrao", "logradouro", "numero", "complemento",
                          "bairro", "cidade", "uf", "cep",
                          "tipoendereco_idtipoendereco", "cliente_idcliente"],
    "telefone":          ["numero", "cliente_idcliente"],
    "status":            ["idstatus", "descricao"],
    "pedido":            ["idpedido", "status_idstatus", "datapedido", "valortotalpedido", "cliente_idcliente"],
    "pedido_has_produto": ["idpedidoproduto", "pedido_idpedido", "produto_idproduto",
                           "quantidade", "precounitario"],
}


def normalizar(sql):
    sql = sql.strip().lower()
    sql = re.sub(r'\s+', ' ', sql)
    return sql


def dividir_alias(token):
    partes = re.split(r'\s+as\s+|\s+', token.strip(), maxsplit=1)
    tabela = partes[0]
    alias  = partes[1] if len(partes) == 2 else partes[0]
    return tabela, alias


def resolver_coluna(col, alias_map):
    col = col.strip().lower()
    if '.' in col:
        prefixo, campo = col.split('.', 1)
        tabela = alias_map.get(prefixo, prefixo)
        if tabela in SCHEMA and campo in SCHEMA[tabela]:
            return tabela, campo
        return None
    # sem prefixo: busca em todas as tabelas do alias_map
    candidatos = []
    for alias, tabela in alias_map.items():
        if tabela in SCHEMA and col in SCHEMA[tabela]:
            candidatos.append((tabela, col))
    if len(candidatos) == 1:
        return candidatos[0]
    if len(candidatos) > 1:
        return ("ambíguo", col)
    return None


# ─────────────────────────────────────────────
# HU1 — PARSER E VALIDADOR
# ─────────────────────────────────────────────

class ErroSQL(Exception):
    pass


def parse_sql(sql_original):
    sql = normalizar(sql_original)

    if not sql.startswith("select "):
        raise ErroSQL("A consulta deve começar com SELECT.")
    if " from " not in sql:
        raise ErroSQL("Palavra-chave FROM não encontrada.")

    where_clause = None
    where_match = re.search(r'\bwhere\b', sql)
    if where_match:
        where_clause = sql[where_match.end():].strip()
        sql_sem_where = sql[:where_match.start()].strip()
    else:
        sql_sem_where = sql

    from_match = re.search(r'\bfrom\b', sql_sem_where)
    if not from_match:
        raise ErroSQL("FROM não localizado na consulta (sem WHERE).")

    select_part = sql_sem_where[len("select "):from_match.start()].strip()
    from_join_part = sql_sem_where[from_match.end():].strip()

    join_split = re.split(r'\bjoin\b', from_join_part)
    tabela_principal_raw = join_split[0].strip()
    joins_raw = join_split[1:]

    tabela_principal, alias_principal = dividir_alias(tabela_principal_raw)
    if tabela_principal not in SCHEMA:
        raise ErroSQL(f"Tabela '{tabela_principal}' não existe no modelo.")

    alias_map = {alias_principal: tabela_principal}

    joins = []
    for jr in joins_raw:
        on_match = re.search(r'\bon\b', jr)
        if not on_match:
            raise ErroSQL(f"JOIN sem cláusula ON: '...join {jr}'")
        tabela_join_raw = jr[:on_match.start()].strip()
        cond_join = jr[on_match.end():].strip()

        tabela_join, alias_join = dividir_alias(tabela_join_raw)
        if tabela_join not in SCHEMA:
            raise ErroSQL(f"Tabela de JOIN '{tabela_join}' não existe no modelo.")

        alias_map[alias_join] = tabela_join
        joins.append({"tabela": tabela_join, "alias": alias_join, "condicao": cond_join})

    colunas_raw = [c.strip() for c in select_part.split(",")]
    colunas = []
    for col_raw in colunas_raw:
        col_raw = col_raw.strip()
        if col_raw == "*":
            colunas.append("*")
            continue
        resultado = resolver_coluna(col_raw, alias_map)
        if resultado is None:
            raise ErroSQL(f"Coluna '{col_raw}' não encontrada no modelo.")
        if resultado[0] == "ambíguo":
            raise ErroSQL(f"Coluna '{col_raw}' é ambígua — use prefixo de tabela/alias.")
        colunas.append(col_raw)

    if where_clause:
        _validar_condicao(where_clause, alias_map, contexto="WHERE")

    for j in joins:
        _validar_condicao(j["condicao"], alias_map, contexto=f"ON (join {j['tabela']})")

    return {
        "colunas":          colunas,
        "tabela_principal": tabela_principal,
        "alias_principal":  alias_principal,
        "joins":            joins,
        "where":            where_clause,
        "alias_map":        alias_map,
    }


# Operadores relacionais reconhecidos
_OPERADORES = r"(<=|>=|<>|<|>|=)"

def _validar_condicao(cond, alias_map, contexto=""):
    cond_limpa = cond.replace("(", " ").replace(")", " ")

    # Divide por AND
    partes = re.split(r'\band\b', cond_limpa)

    for parte in partes:
        parte = parte.strip()
        if not parte:
            continue
        m = re.search(_OPERADORES, parte)
        if not m:
            raise ErroSQL(f"Operador inválido ou não reconhecido em '{parte}' [{contexto}].")
        lado_esq = parte[:m.start()].strip()
        lado_dir = parte[m.end():].strip()

        for lado in (lado_esq, lado_dir):
            if re.fullmatch(r'-?\d+(\.\d+)?', lado):
                continue
            if re.fullmatch(r"'[^']*'|\"[^\"]*\"", lado):
                continue
            resultado = resolver_coluna(lado, alias_map)
            if resultado is None:
                raise ErroSQL(f"Coluna/valor '{lado}' inválido em [{contexto}].")
            if resultado[0] == "ambíguo":
                raise ErroSQL(f"Coluna '{lado}' é ambígua em [{contexto}].")


# ─────────────────────────────────────────────
# HU2 — CONVERSÃO PARA ÁLGEBRA RELACIONAL
# ─────────────────────────────────────────────

def gerar_algebra_relacional(parsed):
    colunas   = parsed["colunas"]
    tabela_p  = parsed["tabela_principal"]
    alias_p   = parsed["alias_principal"]
    joins     = parsed["joins"]
    where     = parsed["where"]
    alias_map = parsed["alias_map"]

    if alias_p != tabela_p:
        base = f"{tabela_p} ρ({alias_p})"
    else:
        base = tabela_p

    for j in joins:
        alias_j = j["alias"]
        tab_j   = j["tabela"]
        cond_j  = j["condicao"]

        if alias_j != tab_j:
            lado_dir = f"{tab_j} ρ({alias_j})"
        else:
            lado_dir = tab_j

        base = f"({base} ⋈ [{cond_j}] {lado_dir})"

    if where:
        base = f"σ[{where}]({base})"

    if colunas == ["*"]:
        resultado = base
    else:
        proj = ", ".join(colunas)
        resultado = f"π[{proj}]({base})"

    return resultado


def gerar_algebra_relacional_detalhada(parsed):
    linhas = []
    colunas   = parsed["colunas"]
    tabela_p  = parsed["tabela_principal"]
    alias_p   = parsed["alias_principal"]
    joins     = parsed["joins"]
    where     = parsed["where"]

    linhas.append("── Passo a passo da construção ──")
    linhas.append(f"  Tabela base          : {tabela_p}")

    for i, j in enumerate(joins, 1):
        linhas.append(f"  JOIN {i}               : {j['tabela']}  ON  {j['condicao']}")

    if where:
        linhas.append(f"  Seleção (σ)          : {where}")

    if colunas == ["*"]:
        linhas.append("  Projeção (π)         : todos os atributos (*)")
    else:
        linhas.append(f"  Projeção (π)         : {', '.join(colunas)}")

    return "\n".join(linhas)


# ─────────────────────────────────────────────
# HU3 — GRAFO DE OPERADORES
# ─────────────────────────────────────────────

from graphviz import Digraph

def construir_grafo(parsed, nome_arquivo="grafo_operadores"):
    colunas  = parsed["colunas"]
    tabela_p = parsed["tabela_principal"]
    alias_p  = parsed["alias_principal"]
    joins    = parsed["joins"]
    where    = parsed["where"]

    dot = Digraph(name="grafo_operadores", graph_attr={"rankdir": "TB", "fontname": "Helvetica"})
    dot.attr("node", fontname="Helvetica", fontsize="11")

    contador = [0]

    def novo_id(prefixo="n"):
        contador[0] += 1
        return f"{prefixo}_{contador[0]}"

    # Folhas (tabelas)
    id_tabela_p = novo_id("folha")
    label_p = tabela_p if alias_p == tabela_p else f"{tabela_p}\n(alias: {alias_p})"
    dot.node(id_tabela_p, label_p, shape="rectangle", style="filled", fillcolor="#cce5ff")

    ids_joins = []
    for j in joins:
        id_j = novo_id("folha")
        label_j = j["tabela"] if j["alias"] == j["tabela"] else f"{j['tabela']}\n(alias: {j['alias']})"
        dot.node(id_j, label_j, shape="rectangle", style="filled", fillcolor="#cce5ff")
        ids_joins.append((id_j, j["condicao"]))

    # Junções encadeadas
    no_atual = id_tabela_p
    for id_j, cond_j in ids_joins:
        id_join = novo_id("join")
        dot.node(id_join, f"⋈\n{cond_j}", shape="diamond", style="filled", fillcolor="#fff3cd")
        dot.edge(id_join, no_atual)
        dot.edge(id_join, id_j)
        no_atual = id_join

    # Seleção (WHERE)
    if where:
        id_sel = novo_id("sel")
        dot.node(id_sel, f"σ\n{where}", shape="ellipse", style="filled", fillcolor="#d4edda")
        dot.edge(id_sel, no_atual)
        no_atual = id_sel

    # Projeção (raiz)
    id_proj = novo_id("proj")
    if colunas == ["*"]:
        label_proj = "π\n*"
    else:
        label_proj = "π\n" + ", ".join(colunas)
    dot.node(id_proj, label_proj, shape="ellipse", style="filled", fillcolor="#f8d7da")
    dot.edge(id_proj, no_atual)

    caminho = dot.render(nome_arquivo, format="png", cleanup=True)
    return caminho


# ─────────────────────────────────────────────
# HU5 — PLANO DE EXECUÇÃO
# ─────────────────────────────────────────────

def gerar_plano_execucao(parsed):
    colunas  = parsed["colunas"]
    tabela_p = parsed["tabela_principal"]
    alias_p  = parsed["alias_principal"]
    joins    = parsed["joins"]
    where    = parsed["where"]

    passos = []
    passo  = 1

    passos.append((passo, "SCAN", f"Leitura da tabela '{tabela_p}'" +
                   (f" (alias: {alias_p})" if alias_p != tabela_p else "")))
    passo += 1

    for j in joins:
        passos.append((passo, "SCAN", f"Leitura da tabela '{j['tabela']}'" +
                       (f" (alias: {j['alias']})" if j['alias'] != j['tabela'] else "")))
        passo += 1

    if where:
        passos.append((passo, "SELECT", f"Aplicar seleção σ[{where}]"))
        passo += 1

    tabela_atual = tabela_p
    for j in joins:
        passos.append((passo, "JOIN", f"Junção ⋈  {tabela_atual}  ×  {j['tabela']}  ON  {j['condicao']}"))
        tabela_atual = f"({tabela_atual} ⋈ {j['tabela']})"
        passo += 1

    if colunas != ["*"]:
        passos.append((passo, "PROJECT", f"Projeção π[{', '.join(colunas)}]"))
        passo += 1

    passos.append((passo, "RESULT", "Retorno do resultado final"))

    return passos


def exibir_plano_execucao(passos):
    linhas = []
    linhas.append("── Plano de Execução ──")
    for num, tipo, descricao in passos:
        linhas.append(f"  {num:2}. [{tipo:<7}] {descricao}")
    return "\n".join(linhas)


SEPARADOR = "─" * 60

def cabecalho():
    print(SEPARADOR)
    print("  Processador de Consultas SQL")
    print("  HU1 – Validação  |  HU2 – Álgebra Relacional  |  HU3 – Grafo  |  HU5 – Plano de Execução")
    print(SEPARADOR)
    print("  Tabelas disponíveis:", ", ".join(sorted(SCHEMA.keys())))
    print(SEPARADOR)

def loop_principal():
    cabecalho()
    print("  Digite 'sair' para encerrar.\n")

    while True:
        try:
            sql = input("SQL> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nEncerrando.")
            break

        if not sql:
            continue
        if sql.lower() in ("sair", "exit", "quit"):
            print("Encerrando.")
            break

        print()
        try:
            parsed = parse_sql(sql)
            print("✔ Consulta válida!\n")

            print("── Estrutura reconhecida ──")
            print(f"  SELECT  : {', '.join(parsed['colunas'])}")
            print(f"  FROM    : {parsed['tabela_principal']}", end="")
            if parsed['alias_principal'] != parsed['tabela_principal']:
                print(f" AS {parsed['alias_principal']}", end="")
            print()
            for j in parsed["joins"]:
                print(f"  JOIN    : {j['tabela']} AS {j['alias']}  ON  {j['condicao']}")
            if parsed["where"]:
                print(f"  WHERE   : {parsed['where']}")

            print()
            algebra = gerar_algebra_relacional(parsed)
            print("── Álgebra Relacional ──")
            print(f"  {algebra}")
            print()
            print(gerar_algebra_relacional_detalhada(parsed))
            print()
            plano = gerar_plano_execucao(parsed)
            print(exibir_plano_execucao(plano))
            print()
            caminho = construir_grafo(parsed)
            print(f"── Grafo de Operadores ──")
            print(f"  Imagem salva em: {caminho}")

        except ErroSQL as e:
            print(f"✘ Erro de validação: {e}")

        print(SEPARADOR)
        print()


# ─────────────────────────────────────────────
# MODO BATCH (para testes automáticos)
# ─────────────────────────────────────────────
EXEMPLOS = [
    # 1. SELECT simples
    "SELECT idProduto, Nome, Preco FROM Produto WHERE Preco > 100",

    # 2. JOIN único
    "SELECT c.Nome, p.DataPedido FROM Cliente c JOIN Pedido p ON c.idCliente = p.Cliente_idCliente",

    # 3. Múltiplos JOINs
    (
        "SELECT c.Nome, pr.Nome, php.Quantidade "
        "FROM Cliente c "
        "JOIN Pedido p ON c.idCliente = p.Cliente_idCliente "
        "JOIN Pedido_has_Produto php ON p.idPedido = php.Pedido_idPedido "
        "JOIN Produto pr ON pr.idProduto = php.Produto_idProduto"
    ),

    # 4. WHERE com múltiplas condições
    (
        "SELECT idCliente, Nome FROM Cliente "
        "WHERE idCliente > 10 AND TipoCliente_idTipoCliente = 1"
    ),

    # 5. Consulta inválida — tabela inexistente
    "SELECT id FROM Funcionario",

    # 6. Consulta inválida — coluna inexistente
    "SELECT Salario FROM Cliente",

    # 7. SELECT *
    "SELECT * FROM Produto",
]

def modo_demo():
    cabecalho()
    print("  [MODO DEMO — executando exemplos pré-definidos]\n")
    for i, sql in enumerate(EXEMPLOS, 1):
        print(f"SQL> {sql}")
        try:
            parsed  = parse_sql(sql)
            algebra = gerar_algebra_relacional(parsed)
            plano   = gerar_plano_execucao(parsed)
            caminho = construir_grafo(parsed, nome_arquivo=f"grafo_{i}")
            print("✔ Válida")
            print(f"  Álgebra: {algebra}")
            print(exibir_plano_execucao(plano))
            print(f"  Grafo salvo em: {caminho}")
        except ErroSQL as e:
            print(f"✘ {e}")
        print(SEPARADOR)
    print()


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        modo_demo()
    else:
        loop_principal()