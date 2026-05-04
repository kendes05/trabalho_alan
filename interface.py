import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import os
import tempfile

from main import (
    SCHEMA,
    ErroSQL,
    parse_sql,
    gerar_algebra_relacional,
    gerar_algebra_relacional_detalhada,
    gerar_plano_execucao,
    exibir_plano_execucao,
    construir_grafo,
)

# ─────────────────────────────────────────────
# CORES E FONTES
# ─────────────────────────────────────────────
COR_BG         = "#1e1e2e"
COR_PAINEL     = "#2a2a3e"
COR_BORDA      = "#44475a"
COR_TEXTO      = "#cdd6f4"
COR_PLACEHOLDER= "#6c7086"
COR_DESTAQUE   = "#89b4fa"
COR_ERRO       = "#f38ba8"
COR_OK         = "#a6e3a1"
COR_AMARELO    = "#f9e2af"
COR_BTN        = "#313244"
COR_BTN_HOVER  = "#45475a"

FONTE_MONO  = ("Consolas", 11)
FONTE_LABEL = ("Segoe UI", 10)
FONTE_TITLE = ("Segoe UI", 11, "bold")

EXEMPLOS = [
    "SELECT idProduto, Nome, Preco FROM Produto WHERE Preco > 100",
    "SELECT c.Nome, p.DataPedido FROM Cliente c JOIN Pedido p ON c.idCliente = p.Cliente_idCliente",
    (
        "SELECT c.Nome, pr.Nome, php.Quantidade "
        "FROM Cliente c "
        "JOIN Pedido p ON c.idCliente = p.Cliente_idCliente "
        "JOIN Pedido_has_Produto php ON p.idPedido = php.Pedido_idPedido "
        "JOIN Produto pr ON pr.idProduto = php.Produto_idProduto"
    ),
    "SELECT idCliente, Nome FROM Cliente WHERE idCliente > 10 AND TipoCliente_idTipoCliente = 1",
    "SELECT * FROM Produto",
]

# ─────────────────────────────────────────────
# WIDGETS AUXILIARES
# ─────────────────────────────────────────────

def criar_texto_readonly(pai, height, **kwargs):
    t = tk.Text(
        pai, height=height, bg=COR_PAINEL, fg=COR_TEXTO,
        font=FONTE_MONO, relief="flat", bd=0,
        insertbackground=COR_TEXTO, selectbackground=COR_DESTAQUE,
        wrap="word", **kwargs
    )
    t.config(state="disabled")
    return t

def escrever_texto(widget, conteudo, cor=None):
    widget.config(state="normal")
    widget.delete("1.0", "end")
    widget.insert("end", conteudo)
    if cor:
        widget.config(fg=cor)
    else:
        widget.config(fg=COR_TEXTO)
    widget.config(state="disabled")

def btn_style(b):
    b.config(
        bg=COR_BTN, fg=COR_TEXTO, font=FONTE_LABEL,
        relief="flat", bd=0, padx=12, pady=6, cursor="hand2",
        activebackground=COR_BTN_HOVER, activeforeground=COR_TEXTO,
    )
    b.bind("<Enter>", lambda e: b.config(bg=COR_BTN_HOVER))
    b.bind("<Leave>", lambda e: b.config(bg=COR_BTN))

