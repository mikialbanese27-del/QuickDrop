"""QuickDrop Windows host. Python 3.10+, standard library only."""
import base64
import ctypes
import json
import queue
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
import uuid
import sys
import winsound
import io
import math
import struct
import wave
import winreg
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import tkinter as tk
from tkinter import messagebox, filedialog, ttk

APP = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'QuickDrop'
APP.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = APP / 'settings.json'
DEFAULT = {'token': secrets.token_urlsafe(24), 'shortcut': 'CTRL+ALT+S', 'port': 8765, 'inbox': str(Path.home() / 'Desktop' / 'QuickDrop Ricevuti')}
try:
    config = {**DEFAULT, **json.loads(CONFIG_FILE.read_text('utf-8'))}
except (ValueError, OSError):
    config = DEFAULT.copy()
INBOX = Path(config['inbox']).expanduser()
INBOX.mkdir(parents=True, exist_ok=True)

def save():
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding='utf-8')
save()

pending = {}
pending_lock = threading.Lock()
ui_events = queue.Queue()
status_callback = lambda msg: ui_events.put(('status', msg))

def autostart_key():
    return r'Software\Microsoft\Windows\CurrentVersion\Run'

def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, autostart_key()) as key:
            winreg.QueryValueEx(key, 'QuickDrop')
        return True
    except OSError:
        return False

def set_autostart(enabled):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, autostart_key(), 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            exe = Path(sys.executable)
            if exe.name.lower() == 'python.exe' and exe.with_name('pythonw.exe').is_file():
                exe = exe.with_name('pythonw.exe')
            command = f'"{exe}" "{Path(__file__).resolve()}"'
            winreg.SetValueEx(key, 'QuickDrop', 0, winreg.REG_SZ, command)
        else:
            try: winreg.DeleteValue(key, 'QuickDrop')
            except FileNotFoundError: pass


def notify(msg):
    print(msg, flush=True)
    status_callback(msg)

