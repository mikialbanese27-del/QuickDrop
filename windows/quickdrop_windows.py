"""QuickDrop Windows host. Python 3.10+, standard library only."""
import base64
import ctypes
import json
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
import uuid
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import tkinter as tk
from tkinter import messagebox

APP = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'QuickDrop'
APP.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = APP / 'settings.json'
INBOX = APP / 'Ricevuti'
INBOX.mkdir(exist_ok=True)

DEFAULT = {'token': secrets.token_urlsafe(24), 'shortcut': 'CTRL+ALT+S', 'port': 8765}
try:
    config = {**DEFAULT, **json.loads(CONFIG_FILE.read_text('utf-8'))}
except (ValueError, OSError):
    config = DEFAULT.copy()

def save():
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding='utf-8')
save()

pending = {}
pending_lock = threading.Lock()
status_callback = lambda msg: None

def notify(msg):
    print(msg, flush=True)
    status_callback(msg)

def local_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(('192.0.2.1', 9))
        return sock.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        sock.close()


def selected_explorer_paths():
    """Find selection in foreground Explorer, not unrelated Explorer windows."""
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    ps = r'''
$target = [long]__HWND__
$shell = New-Object -ComObject Shell.Application
foreach ($w in $shell.Windows()) {
  try {
    if ([long]$w.HWND -eq $target -and $w.Document) {
      foreach ($x in $w.Document.SelectedItems()) {
        if ($x.Path) { Write-Output $x.Path }
      }
      break
    }
  } catch { }
}
'''
    ps = ps.replace('__HWND__', str(hwnd))
    encoded = base64.b64encode(ps.encode('utf-16le')).decode('ascii')
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    proc = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-STA', '-EncodedCommand', encoded],
                          capture_output=True, text=True, errors='replace', timeout=8,
                          startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or 'PowerShell non disponibile')
    return [Path(line.strip()) for line in proc.stdout.splitlines() if line.strip()]


def queue_files(paths):
    added = 0
    with pending_lock:
        for path in paths:
            if path.is_file():
                item_id = uuid.uuid4().hex
                pending[item_id] = {'path': str(path), 'name': path.name, 'created': time.time()}
                added += 1
    notify(f'{added} file pronti sul telefono' if added else 'Nessun file valido selezionato')


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass

    def reply(self, status, body=b'', mime='application/json'):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if body:
            self.wfile.write(body)

    def authorized(self):
        if not secrets.compare_digest(self.headers.get('X-QuickDrop-Token', ''), config['token']):
            self.reply(401, b'{"error":"unauthorized"}')
            return False
        return True

    def do_GET(self):
        if not self.authorized(): return
        path = urlparse(self.path).path
        if path == '/pending':
            with pending_lock:
                rows = [{'id': k, 'name': v['name']} for k, v in pending.items()]
            self.reply(200, json.dumps(rows, ensure_ascii=False).encode('utf-8'))
        elif path.startswith('/download/'):
            item_id = path.rsplit('/', 1)[-1]
            with pending_lock:
                row = pending.get(item_id)
            if not row:
                self.reply(404); return
            file = Path(row['path'])
            if not file.is_file():
                self.reply(404); return
            try:
                size = file.stat().st_size
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(size))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                with file.open('rb') as src:
                    shutil.copyfileobj(src, self.wfile, 256 * 1024)
            except (OSError, ConnectionError):
                pass
        else:
            self.reply(404)

    def do_POST(self):
        if not self.authorized(): return
        path = urlparse(self.path).path
        if path.startswith('/ack/'):
            with pending_lock:
                pending.pop(path.rsplit('/', 1)[-1], None)
            self.reply(200, b'{}')
            return
        if path != '/upload':
            self.reply(404); return
        try:
            length = int(self.headers.get('Content-Length', '-1'))
            encoded_name = self.headers.get('X-File-Name', '')
            if not encoded_name or not 0 <= length <= 20 * 1024**3:
                self.reply(400, b'{"error":"bad upload"}'); return
            name = base64.urlsafe_b64decode(encoded_name + '=' * (-len(encoded_name) % 4)).decode('utf-8')
            name = Path(name.replace('\\', '/')).name.strip().strip('.')
            if not name or name in ('.', '..'):
                self.reply(400); return
            target = INBOX / name
            if target.exists():
                stem, ext = target.stem, target.suffix
                target = INBOX / f'{stem}_{uuid.uuid4().hex[:6]}{ext}'
            tmp = INBOX / ('.upload_' + uuid.uuid4().hex)
            remaining = length
            try:
                with tmp.open('wb') as dst:
                    while remaining:
                        chunk = self.rfile.read(min(256 * 1024, remaining))
                        if not chunk: raise ConnectionError('upload interrupted')
                        dst.write(chunk)
                        remaining -= len(chunk)
                tmp.rename(target)
            finally:
                tmp.unlink(missing_ok=True)
            notify('Ricevuto da Android: ' + target.name)
            self.reply(200, b'{}')
        except (OSError, ValueError, UnicodeError, ConnectionError):
            self.reply(400, b'{"error":"upload failed"}')


