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
# HU4 — OTIMIZAÇÃO DA CONSULTA (HEURÍSTICAS)
# ─────────────────────────────────────────────

def _extrair_tabelas_cond(cond, alias_map):
    """Retorna o conjunto de tabelas referenciadas em uma condição."""
    tabelas = set()
    for token in re.findall(r'[\w.]+', cond):
        if '.' in token:
            prefixo = token.split('.')[0]
            tabela = alias_map.get(prefixo, prefixo)
            if tabela in SCHEMA:
                tabelas.add(tabela)
        else:
            t = token.lower()
            if t in SCHEMA:
                tabelas.add(t)
    return tabelas


def _contar_atributos_where(where_clause):
    """Conta quantas condições AND existem (proxy de restritividade)."""
    if not where_clause:
        return 0
    return len(re.split(r'\band\b', where_clause))


def otimizar_consulta(parsed):
    """
    Aplica heurísticas de otimização e devolve uma representação otimizada.

    Heurísticas aplicadas:
      a-i)  Seleção (WHERE) pushed-down — aplicada à tabela antes dos JOINs
      a-ii) Projeção intermediária — restringe atributos o mais cedo possível
      b-i)  Reordenação de JOINs — joins mais restritivos (com condição que
            envolve mais atributos / linker direto com tabela base) ficam primeiro
      b-ii) Garante que nenhum produto cartesiano seja introduzido (todos os
            JOINs têm condição ON verificada no parser)
    """
    import copy
    opt = copy.deepcopy(parsed)

    colunas   = opt["colunas"]
    tabela_p  = opt["tabela_principal"]
    alias_p   = opt["alias_principal"]
    joins     = opt["joins"]
    where     = opt["where"]
    alias_map = opt["alias_map"]

    # ── Heurística b-i: reordenar JOINs por restritividade ──────────────
    # Critério: joins cuja condição ON referencia a tabela principal diretamente
    # vêm primeiro; depois os demais na ordem original (preserva encadeamento).
    def _score_join(j):
        tabelas_na_cond = _extrair_tabelas_cond(j["condicao"], alias_map)
        # Quanto mais a condição liga diretamente à tabela principal, menor o score (vem antes)
        direto = 1 if tabela_p in tabelas_na_cond else 2
        # Restritividade pela quantidade de termos na condição ON (mais termos = mais restritivo)
        termos = len(re.findall(r'[\w.]+', j["condicao"]))
        return (direto, -termos)

    opt["joins"] = sorted(joins, key=_score_join)

    # ── Heurística a-i: identificar quais partes do WHERE podem ser pushed-down ──
    # Divide o WHERE em sub-condições AND e classifica cada uma pela tabela que afeta.
    pushed_down = {}   # alias_tabela -> lista de sub-condições
    remaining_where = []

    if where:
        sub_conds = [s.strip() for s in re.split(r'\band\b', where) if s.strip()]
        for sub in sub_conds:
            # Verifica se a condição só usa colunas de uma única tabela
            tabelas_ref = set()
            for token in re.findall(r'[\w.]+', sub):
                if re.fullmatch(r'-?\d+(\.\d+)?', token):
                    continue  # literal numérico
                if '.' in token:
                    prefixo = token.split('.')[0].lower()
                    if prefixo in alias_map:
                        tabelas_ref.add(alias_map[prefixo])
                else:
                    # token sem prefixo: busca em qual tabela esse campo existe
                    t = token.lower()
                    for alias, tabela in alias_map.items():
                        if tabela in SCHEMA and t in SCHEMA[tabela]:
                            tabelas_ref.add(tabela)
            if len(tabelas_ref) == 1:
                tbl = list(tabelas_ref)[0]
                pushed_down.setdefault(tbl, []).append(sub)
            else:
                remaining_where.append(sub)

    opt["pushed_down_filters"] = pushed_down        # seleções antecipadas por tabela
    opt["remaining_where"]     = remaining_where    # seleções que ficam após JOIN

    # ── Heurística a-ii: projeções intermediárias ────────────────────────
    # Para cada tabela envolvida, calcula quais colunas realmente são necessárias
    # (aparecem no SELECT, nos JOINs ou no WHERE) — permite push-down de projeção.
    colunas_necessarias = {}   # tabela -> set de campos

    def _registrar(col_token):
        r = resolver_coluna(col_token, alias_map)
        if r and r[0] != "ambíguo":
            tbl, campo = r
            colunas_necessarias.setdefault(tbl, set()).add(campo)

    # Colunas do SELECT
    if colunas != ["*"]:
        for c in colunas:
            _registrar(c)

    # Colunas dos JOINs
    for j in opt["joins"]:
        for token in re.findall(r'[\w.]+', j["condicao"]):
            if '.' in token or token.lower() not in ('and', 'or', 'on'):
                _registrar(token)

    # Colunas do WHERE
    if where:
        for token in re.findall(r'[\w.]+', where):
            if '.' in token:
                _registrar(token)

    opt["proj_intermediaria"] = colunas_necessarias

    # ── Álgebra relacional otimizada (texto) ─────────────────────────────
    opt["algebra_otimizada"]  = _gerar_algebra_otimizada(opt)

    # ── Passos de otimização para exibição ───────────────────────────────
    opt["passos_otimizacao"]  = _descrever_otimizacoes(opt, joins, where)

    return opt


