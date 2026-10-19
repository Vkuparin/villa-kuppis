"""Start a supervised pump app session with memory-only credentials."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from villa_kuppis.pump_app import PumpAppServer, make_demo_service
from villa_kuppis.pump_service import make_live_service

def gui_getpass():
    """Collect once in a local dialog; return fields only to the probe."""
    credentials = None
    position = 0

    def prompt(_label):
        nonlocal credentials, position
        if credentials is None:
            import tkinter as tk
            from tkinter import ttk

            dialog = tk.Tk()
            dialog.title("Villa Kuppis - MELCloud Home login")
            dialog.resizable(False, False)
            dialog.attributes("-topmost", True)
            panel = ttk.Frame(dialog, padding=20)
            panel.grid()
            email = tk.StringVar(dialog)
            password = tk.StringVar(dialog)
            visible = tk.BooleanVar(dialog, value=False)
            result = []

            ttk.Label(panel, text="Email").grid(row=0, column=0, sticky="w")
            email_entry = ttk.Entry(panel, textvariable=email, width=48)
            email_entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(3, 10))
            ttk.Label(panel, text="Password").grid(row=2, column=0, sticky="w")
            password_entry = ttk.Entry(panel, textvariable=password, show="*", width=48)
            password_entry.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(3, 6))
            ttk.Checkbutton(panel, text="Show password", variable=visible,
                            command=lambda: password_entry.configure(show="" if visible.get() else "*")).grid(row=4, column=0, columnspan=2, sticky="w")
            ttk.Label(panel, text="Credentials stay in memory and are not saved.").grid(row=5, column=0, columnspan=2, sticky="w", pady=(12, 14))

            def accept(_event=None):
                if not email.get().strip():
                    email_entry.focus_set()
                    return
                if not password.get():
                    password_entry.focus_set()
                    return
                result.append((email.get().strip(), password.get()))
                dialog.destroy()

            ttk.Button(panel, text="Cancel", command=dialog.destroy).grid(row=6, column=0, sticky="w")
            ttk.Button(panel, text="Sign in", command=accept).grid(row=6, column=1, sticky="e")
            dialog.bind("<Return>", accept)
            dialog.bind("<Escape>", lambda _event: dialog.destroy())
            dialog.update_idletasks()
            x = max(0, (dialog.winfo_screenwidth() - dialog.winfo_reqwidth()) // 2)
            y = max(0, (dialog.winfo_screenheight() - dialog.winfo_reqheight()) // 2)
            dialog.geometry(f"+{x}+{y}")
            email_entry.focus_set()
            dialog.mainloop()
            credentials = result[0] if result else ("", "")
        value = credentials[position] if position < 2 else ""
        position += 1
        return value

    return prompt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--live', action='store_true')
    mode.add_argument('--demo', action='store_true')
    parser.add_argument('--port', type=int, default=8377)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    keys = ('VILLA_KUPPIS_MELCLOUD_USERNAME', 'VILLA_KUPPIS_MELCLOUD_PASSWORD',
            'VILLA_KUPPIS_WEATHER_HOME', 'VILLA_KUPPIS_WEATHER_COTTAGE')
    original = {key: os.environ.get(key) for key in keys}
    for key in keys:
        os.environ.pop(key, None)
    try:
        if args.live:
            prompt = gui_getpass()
            username, password = prompt('Email'), prompt('Password')
            if not username or not password:
                print('Cancelled. No cloud connection was opened.')
                return 0
            os.environ[keys[0]], os.environ[keys[1]] = username, password
        data = tempfile.mkdtemp(prefix='villa-kuppis-supervised-')
        print('Temporary session data:', data, flush=True)
        try:
            service = make_live_service(data) if args.live else make_demo_service(data)
        finally:
            for key in keys[:2]:
                os.environ.pop(key, None)
        server = None
        try:
            server = PumpAppServer(service, port=args.port, demo=args.demo)
            service.start()
            print('Pump app:', server.url, '(Ctrl+C stops)', flush=True)
            webbrowser.open(server.url)
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            print('Stopped. Check the final settings in MELCloud Home.')
        finally:
            if server is not None:
                server.server_close()
            service.close()
        return 0
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

if __name__ == '__main__':
    raise SystemExit(main())