MOD = {'CTRL': 0x0002, 'ALT': 0x0001, 'SHIFT': 0x0004, 'WIN': 0x0008}
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

class ShortcutListener:
    def __init__(self):
        self.thread = None
        self.tid = None
        self.ready = threading.Event()

    def start(self):
        self.ready.clear()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        self.ready.wait(timeout=4)

    def stop(self):
        if self.tid:
            ctypes.windll.user32.PostThreadMessageW(self.tid, WM_QUIT, 0, 0)
            self.thread.join(timeout=3)
            self.tid = None

    def run(self):
        self.tid = ctypes.windll.kernel32.GetCurrentThreadId()
        parts = [s.strip().upper() for s in config['shortcut'].split('+')]
        mods = 0
        for mod in parts[:-1]:
            mods |= MOD[mod]
        key = parts[-1]
        vk = ord(key) if len(key) == 1 and key.isalnum() else 0x70 + int(key[1:]) - 1
        ok = ctypes.windll.user32.RegisterHotKey(None, 1, mods | 0x4000, vk)
        self.ready.set()
        if not ok:
            notify('Shortcut già occupata: ' + config['shortcut']); return
        msg = wintypes.MSG()
        try:
            while ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    # HWND changes when the shortcut fires in certain Explorer configurations;
                    # get selection promptly on a worker so the event loop stays responsive.
                    threading.Thread(target=self.send_selection, daemon=True).start()
        finally:
            ctypes.windll.user32.UnregisterHotKey(None, 1)

    @staticmethod
    def send_selection():
        try:
            queue_files(selected_explorer_paths())
        except Exception as exc:
            notify('Errore selezione: ' + str(exc))


def main():
    server = ThreadingHTTPServer(('0.0.0.0', config['port']), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    listener = ShortcutListener()
    listener.start()

    root = tk.Tk()
    root.title('QuickDrop - Windows')
    root.geometry('520x330')
    root.resizable(False, False)
    tk.Label(root, text='QuickDrop', font=('Segoe UI', 22, 'bold')).pack(pady=(14, 4))
    tk.Label(root, text=f'Indirizzo PC: {local_ip()}:{config["port"]}', font=('Segoe UI', 12)).pack()
    tk.Label(root, text='Codice di associazione (solo dispositivi fidati):').pack(pady=(12, 2))
    code = tk.Entry(root, width=42, justify='center')
    code.insert(0, config['token'])
    code.configure(state='readonly')
    code.pack()
    tk.Label(root, text='Scorciatoia di Esplora file:').pack(pady=(12, 2))
    hotkey_entry = tk.Entry(root, justify='center', width=24)
    hotkey_entry.insert(0, config['shortcut'])
    hotkey_entry.pack()
    status = tk.StringVar(value='Pronto. Seleziona file in Esplora file e usa la scorciatoia.')
    def ui_notify(msg):
        root.after(0, lambda: status.set(msg))
    global status_callback
    status_callback = ui_notify

    def update_hotkey():
        new = hotkey_entry.get().strip().upper().replace(' ', '')
        parts = new.split('+')
        if len(parts) < 2 or not all(p in MOD for p in parts[:-1]) or not (len(parts[-1]) == 1 and parts[-1].isalnum() or parts[-1] in [f'F{i}' for i in range(1, 13)]):
            messagebox.showerror('Scorciatoia non valida', 'Esempio: CTRL+ALT+S oppure CTRL+SHIFT+F8')
            return
        listener.stop()
        config['shortcut'] = new
        save()
        listener.start()
        status.set('Nuova scorciatoia: ' + new)

    tk.Button(root, text='Salva scorciatoia', command=update_hotkey).pack(pady=6)
    tk.Button(root, text='Apri cartella ricevuti', command=lambda: os.startfile(INBOX)).pack()
    tk.Label(root, textvariable=status, wraplength=490, fg='#185b83').pack(pady=16)
    tk.Label(root, text='Mantieni aperta questa finestra. Usa solo una rete Wi-Fi fidata.', fg='#666').pack()
    def on_close():
        listener.stop()
        server.shutdown()
        root.destroy()
    root.protocol('WM_DELETE_WINDOW', on_close)
    root.mainloop()

if __name__ == '__main__':
    if os.name != 'nt':
        raise SystemExit('QuickDrop Windows richiede Windows 10/11')
    main()