# ─────────────────────────────────────────────
# JANELA PRINCIPAL
# ─────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Processador de Consultas SQL")
        self.configure(bg=COR_BG)
        self.geometry("1100x780")
        self.minsize(900, 650)
        self._parsed   = None
        self._img_path = None
        self._photo    = None
        self._construir_ui()

    def _construir_ui(self):
        # ── Título ────────────────────────────────────────────────────
        topo = tk.Frame(self, bg=COR_BG)
        topo.pack(fill="x", padx=20, pady=(16, 0))

        tk.Label(
            topo, text="Processador de Consultas SQL",
            bg=COR_BG, fg=COR_DESTAQUE,
            font=("Segoe UI", 16, "bold")
        ).pack(side="left")

        tk.Label(
            topo, text="HU1 · HU2 · HU3 · HU5",
            bg=COR_BG, fg=COR_PLACEHOLDER,
            font=("Segoe UI", 10)
        ).pack(side="left", padx=12, pady=4)

        # ── Área de entrada ───────────────────────────────────────────
        frame_entrada = tk.LabelFrame(
            self, text=" Consulta SQL ", bg=COR_BG, fg=COR_DESTAQUE,
            font=FONTE_TITLE, bd=1, relief="groove", labelanchor="nw"
        )
        frame_entrada.pack(fill="x", padx=20, pady=(12, 0))

        self.txt_entrada = tk.Text(
            frame_entrada, height=4, bg=COR_PAINEL, fg=COR_TEXTO,
            font=FONTE_MONO, relief="flat", bd=0,
            insertbackground=COR_TEXTO, selectbackground=COR_DESTAQUE,
            wrap="word", padx=8, pady=8,
        )
        self.txt_entrada.pack(fill="x", padx=8, pady=8)
        self.txt_entrada.bind("<Control-Return>", lambda e: self._executar())

        # ── Barra de botões ───────────────────────────────────────────
        frame_btns = tk.Frame(self, bg=COR_BG)
        frame_btns.pack(fill="x", padx=20, pady=8)

        self.btn_exec = tk.Button(frame_btns, text="▶  Executar  (Ctrl+Enter)", command=self._executar)
        btn_style(self.btn_exec)
        self.btn_exec.config(fg=COR_OK)
        self.btn_exec.pack(side="left", padx=(0, 8))

        btn_limpar = tk.Button(frame_btns, text="✕  Limpar", command=self._limpar)
        btn_style(btn_limpar)
        btn_limpar.pack(side="left", padx=(0, 8))

        # Exemplos dropdown
        self.var_exemplo = tk.StringVar(value="Exemplos")
        menu_ex = tk.OptionMenu(frame_btns, self.var_exemplo, *[f"Exemplo {i+1}" for i in range(len(EXEMPLOS))],
                                command=self._carregar_exemplo)
        menu_ex.config(bg=COR_BTN, fg=COR_TEXTO, font=FONTE_LABEL,
                       relief="flat", bd=0, activebackground=COR_BTN_HOVER,
                       activeforeground=COR_TEXTO, highlightthickness=0)
        menu_ex["menu"].config(bg=COR_PAINEL, fg=COR_TEXTO, font=FONTE_LABEL,
                               activebackground=COR_DESTAQUE, activeforeground=COR_BG)
        menu_ex.pack(side="left")

        # Status à direita
        self.lbl_status = tk.Label(frame_btns, text="", bg=COR_BG, font=FONTE_LABEL)
        self.lbl_status.pack(side="right")

        # ── Notebook de resultados ────────────────────────────────────
        style = ttk.Style(self)
        style.theme_use("default")
        style.configure("TNotebook",           background=COR_BG,    borderwidth=0)
        style.configure("TNotebook.Tab",       background=COR_BTN,   foreground=COR_TEXTO,
                        font=FONTE_LABEL,      padding=(14, 6))
        style.map("TNotebook.Tab",
                  background=[("selected", COR_PAINEL)],
                  foreground=[("selected", COR_DESTAQUE)])

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=20, pady=(0, 16))

        # Tab 1 — HU1 validação
        tab1 = tk.Frame(nb, bg=COR_PAINEL)
        nb.add(tab1, text="  HU1 · Validação  ")
        self.txt_hu1 = criar_texto_readonly(tab1, height=10)
        self.txt_hu1.pack(fill="both", expand=True, padx=10, pady=10)

        # Tab 2 — HU2 álgebra
        tab2 = tk.Frame(nb, bg=COR_PAINEL)
        nb.add(tab2, text="  HU2 · Álgebra Relacional  ")
        self.txt_hu2 = criar_texto_readonly(tab2, height=10)
        self.txt_hu2.pack(fill="both", expand=True, padx=10, pady=10)

        # Tab 3 — HU3 grafo
        tab3 = tk.Frame(nb, bg=COR_PAINEL)
        nb.add(tab3, text="  HU3 · Grafo de Operadores  ")
        self._montar_tab_grafo(tab3)

        # Tab 4 — HU5 plano
        tab4 = tk.Frame(nb, bg=COR_PAINEL)
        nb.add(tab4, text="  HU5 · Plano de Execução  ")
        self.txt_hu5 = criar_texto_readonly(tab4, height=10)
        self.txt_hu5.pack(fill="both", expand=True, padx=10, pady=10)

        self.nb = nb

    def _montar_tab_grafo(self, pai):
        frame_topo = tk.Frame(pai, bg=COR_PAINEL)
        frame_topo.pack(fill="x", padx=10, pady=(10, 4))

        self.lbl_grafo_status = tk.Label(
            frame_topo, text="Execute uma consulta para gerar o grafo.",
            bg=COR_PAINEL, fg=COR_PLACEHOLDER, font=FONTE_LABEL
        )
        self.lbl_grafo_status.pack(side="left")

        btn_salvar = tk.Button(frame_topo, text="💾  Salvar imagem", command=self._salvar_grafo)
        btn_style(btn_salvar)
        btn_salvar.pack(side="right")

        # Canvas com scrollbars
        frame_canvas = tk.Frame(pai, bg=COR_PAINEL)
        frame_canvas.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.canvas_grafo = tk.Canvas(frame_canvas, bg=COR_PAINEL, bd=0, highlightthickness=0)
        sb_v = ttk.Scrollbar(frame_canvas, orient="vertical",   command=self.canvas_grafo.yview)
        sb_h = ttk.Scrollbar(frame_canvas, orient="horizontal", command=self.canvas_grafo.xview)
        self.canvas_grafo.configure(yscrollcommand=sb_v.set, xscrollcommand=sb_h.set)

        sb_v.pack(side="right",  fill="y")
        sb_h.pack(side="bottom", fill="x")
        self.canvas_grafo.pack(fill="both", expand=True)

    # ─────────────────────────────────────────
    # AÇÕES
    # ─────────────────────────────────────────

    def _executar(self):
        sql = self.txt_entrada.get("1.0", "end").strip()
        if not sql:
            return
        self.btn_exec.config(state="disabled")
        self._set_status("Processando…", COR_AMARELO)
        threading.Thread(target=self._processar, args=(sql,), daemon=True).start()

    def _processar(self, sql):
        try:
            parsed = parse_sql(sql)
            self._parsed = parsed

            # HU1
            linhas_hu1 = ["✔ Consulta válida!\n"]
            linhas_hu1.append(f"SELECT  : {', '.join(parsed['colunas'])}")
            from_txt = parsed['tabela_principal']
            if parsed['alias_principal'] != parsed['tabela_principal']:
                from_txt += f" AS {parsed['alias_principal']}"
            linhas_hu1.append(f"FROM    : {from_txt}")
            for j in parsed["joins"]:
                linhas_hu1.append(f"JOIN    : {j['tabela']} AS {j['alias']}  ON  {j['condicao']}")
            if parsed["where"]:
                linhas_hu1.append(f"WHERE   : {parsed['where']}")
            linhas_hu1.append(f"\nTabelas no schema disponíveis:\n  {', '.join(sorted(SCHEMA.keys()))}")

            # HU2
            algebra     = gerar_algebra_relacional(parsed)
            detalhada   = gerar_algebra_relacional_detalhada(parsed)
            txt_hu2     = f"Expressão:\n  {algebra}\n\n{detalhada}"

            # HU3
            tmp = tempfile.mktemp()
            caminho_img = construir_grafo(parsed, nome_arquivo=tmp)
            self._img_path = caminho_img

            # HU5
            plano    = gerar_plano_execucao(parsed)
            txt_hu5  = exibir_plano_execucao(plano)

            self.after(0, lambda: self._mostrar_resultados(
                "\n".join(linhas_hu1), txt_hu2, caminho_img, txt_hu5, ok=True
            ))

        except ErroSQL as e:
            self.after(0, lambda msg=str(e): self._mostrar_erro(msg))
        except Exception as e:
            self.after(0, lambda msg=f"Erro inesperado: {e}": self._mostrar_erro(msg))

    def _mostrar_resultados(self, hu1, hu2, img_path, hu5, ok):
        escrever_texto(self.txt_hu1, hu1, cor=COR_OK)
        escrever_texto(self.txt_hu2, hu2)
        escrever_texto(self.txt_hu5, hu5)
        self._carregar_imagem_grafo(img_path)
        self._set_status("✔ Consulta válida", COR_OK)
        self.btn_exec.config(state="normal")

    def _mostrar_erro(self, msg):
        escrever_texto(self.txt_hu1, f"✘ Erro de validação:\n\n  {msg}", cor=COR_ERRO)
        escrever_texto(self.txt_hu2, "")
        escrever_texto(self.txt_hu5, "")
        self.canvas_grafo.delete("all")
        self.lbl_grafo_status.config(text="Nenhum grafo — consulta inválida.", fg=COR_ERRO)
        self._set_status("✘ Erro na consulta", COR_ERRO)
        self.btn_exec.config(state="normal")
        self.nb.select(0)

    def _carregar_imagem_grafo(self, path):
        try:
            from PIL import Image, ImageTk
            img = Image.open(path)
            self._photo = ImageTk.PhotoImage(img)
            self.canvas_grafo.delete("all")
            self.canvas_grafo.create_image(0, 0, anchor="nw", image=self._photo)
            self.canvas_grafo.configure(scrollregion=(0, 0, img.width, img.height))
            self.lbl_grafo_status.config(
                text=f"Grafo gerado ({img.width}×{img.height}px)", fg=COR_OK
            )
        except ImportError:
            self.lbl_grafo_status.config(
                text=f"Instale Pillow (pip install Pillow) para visualizar o grafo aqui.\nArquivo salvo em: {path}",
                fg=COR_AMARELO,
            )
        except Exception as e:
            self.lbl_grafo_status.config(text=f"Erro ao carregar imagem: {e}", fg=COR_ERRO)

    def _limpar(self):
        self.txt_entrada.delete("1.0", "end")
        escrever_texto(self.txt_hu1, "")
        escrever_texto(self.txt_hu2, "")
        escrever_texto(self.txt_hu5, "")
        self.canvas_grafo.delete("all")
        self.lbl_grafo_status.config(
            text="Execute uma consulta para gerar o grafo.", fg=COR_PLACEHOLDER
        )
        self._set_status("", None)
        self._parsed   = None
        self._img_path = None
        self._photo    = None
        self.var_exemplo.set("Exemplos")

    def _carregar_exemplo(self, escolha):
        idx = int(escolha.split()[-1]) - 1
        sql = EXEMPLOS[idx]
        self.txt_entrada.delete("1.0", "end")
        self.txt_entrada.insert("1.0", sql)

    def _salvar_grafo(self):
        if not self._img_path or not os.path.exists(self._img_path):
            messagebox.showwarning("Aviso", "Nenhum grafo disponível para salvar.")
            return
        destino = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("Todos", "*.*")],
            initialfile="grafo_operadores.png",
        )
        if destino:
            import shutil
            shutil.copy(self._img_path, destino)
            messagebox.showinfo("Salvo", f"Grafo salvo em:\n{destino}")

    def _set_status(self, msg, cor):
        self.lbl_status.config(text=msg, fg=cor or COR_TEXTO)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    app = App()
    app.mainloop()