def gentle_sent_sound():
    """Short, quiet, two-tone WAV chime; played off the UI/server threads."""
    try:
        sample_rate = 22050
        duration = 0.26
        wav_bytes = io.BytesIO()
        with wave.open(wav_bytes, 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(sample_rate)
            samples = bytearray()
            for i in range(int(sample_rate * duration)):
                t = i / sample_rate
                # Rounded bell-like tones with a gentle attack and release.
                frequency = 670.0 if t < 0.12 else 845.0
                local = t if t < 0.12 else t - 0.12
                segment_length = 0.12 if t < 0.12 else duration - 0.12
                envelope = min(1.0, local / 0.015) * max(0.0, (segment_length - local) / 0.075)
                value = 0.12 * envelope * (math.sin(2 * math.pi * frequency * t)
                            + 0.17 * math.sin(2 * math.pi * frequency * 2 * t))
                samples.extend(struct.pack('<h', int(max(-1, min(1, value)) * 32767)))
            audio.writeframes(samples)
        winsound.PlaySound(wav_bytes.getvalue(), winsound.SND_MEMORY | winsound.SND_NODEFAULT)
    except (RuntimeError, OSError, ValueError):
        pass

def play_sent_sound():
    threading.Thread(target=gentle_sent_sound, daemon=True).start()

def local_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(('192.0.2.1', 9))
        return sock.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        sock.close()


def selected_explorer_paths(hwnd):
    """Find selection in Explorer window active at the moment of the shortcut."""
    ps = r'''
$target = [long]__HWND__
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class QDWin {
  [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr hwnd, uint flags);
}
'@
$root = [QDWin]::GetAncestor([IntPtr]$target, 2).ToInt64()
$shell = New-Object -ComObject Shell.Application
foreach ($w in $shell.Windows()) {
  try {
    $window = [long]$w.HWND
    if (($window -eq $target -or $window -eq $root) -and $w.Document) {
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
    if added:
        play_sent_sound()


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
                ui_events.put(('out_progress', (file.name, 0, size)))
                sent = 0
                with file.open('rb') as src:
                    while True:
                        chunk = src.read(256 * 1024)
                        if not chunk: break
                        self.wfile.write(chunk)
                        sent += len(chunk)
                        ui_events.put(('out_progress', (file.name, sent, size)))
                ui_events.put(('out_done', file.name))
            except (OSError, ConnectionError):
                pass
        else:
            self.reply(404)

    def do_POST(self):
        if not self.authorized(): return
        path = urlparse(self.path).path
        if path == '/queue-local':
            # Restrict the local quick-drop endpoint to this Windows computer.
            if self.client_address[0] not in ('127.0.0.1', '::1'):
                self.reply(403); return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length < 65536:
                    self.reply(400); return
                data = json.loads(self.rfile.read(length).decode('utf-8'))
                paths = data.get('paths', [])
                if not isinstance(paths, list) or len(paths) > 100 or any(not isinstance(p, str) for p in paths):
                    self.reply(400); return
                queue_files([Path(p) for p in paths])
                self.reply(200, b'{}')
            except (ValueError, UnicodeError, OSError, TypeError):
                self.reply(400)
            return
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
            ui_events.put(('progress', (name, 0, length)))
            try:
                with tmp.open('wb') as dst:
                    while remaining:
                        chunk = self.rfile.read(min(256 * 1024, remaining))
                        if not chunk: raise ConnectionError('upload interrupted')
                        dst.write(chunk)
                        remaining -= len(chunk)
                        ui_events.put(('progress', (name, length - remaining, length)))
                tmp.rename(target)
            finally:
                tmp.unlink(missing_ok=True)
            notify('Ricevuto da Android: ' + target.name)
            ui_events.put(('received', target.name))
            ui_events.put(('progress_done', target.name))
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
                    hwnd = ctypes.windll.user32.GetForegroundWindow()
                    threading.Thread(target=self.send_selection, args=(hwnd,), daemon=True).start()
        finally:
            ctypes.windll.user32.UnregisterHotKey(None, 1)

    @staticmethod
    def send_selection(hwnd):
        try:
            paths = selected_explorer_paths(hwnd)
            if not paths:
                notify('Nessun file selezionato in Esplora file: apri una cartella e seleziona un file')
                return
            queue_files(paths)
        except Exception as exc:
            notify('Errore selezione: ' + str(exc))


def main():
    server = ThreadingHTTPServer(('0.0.0.0', config['port']), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    listener = ShortcutListener()
    listener.start()
    # Drag-and-drop helper runs independently with an OLE-capable WinForms drop target.
    drop_helper = None
    helper_path = Path(__file__).with_name('quickdrop_dropzone.ps1')
    if helper_path.is_file():
        try:
            child_env = os.environ.copy()
            child_env['QUICKDROP_PORT'] = str(config['port'])
            child_env['QUICKDROP_TOKEN'] = config['token']
            drop_helper = subprocess.Popen(
                ['powershell.exe', '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File', str(helper_path)],
                env=child_env, creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError as exc:
            notify('Area trascinamento non avviata: ' + str(exc))

    root = tk.Tk()
    root.title('QuickDrop - Windows')
    root.geometry('560x465')
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
    def show_received_popup(filename):
        popup = tk.Toplevel(root)
        popup.overrideredirect(True)
        popup.attributes('-topmost', True)
        popup.configure(bg='#173a50')
        w, h = 360, 100
        x = root.winfo_screenwidth() - w - 30
        y = root.winfo_screenheight() - h - 85
        popup.geometry(f'{w}x{h}+{x}+{y}')
        tk.Label(popup, text='QuickDrop - File ricevuto', font=('Segoe UI', 12, 'bold'),
                 bg='#173a50', fg='white').pack(pady=(14, 3))
        tk.Label(popup, text=filename, font=('Segoe UI', 10), bg='#173a50', fg='white',
                 wraplength=320).pack()
        popup.after(4300, popup.destroy)

    def poll_ui_events():
        try:
            for _ in range(30):
                kind, value = ui_events.get_nowait()
                if kind == 'status':
                    status.set(value)
                elif kind == 'received':
                    show_received_popup(value)
                elif kind == 'progress':
                    name, done, total = value
                    pct = min(100, int(100 * done / total)) if total else 100
                    progress_value.set(pct)
                    progress_text.set(f'Ricezione {name}: {pct}%')
                elif kind == 'progress_done':
                    progress_value.set(100)
                    progress_text.set('Ricevuto: ' + value)
                elif kind == 'out_progress':
                    name, done, total = value
                    pct = min(100, int(100 * done / total)) if total else 100
                    progress_value.set(pct)
                    progress_text.set(f'Invio al telefono {name}: {pct}%')
                elif kind == 'out_done':
                    progress_value.set(100)
                    progress_text.set('Inviato al telefono: ' + value)
        except queue.Empty:
            pass
        root.after(100, poll_ui_events)

    global status_callback
    status_callback = lambda msg: ui_events.put(('status', msg))
    root.after(100, poll_ui_events)

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

    tk.Label(root, text='Cartella file ricevuti:').pack()
    folder_name = tk.StringVar(value=str(INBOX))
    tk.Label(root, textvariable=folder_name, wraplength=510, fg='#345').pack()
    def change_folder():
        global INBOX
        selected = filedialog.askdirectory(initialdir=str(INBOX), title='Seleziona la cartella QuickDrop')
        if selected:
            INBOX = Path(selected)
            INBOX.mkdir(parents=True, exist_ok=True)
            config['inbox'] = str(INBOX)
            save()
            folder_name.set(str(INBOX))
    tk.Button(root, text='Cambia cartella', command=change_folder).pack(pady=3)
    auto_flag = tk.BooleanVar(value=autostart_enabled())
    def change_autostart():
        try:
            set_autostart(auto_flag.get())
        except OSError as exc:
            auto_flag.set(autostart_enabled())
            messagebox.showerror('Avvio con Windows', str(exc))
    tk.Checkbutton(root, text='Avvia automaticamente con Windows', variable=auto_flag, command=change_autostart).pack()
    progress_value = tk.IntVar(value=0)
    progress_text = tk.StringVar(value='Nessun trasferimento in corso')
    ttk.Progressbar(root, variable=progress_value, length=430, maximum=100).pack(pady=(7, 2))
    tk.Label(root, textvariable=progress_text, wraplength=500).pack()

    tk.Button(root, text='Apri cartella ricevuti', command=lambda: os.startfile(INBOX)).pack()
    tk.Label(root, textvariable=status, wraplength=490, fg='#185b83').pack(pady=16)
    tk.Label(root, text='Puoi minimizzare la finestra, QuickDrop continua a funzionare.', fg='#666').pack()
    def on_close():
        if drop_helper is not None and drop_helper.poll() is None:
            drop_helper.terminate()
        listener.stop()
        server.shutdown()
        root.destroy()
    root.protocol('WM_DELETE_WINDOW', on_close)
    root.mainloop()

if __name__ == '__main__':
    if os.name != 'nt':
        raise SystemExit('QuickDrop Windows richiede Windows 10/11')
    main()
