import os
import subprocess
import json
import time
import threading
import shutil
from datetime import datetime
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.console import Console
from rich.text import Text

# --- Configuration ---
DGB_BIN_PATH = "digibyte-cli" 
REFRESH_RATE = 2
MAX_ROWS_PER_TABLE = 20
DGB_DATA_DIR = os.path.expanduser("~/.digibyte")

class SystemMonitor:
    @staticmethod
    def get_cpu_usage():
        def read_stats():
            try:
                with open('/proc/stat', 'r') as f:
                    fields = [float(column) for column in f.readline().strip().split()[1:]]
                return sum(fields), fields[3]
            except: return 0, 0
        t1, i1 = read_stats()
        time.sleep(0.1)
        t2, i2 = read_stats()
        diff_total = t2 - t1
        diff_idle = i2 - i1
        return (1 - (diff_idle / diff_total)) * 100 if diff_total > 0 else 0

    @staticmethod
    def get_ram_usage():
        try:
            mem = {}
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    parts = line.split()
                    mem[parts[0].rstrip(':')] = int(parts[1])
            used = (mem['MemTotal'] - mem.get('MemAvailable', mem['MemFree'])) / (1024 * 1024)
            return used, (used / (mem['MemTotal'] / (1024 * 1024))) * 100
        except: return 0, 0

    @staticmethod
    def get_dir_size(path):
        total = 0
        try:
            if not os.path.exists(path): return 0
            for entry in os.scandir(path):
                if entry.is_file(): total += entry.stat().st_size
                elif entry.is_dir(): total += SystemMonitor.get_dir_size(entry.path)
        except: pass
        return total / (1024**3)