def _gerar_algebra_otimizada(opt):
    """Gera a expressão de álgebra relacional após aplicar as heurísticas."""
    tabela_p  = opt["tabela_principal"]
    alias_p   = opt["alias_principal"]
    joins     = opt["joins"]
    colunas   = opt["colunas"]
    pushed    = opt.get("pushed_down_filters", {})
    remaining = opt.get("remaining_where", [])
    proj_int  = opt.get("proj_intermediaria", {})

    def _nome(tabela, alias):
        return f"{tabela} ρ({alias})" if alias != tabela else tabela

    def _proj_int_expr(expr, tabela):
        """Aplica projeção intermediária se não for SELECT *."""
        if colunas == ["*"]:
            return expr
        cols = proj_int.get(tabela, set())
        if cols:
            return f"π[{', '.join(sorted(cols))}]({expr})"
        return expr

    # Tabela base com seleção pushed-down e projeção intermediária
    base_expr = _nome(tabela_p, alias_p)
    if tabela_p in pushed:
        filtro = " AND ".join(pushed[tabela_p])
        base_expr = f"σ[{filtro}]({base_expr})"
    base_expr = _proj_int_expr(base_expr, tabela_p)

    for j in joins:
        lado_dir = _nome(j["tabela"], j["alias"])
        if j["tabela"] in pushed:
            filtro = " AND ".join(pushed[j["tabela"]])
            lado_dir = f"σ[{filtro}]({lado_dir})"
        lado_dir = _proj_int_expr(lado_dir, j["tabela"])

        base_expr = f"({base_expr} ⋈ [{j['condicao']}] {lado_dir})"

    # Seleções restantes (condições entre tabelas)
    if remaining:
        base_expr = f"σ[{' AND '.join(remaining)}]({base_expr})"

    # Projeção final
    if colunas != ["*"]:
        base_expr = f"π[{', '.join(colunas)}]({base_expr})"

    return base_expr


def _descrever_otimizacoes(opt, joins_originais, where_original):
    """Retorna lista de strings descrevendo cada heurística aplicada."""
    descricoes = []

    # Reordenação de JOINs
    nomes_orig = [j["tabela"] for j in joins_originais]
    nomes_opt  = [j["tabela"] for j in opt["joins"]]
    if nomes_orig != nomes_opt:
        descricoes.append(
            f"[b-i] JOINs reordenados por restritividade:\n"
            f"       Antes : {' → '.join(nomes_orig)}\n"
            f"       Depois: {' → '.join(nomes_opt)}"
        )
    else:
        descricoes.append("[b-i] Ordem dos JOINs mantida (já otimizada).")

    # Push-down de seleção
    pushed = opt.get("pushed_down_filters", {})
    if pushed:
        for tbl, conds in pushed.items():
            descricoes.append(
                f"[a-i] Seleção antecipada (push-down) em '{tbl}':\n"
                f"       σ[{' AND '.join(conds)}] aplicada antes do JOIN"
            )
    else:
        descricoes.append("[a-i] Nenhuma seleção pode ser antecipada (WHERE cruza tabelas).")

    # Seleções que ficaram após JOIN
    remaining = opt.get("remaining_where", [])
    if remaining:
        descricoes.append(
            f"[a-i] Seleção pós-JOIN mantida:\n"
            f"       σ[{' AND '.join(remaining)}]"
        )

    # Projeções intermediárias
    proj = opt.get("proj_intermediaria", {})
    if proj and opt["colunas"] != ["*"]:
        for tbl, cols in proj.items():
            descricoes.append(
                f"[a-ii] Projeção intermediária em '{tbl}':\n"
                f"        π[{', '.join(sorted(cols))}]"
            )
    else:
        descricoes.append("[a-ii] SELECT * — projeção intermediária não aplicável.")

    # Produto cartesiano
    descricoes.append("[b-ii] Nenhum produto cartesiano — todos os JOINs possuem cláusula ON.")

    return descricoes


