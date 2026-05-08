import queue
import tkinter as tk
from tkinter import ttk, messagebox


class BotGUI:
    """
    Tkinter GUI that lives on the main thread only.
    Communicates with CocBot via a thread-safe queue.
    """

    def __init__(self, root: tk.Tk, bot, msg_queue: queue.Queue):
        self.root = root
        self.bot = bot
        self.msg_queue = msg_queue

        self.root.title("COC Auto Farm")
        self.root.geometry("500x520")
        self.root.minsize(450, 450)

        self._build_ui()
        self._poll_queue()

    def _build_ui(self):
        mainframe = ttk.Frame(self.root, padding="10 10 10 10")
        mainframe.grid(column=0, row=0, sticky=(tk.N, tk.W, tk.E, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # --- Thresholds (what to look for) ---
        ttk.Label(mainframe, text="--- Thresholds (attack if above) ---").grid(
            column=1, row=0, columnspan=2, sticky=tk.W, pady=(0, 5)
        )

        ttk.Label(mainframe, text="Min Gold").grid(column=1, row=1, sticky=tk.W)
        self.min_gold = tk.IntVar(value=200000)
        ttk.Entry(mainframe, width=10, textvariable=self.min_gold).grid(
            column=2, row=1, sticky=(tk.W, tk.E)
        )

        ttk.Label(mainframe, text="Min Elixir").grid(column=1, row=2, sticky=tk.W)
        self.min_elixir = tk.IntVar(value=200000)
        ttk.Entry(mainframe, width=10, textvariable=self.min_elixir).grid(
            column=2, row=2, sticky=(tk.W, tk.E)
        )

        ttk.Label(mainframe, text="Min Dark Elixir").grid(column=1, row=3, sticky=tk.W)
        self.min_dark = tk.IntVar(value=0)
        ttk.Entry(mainframe, width=10, textvariable=self.min_dark).grid(
            column=2, row=3, sticky=(tk.W, tk.E)
        )

        # --- Goals (stop when totals exceed) ---
        ttk.Label(mainframe, text="--- Goals (stop farm when reached) ---").grid(
            column=1, row=4, columnspan=2, sticky=tk.W, pady=(10, 5)
        )

        ttk.Label(mainframe, text="Total Gold").grid(column=1, row=5, sticky=tk.W)
        self.goal_gold = tk.IntVar(value=5000000)
        ttk.Entry(mainframe, width=10, textvariable=self.goal_gold).grid(
            column=2, row=5, sticky=(tk.W, tk.E)
        )

        ttk.Label(mainframe, text="Total Elixir").grid(column=1, row=6, sticky=tk.W)
        self.goal_elixir = tk.IntVar(value=5000000)
        ttk.Entry(mainframe, width=10, textvariable=self.goal_elixir).grid(
            column=2, row=6, sticky=(tk.W, tk.E)
        )

        ttk.Label(mainframe, text="Total Dark Elixir").grid(column=1, row=7, sticky=tk.W)
        self.goal_dark = tk.IntVar(value=0)
        ttk.Entry(mainframe, width=10, textvariable=self.goal_dark).grid(
            column=2, row=7, sticky=(tk.W, tk.E)
        )

        # --- Controls ---
        ctrl_frame = ttk.Frame(mainframe)
        ctrl_frame.grid(column=1, row=8, columnspan=2, pady=(15, 5), sticky=(tk.W, tk.E))

        self.start_btn = ttk.Button(ctrl_frame, text="Start Farm", command=self._on_start)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.stop_btn = ttk.Button(ctrl_frame, text="Stop Farm (F12)", command=self._on_stop)
        self.stop_btn.pack(side=tk.LEFT)
        self.stop_btn.config(state="disabled")

        # --- Status ---
        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(mainframe, text="Status:").grid(column=1, row=9, sticky=tk.W, pady=(10, 0))
        ttk.Label(mainframe, textvariable=self.status_var).grid(
            column=2, row=9, sticky=(tk.W, tk.E), pady=(10, 0)
        )

        self.totals_var = tk.StringVar(value="Gold: 0 | Elixir: 0 | Dark: 0")
        ttk.Label(mainframe, text="Totals:").grid(column=1, row=10, sticky=tk.W)
        ttk.Label(mainframe, textvariable=self.totals_var).grid(
            column=2, row=10, sticky=(tk.W, tk.E)
        )

        # --- Log viewer ---
        ttk.Label(mainframe, text="Log:").grid(
            column=1, row=11, columnspan=2, sticky=tk.W, pady=(10, 0)
        )
        log_frame = ttk.Frame(mainframe)
        log_frame.grid(column=1, row=12, columnspan=2, sticky=(tk.N, tk.W, tk.E, tk.S), pady=(0, 5))
        mainframe.rowconfigure(12, weight=1)
        mainframe.columnconfigure(2, weight=1)

        self.log_text = tk.Text(log_frame, height=10, wrap=tk.WORD, state="disabled")
        vsb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=vsb.set)

        self.log_text.grid(column=0, row=0, sticky=(tk.N, tk.S, tk.E, tk.W))
        vsb.grid(column=1, row=0, sticky=(tk.N, tk.S))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

    def _on_start(self):
        targets = (
            self.min_gold.get(),
            self.min_elixir.get(),
            self.min_dark.get(),
        )
        goals = (
            self.goal_gold.get(),
            self.goal_elixir.get(),
            self.goal_dark.get(),
        )
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_var.set("starting...")
        self.bot.start(targets, goals)

    def _on_stop(self):
        self.stop_btn.config(state="disabled")
        self.bot.stop()

    def _log(self, text: str):
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    def _poll_queue(self):
        """Drain the bot queue every 150ms on the main thread."""
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                mtype = msg.get("type")

                if mtype == "log":
                    self._log(msg.get("text", ""))

                elif mtype == "status":
                    state = msg.get("state", "unknown")
                    self.status_var.set(state)
                    if state == "stopped":
                        self.start_btn.config(state="normal")
                        self.stop_btn.config(state="disabled")
                        self.root.deiconify()
                    elif state == "error":
                        self.start_btn.config(state="normal")
                        self.stop_btn.config(state="disabled")
                        self.root.deiconify()
                    elif state == "running":
                        self.start_btn.config(state="disabled")
                        self.stop_btn.config(state="normal")
                        self.root.withdraw()  # hide window while farming, like old notebook

                elif mtype == "alert":
                    messagebox.showinfo("Bot Alert", msg.get("text", ""))

                elif mtype == "totals":
                    data = msg.get("data", {})
                    self.totals_var.set(
                        f"Gold: {data.get('gold', 0)} | "
                        f"Elixir: {data.get('elixir', 0)} | "
                        f"Dark: {data.get('delixir', 0)}"
                    )
        except queue.Empty:
            pass
        finally:
            self.root.after(150, self._poll_queue)