class DGBNodeMonitor:
    def __init__(self):
        self.sys_mon = SystemMonitor()
        self.start_time = time.time()
        self.data = {
            "node": {}, "system": {}, "blockchain": {},
            "peers_in": [], "peers_out": [],
            "last_update": "---"
        }
        self.lock = threading.Lock()

    def _run_cli(self, cmd_args):
        try:
            result = subprocess.run([DGB_BIN_PATH] + cmd_args, capture_output=True, text=True, check=True)
            return json.loads(result.stdout)
        except: return None

    def format_uptime(self, seconds):
        days, rem = divmod(int(seconds), 86400)
        hours, rem = divmod(rem, 3600)
        mins, secs = divmod(rem, 60)
        if days > 0: return f"{days}d {hours}h {mins}m"
        return f"{hours}h {mins}m {secs}s"

    def format_diff(self, val):
        # Generic scaling used for both now, supporting G, M, and K
        if val >= 1e9: return f"{val/1e9:.2f} G"
        if val >= 1e6: return f"{val/1e6:.2f} M"
        if val >= 1e3: return f"{val/1e3:.2f} K"
        return f"{val:.2f}"

    def update_data(self):
        bc_info = self._run_cli(["getblockchaininfo"])
        net_info = self._run_cli(["getnetworkinfo"])
        mining_info = self._run_cli(["getmininginfo"])
        uptime_info = self._run_cli(["uptime"])
        peers = self._run_cli(["getpeerinfo"]) or []
        
        cpu_p = self.sys_mon.get_cpu_usage()
        ram_gb, ram_p = self.sys_mon.get_ram_usage()
        blocks_size = self.sys_mon.get_dir_size(os.path.join(DGB_DATA_DIR, "blocks"))
        chain_size = self.sys_mon.get_dir_size(os.path.join(DGB_DATA_DIR, "chainstate"))
        
        with self.lock:
            self.data["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if bc_info:
                diffs = bc_info.get("difficulties", {})
                node_uptime = uptime_info if isinstance(uptime_info, int) else (time.time() - self.start_time)
                self.data["node"] = {"ver": net_info.get("subversion", "N/A") if net_info else "N/A"}
                self.data["blockchain"] = {
                    "height": bc_info.get("blocks", 0),
                    "sync": bc_info.get("verificationprogress", 0) * 100,
                    "diff_sha": self.format_diff(diffs.get("sha256d", 0)),
                    "diff_scrypt": self.format_diff(diffs.get("scrypt", 0)),
                    "hashrate": (mining_info.get("networkhashps", 0) if mining_info else 0) / 1e15,
                    "uptime": self.format_uptime(node_uptime)
                }
            self.data["system"] = {
                "cpu": cpu_p, "ram": ram_gb, "ram_p": ram_p,
                "blocks_gb": blocks_size, "chain_gb": chain_size
            }
            sorted_peers = sorted(peers, key=lambda x: x.get('pingtime', 999))
            self.data["peers_in"] = [p for p in sorted_peers if p.get("inbound")]
            self.data["peers_out"] = [p for p in sorted_peers if not p.get("inbound")]

    def generate_layout(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="top", size=6), 
            Layout(name="mid", size=3),
            Layout(name="outbound", ratio=1),
            Layout(name="inbound", ratio=1)
        )

        sys, bc, node = self.data["system"], self.data["blockchain"], self.data["node"]
        perf_grid = Table.grid(expand=True)
        perf_grid.add_column(ratio=1); perf_grid.add_column(ratio=1)
        
        n_info = (f"[bold bright_magenta]DGB Version:[/] {node.get('ver', '...')}\n"
                  f"[bold bright_magenta]CPU Usage:[/]   {sys.get('cpu', 0):.1f}%\n"
                  f"[bold bright_magenta]RAM Usage:[/]   {sys.get('ram', 0):.2f} GB ({sys.get('ram_p', 0):.1f}%)\n"
                  f"[bold bright_magenta]Disk Space:[/]  Blocks: {sys.get('blocks_gb', 0):.2f} GB | Chainstate: {sys.get('chain_gb', 0):.2f} GB")
        
        b_info = (f"[bold bright_yellow]Height:[/]     {bc.get('height', 0)} (Synced: {bc.get('sync', 0):.2f}%)\n"
                  f"[bold bright_yellow]Difficulty:[/] SHA256d: {bc.get('diff_sha', '0')} | Scrypt: {bc.get('diff_scrypt', '0')}\n"
                  f"[bold bright_yellow]Net Hash:[/]   {bc.get('hashrate', 0):.2f} PH/s\n"
                  f"[bold bright_yellow]Uptime:[/]     {bc.get('uptime', '...')}")

        perf_grid.add_row(
            Panel(n_info, title="[bold bright_magenta]Node & System[/]", border_style="bright_magenta"), 
            Panel(b_info, title="[bold bright_yellow]Blockchain Data[/]", border_style="bright_yellow")
        )
        layout["top"].update(perf_grid)
        
        ti, to = len(self.data["peers_in"]), len(self.data["peers_out"])
        status_text = Text.from_markup(f"Total Connected Peers: [bold white]{ti+to}[/]  |  Outbound: [bold bright_cyan]{to}[/]  |  Inbound: [bold bright_green]{ti}[/]  |  Last Updated: [bold bright_white]{self.data['last_update']}[/]")
        layout["mid"].update(Panel(status_text, title="[bold bright_white]Network Status[/]", border_style="bright_white"))

        layout["outbound"].update(self.create_peer_tables(self.data["peers_out"], "Outbound Peers", "bright_cyan"))
        layout["inbound"].update(self.create_peer_tables(self.data["peers_in"], "Inbound Peers", "bright_green"))
        return layout

    def create_peer_tables(self, peer_list, title, border_color):
        if not peer_list: return Panel(Text("No peers connected", style="dim"), title=f"[bold]{title}[/]", border_style=border_color)
        final_row = Table.grid(padding=(0, 2))
        chunks = [peer_list[i:i + MAX_ROWS_PER_TABLE] for i in range(0, len(peer_list), MAX_ROWS_PER_TABLE)]
        renderable_tables = []
        for chunk_idx, chunk in enumerate(chunks):
            t = Table(show_header=True, header_style="bold bright_white", border_style="bright_black")
            t.add_column("No.", style="bright_white", justify="right")
            t.add_column("IP Address:Port", style="bright_white")
            t.add_column("Ping", justify="right")
            for i, p in enumerate(chunk):
                raw_ping = p.get('pingtime')
                ping_ms = round(raw_ping * 1000) if raw_ping else None
                style = "bright_green" if ping_ms and ping_ms <= 50 else "bright_yellow" if ping_ms and ping_ms <= 150 else "red"
                t.add_row(f"{(chunk_idx * MAX_ROWS_PER_TABLE) + i + 1}.", p.get("addr", "N/A"), f"[{style}]{ping_ms} ms[/]" if ping_ms else "[dim]---[/]")
            renderable_tables.append(t)
        final_row.add_row(*renderable_tables)
        return Panel(final_row, title=f"[bold]{title}[/]", border_style=border_color, expand=True)

def main():
    monitor = DGBNodeMonitor()
    threading.Thread(target=lambda: [monitor.update_data() or time.sleep(REFRESH_RATE) for _ in iter(int, 1)], daemon=True).start()
    with Live(monitor.generate_layout(), screen=True, refresh_per_second=4) as live:
        try:
            while True:
                live.update(monitor.generate_layout())
                time.sleep(0.25)
        except KeyboardInterrupt: pass

if __name__ == "__main__":
    main()