def construir_grafo_otimizado(opt, nome_arquivo="grafo_otimizado"):
    """
    Constrói o grafo de operadores OTIMIZADO, incorporando:
      - push-down de seleção (σ antes do JOIN)
      - projeção intermediária (π por tabela)
      - ordem de JOINs reordenada
    """
    dot = Digraph(name="grafo_otimizado", graph_attr={"rankdir": "TB", "fontname": "Helvetica"})
    dot.attr("node", fontname="Helvetica", fontsize="11")

    colunas   = opt["colunas"]
    tabela_p  = opt["tabela_principal"]
    alias_p   = opt["alias_principal"]
    joins     = opt["joins"]
    pushed    = opt.get("pushed_down_filters", {})
    remaining = opt.get("remaining_where", [])
    proj_int  = opt.get("proj_intermediaria", {})

    contador = [0]

    def novo_id(prefixo="n"):
        contador[0] += 1
        return f"{prefixo}_{contador[0]}"

    def _folha_com_otimizacoes(tabela, alias):
        """Cria nó folha + push-down de σ e π intermediária. Retorna id do topo."""
        id_folha = novo_id("folha")
        label = tabela if alias == tabela else f"{tabela}\n(alias: {alias})"
        dot.node(id_folha, label, shape="rectangle", style="filled", fillcolor="#cce5ff")
        topo = id_folha

        # Push-down de seleção (Heurística a-i)
        if tabela in pushed:
            id_sel = novo_id("sel")
            filtro = " AND ".join(pushed[tabela])
            dot.node(id_sel, f"σ [push-down]\n{filtro}",
                     shape="ellipse", style="filled", fillcolor="#d4edda")
            dot.edge(id_sel, topo)
            topo = id_sel

        # Projeção intermediária (Heurística a-ii)
        if colunas != ["*"] and tabela in proj_int:
            cols = sorted(proj_int[tabela])
            id_pi = novo_id("pi")
            dot.node(id_pi, f"π [interm.]\n{', '.join(cols)}",
                     shape="ellipse", style="filled", fillcolor="#e2d9f3")
            dot.edge(id_pi, topo)
            topo = id_pi

        return topo

    # Folha principal
    no_atual = _folha_com_otimizacoes(tabela_p, alias_p)

    # JOINs (em ordem otimizada — Heurística b-i)
    for j in joins:
        no_join_dir = _folha_com_otimizacoes(j["tabela"], j["alias"])
        id_join = novo_id("join")
        dot.node(id_join, f"⋈\n{j['condicao']}",
                 shape="diamond", style="filled", fillcolor="#fff3cd")
        dot.edge(id_join, no_atual)
        dot.edge(id_join, no_join_dir)
        no_atual = id_join

    # Seleções restantes pós-JOIN
    if remaining:
        id_sel_rem = novo_id("sel")
        dot.node(id_sel_rem, f"σ [pós-JOIN]\n{' AND '.join(remaining)}",
                 shape="ellipse", style="filled", fillcolor="#d4edda")
        dot.edge(id_sel_rem, no_atual)
        no_atual = id_sel_rem

    # Projeção final (raiz)
    id_proj = novo_id("proj")
    label_proj = "π [final]\n" + (", ".join(colunas) if colunas != ["*"] else "*")
    dot.node(id_proj, label_proj, shape="ellipse", style="filled", fillcolor="#f8d7da")
    dot.edge(id_proj, no_atual)

    caminho = dot.render(nome_arquivo, format="png", cleanup=True)
    return caminho


def exibir_otimizacao(opt):
    """Formata o relatório de otimização para exibição na interface."""
    linhas = []
    linhas.append("── Álgebra Relacional Otimizada ──")
    linhas.append(f"  {opt['algebra_otimizada']}")
    linhas.append("")
    linhas.append("── Heurísticas Aplicadas ──")
    for desc in opt["passos_otimizacao"]:
        for linha in desc.splitlines():
            linhas.append(f"  {linha}")
        linhas.append("")
    return "\n".join(linhas)


# ─────────────────────────────────────────────
# HU5 — PLANO DE EXECUÇÃO
# ─────────────────────────────────────────────

def gerar_plano_execucao(parsed, otimizado=None):
    """
    Gera o plano de execução.
    Se 'otimizado' for fornecido, usa a ordem otimizada de JOINs e push-downs.
    """
    fonte    = otimizado if otimizado else parsed
    colunas  = fonte["colunas"]
    tabela_p = fonte["tabela_principal"]
    alias_p  = fonte["alias_principal"]
    joins    = fonte["joins"]
    where    = fonte.get("where")
    pushed   = fonte.get("pushed_down_filters", {})
    remaining= fonte.get("remaining_where", [])
    proj_int = fonte.get("proj_intermediaria", {})
    is_opt   = otimizado is not None

    passos = []
    passo  = 1

    # SCAN + push-downs da tabela principal
    passos.append((passo, "SCAN", f"Leitura da tabela '{tabela_p}'" +
                   (f" (alias: {alias_p})" if alias_p != tabela_p else "")))
    passo += 1

    if is_opt and tabela_p in pushed:
        passos.append((passo, "SELECT",
                       f"[push-down] σ[{' AND '.join(pushed[tabela_p])}] em '{tabela_p}'"))
        passo += 1

    if is_opt and colunas != ["*"] and tabela_p in proj_int:
        cols = sorted(proj_int[tabela_p])
        passos.append((passo, "PROJECT",
                       f"[interm.] π[{', '.join(cols)}] em '{tabela_p}'"))
        passo += 1

    # SCAN + push-downs de cada tabela de JOIN
    for j in joins:
        passos.append((passo, "SCAN", f"Leitura da tabela '{j['tabela']}'" +
                       (f" (alias: {j['alias']})" if j['alias'] != j['tabela'] else "")))
        passo += 1

        if is_opt and j["tabela"] in pushed:
            passos.append((passo, "SELECT",
                           f"[push-down] σ[{' AND '.join(pushed[j['tabela']])}] em '{j['tabela']}'"))
            passo += 1

        if is_opt and colunas != ["*"] and j["tabela"] in proj_int:
            cols = sorted(proj_int[j["tabela"]])
            passos.append((passo, "PROJECT",
                           f"[interm.] π[{', '.join(cols)}] em '{j['tabela']}'"))
            passo += 1

    # Seleção WHERE (sem otimização)
    if not is_opt and where:
        passos.append((passo, "SELECT", f"Aplicar seleção σ[{where}]"))
        passo += 1

    # JOINs
    tabela_atual = tabela_p
    for j in joins:
        passos.append((passo, "JOIN",
                       f"Junção ⋈  {tabela_atual}  ×  {j['tabela']}  ON  {j['condicao']}"))
        tabela_atual = f"({tabela_atual} ⋈ {j['tabela']})"
        passo += 1

    # Seleções pós-JOIN (otimizado)
    if is_opt and remaining:
        passos.append((passo, "SELECT",
                       f"[pós-JOIN] σ[{' AND '.join(remaining)}]"))
        passo += 1

    # Projeção final
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
    print("  HU1 – Validação  |  HU2 – Álgebra Relacional  |  HU3 – Grafo  |  HU4 – Otimização  |  HU5 – Plano de Execução")
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

            opt = otimizar_consulta(parsed)
            print("── Otimização (HU4) ──")
            print(exibir_otimizacao(opt))

            plano = gerar_plano_execucao(parsed, otimizado=opt)
            print(exibir_plano_execucao(plano))
            print()
            caminho     = construir_grafo(parsed)
            caminho_opt = construir_grafo_otimizado(opt)
            print(f"── Grafo de Operadores ──")
            print(f"  Imagem original  salva em: {caminho}")
            print(f"  Imagem otimizada salva em: {caminho_opt}")

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
            opt     = otimizar_consulta(parsed)
            plano   = gerar_plano_execucao(parsed, otimizado=opt)
            caminho     = construir_grafo(parsed, nome_arquivo=f"grafo_{i}")
            caminho_opt = construir_grafo_otimizado(opt, nome_arquivo=f"grafo_opt_{i}")
            print("✔ Válida")
            print(f"  Álgebra: {algebra}")
            print(f"  Álgebra otimizada: {opt['algebra_otimizada']}")
            print(exibir_plano_execucao(plano))
            print(f"  Grafo original  salvo em: {caminho}")
            print(f"  Grafo otimizado salvo em: {caminho_opt}")
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