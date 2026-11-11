#!/usr/bin/env python3
"""FATUR CODE STUDIO - IDE Python offline untuk Android (Kivy)."""

import json
import os
import re
import runpy
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback

import kivy
from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp, sp
from kivy.properties import ListProperty, StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.slider import Slider
from kivy.uix.spinner import Spinner
from kivy.uix.switch import Switch
from kivy.uix.textinput import TextInput
from kivy.utils import platform

APP_NAME = "FATUR CODE STUDIO"
APP_VERSION = "1.0.0"
BUILD_STATUS = "Belum diverifikasi"
MAX_FILE = 1024 * 1024
OUTPUT_LIMIT = 2 * 1024 * 1024
ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
BAD_CHARS = set('/\\:*?"<>|')

THEMES = {
    "dark": {
        "bg": (0.055, 0.065, 0.085, 1), "panel": (0.10, 0.12, 0.15, 1),
        "text": (0.90, 0.92, 0.95, 1), "muted": (0.62, 0.67, 0.74, 1),
        "accent": (0.30, 0.55, 1.0, 1), "input": (0.13, 0.15, 0.19, 1),
        "selection": (0.20, 0.35, 0.65, 1),
    },
    "light": {
        "bg": (0.96, 0.97, 0.98, 1), "panel": (1, 1, 1, 1),
        "text": (0.10, 0.12, 0.15, 1), "muted": (0.38, 0.42, 0.48, 1),
        "accent": (0.15, 0.39, 0.92, 1), "input": (1, 1, 1, 1),
        "selection": (0.75, 0.85, 1.0, 1),
    },
}

ABOUT_TEXT = (
    "FATUR CODE STUDIO\n\n"
    "IDE Python offline untuk Android. Menulis, menyimpan, menjalankan, dan "
    "menghentikan skrip Python langsung di ponsel.\n\n"
    "Privasi: offline, tanpa iklan, tanpa analytics, tanpa akun, dan tidak "
    "mengirim data ke server.\n\n"
    "Penyimpanan: proyek berada di penyimpanan privat aplikasi. Menghapus "
    "instalasi aplikasi akan menghapus seluruh proyek.\n\n"
    "Batas eksekusi: kode berjalan sebagai proses terpisah dengan izin "
    "aplikasi, BUKAN sandbox keamanan. Jalankan hanya kode yang dipercaya.\n\n"
    "Terminal adalah konsol eksekusi Python, bukan shell sistem. Tidak ada "
    "perintah shell, pip, atau eksekusi perintah arbitrer.\n\n"
    "Lisensi pihak ketiga: Python, Kivy, Pygments (opsional).\n\n"
    "Versi aplikasi: %s\nStatus build: %s" % (APP_VERSION, BUILD_STATUS)
)


def atomic_write(path, text):
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def validate_name(raw, is_file):
    name = (raw or "").strip()
    if not name:
        raise ValueError("Nama tidak boleh kosong.")
    if len(name) > 64:
        raise ValueError("Nama maksimal 64 karakter.")
    if name.startswith("."):
        raise ValueError("Nama tidak boleh diawali titik.")
    if ".." in name:
        raise ValueError("Nama tidak boleh mengandung '..'.")
    if any(ord(c) < 32 or ord(c) == 127 or c in BAD_CHARS for c in name):
        raise ValueError("Nama mengandung karakter yang tidak diizinkan.")
    if is_file and not name.lower().endswith(".py"):
        name += ".py"
        if len(name) > 64:
            raise ValueError("Nama maksimal 64 karakter.")
    return name


def sanitize_output(text):
    text = ANSI_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        ch for ch in text
        if ch in ("\n", "\t") or (ord(ch) >= 32 and ord(ch) != 127)
    )


def shorten(text, limit=30):
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)] + "..."


class ColorBox(BoxLayout):
    bg = ListProperty([0, 0, 0, 1])

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas.before:
            self._bg_color = Color(rgba=self.bg)
            self._bg_rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._update_bg, size=self._update_bg, bg=self._update_bg)

    def _update_bg(self, *args):
        self._bg_color.rgba = self.bg
        self._bg_rect.pos = self.pos
        self._bg_rect.size = self.size


class ConfigStore:
    DEFAULTS = {
        "schema": 1, "theme": "dark", "font_size": 14,
        "indent_style": "spaces", "indent_size": 4,
        "autosave": True, "autosave_interval": 30, "recent": [],
    }

    def __init__(self, path):
        self.path = path
        self.broken = False
        self.data = dict(self.DEFAULTS)
        self.data["recent"] = []

    def load(self):
        data = dict(self.DEFAULTS)
        data["recent"] = []
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if not isinstance(raw, dict):
                raise ValueError("Pengaturan bukan objek JSON.")
            if raw.get("theme") in ("dark", "light"):
                data["theme"] = raw["theme"]
            try:
                data["font_size"] = min(24, max(10, int(raw.get("font_size", 14))))
            except (TypeError, ValueError):
                pass
            if raw.get("indent_style") in ("spaces", "tab"):
                data["indent_style"] = raw["indent_style"]
            try:
                size = int(raw.get("indent_size", 4))
                if size in (2, 4, 8):
                    data["indent_size"] = size
            except (TypeError, ValueError):
                pass
            if isinstance(raw.get("autosave"), bool):
                data["autosave"] = raw["autosave"]
            try:
                interval = int(raw.get("autosave_interval", 30))
                if interval in (15, 30, 60):
                    data["autosave_interval"] = interval
            except (TypeError, ValueError):
                pass
            recent = raw.get("recent")
            if isinstance(recent, list):
                data["recent"] = [x for x in recent if isinstance(x, str)][:20]
        except FileNotFoundError:
            pass
        except Exception:
            self.broken = True
        self.data = data

    def save(self):
        atomic_write(self.path, json.dumps(self.data, indent=2, ensure_ascii=False))


class ProcessRunner:
    def __init__(self, app):
        self.app = app
        self.running = False
        self.error = None
        self.path = None
        self.cwd = None
        self.pid = None
        self.started_at = 0.0
        self._proc = None
        self._stdin_fd = None
        self._stopped = False
        self._finished = True
        self._limit_hit = False
        self._bytes = 0
        self._lock = threading.Lock()

    def start(self, path, cwd):
        if self.running:
            return False
        self.error = None
        self.path = path
        self.cwd = cwd
        self.pid = None
        self._proc = None
        self._stdin_fd = None
        self._stopped = False
        self._finished = False
        self._limit_hit = False
        self._bytes = 0
        self.started_at = time.time()
        self.running = True
        try:
            if platform == "android":
                self._start_fork()
            else:
                self._start_subprocess()
        except Exception as exc:
            self.running = False
            self._finished = True
            self.error = str(exc) or exc.__class__.__name__
            self._close_stdin()
            return False
        return True

    def _start_subprocess(self):
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        self._proc = subprocess.Popen(
            [sys.executable, "-u", self.path], cwd=self.cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, start_new_session=True,
        )
        self.pid = self._proc.pid
        threading.Thread(target=self._read_stream,
                         args=("stdout", self._proc.stdout), daemon=True).start()
        threading.Thread(target=self._read_stream,
                         args=("stderr", self._proc.stderr), daemon=True).start()
        threading.Thread(target=self._wait_subprocess, daemon=True).start()

    def _start_fork(self):
        read_in, write_in = os.pipe()
        read_out, write_out = os.pipe()
        read_err, write_err = os.pipe()
        pid = os.fork()
        if pid == 0:
            try:
                os.close(write_in)
                os.close(read_out)
                os.close(read_err)
                os.dup2(read_in, 0)
                os.dup2(write_out, 1)
                os.dup2(write_err, 2)
                os.close(read_in)
                os.close(write_out)
                os.close(write_err)
                os.chdir(self.cwd)
                sys.path.insert(0, self.cwd)
                sys.argv = [self.path]
                try:
                    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
                except Exception:
                    pass
                try:
                    sys.stdout.reconfigure(encoding="utf-8", errors="replace",
                                           line_buffering=True, write_through=True)
                    sys.stderr.reconfigure(encoding="utf-8", errors="replace",
                                           line_buffering=True, write_through=True)
                except Exception:
                    pass
                code = 0
                try:
                    runpy.run_path(self.path, run_name="__main__")
                except SystemExit as exc:
                    if exc.code is None:
                        code = 0
                    elif isinstance(exc.code, int):
                        code = exc.code
                    else:
                        code = 1
                except BaseException:
                    traceback.print_exc()
                    code = 1
                try:
                    sys.stdout.flush()
                    sys.stderr.flush()
                except Exception:
                    pass
                os._exit(int(code) & 0xFF)
            except BaseException:
                os._exit(1)
        os.close(read_in)
        os.close(write_out)
        os.close(write_err)
        self.pid = pid
        self._stdin_fd = write_in
        threading.Thread(target=self._read_fd, args=("stdout", read_out), daemon=True).start()
        threading.Thread(target=self._read_fd, args=("stderr", read_err), daemon=True).start()
        threading.Thread(target=self._wait_fork, args=(pid,), daemon=True).start()

    def _read_stream(self, name, stream):
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                self._emit(name, chunk.decode("utf-8", "replace"))
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _read_fd(self, name, fd):
        try:
            while True:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                self._emit(name, chunk.decode("utf-8", "replace"))
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def _wait_subprocess(self):
        proc = self._proc
        try:
            code = proc.wait() if proc is not None else -1
        except Exception:
            code = -1
        self._finish(code)

    def _wait_fork(self, pid):
        try:
            _, status = os.waitpid(pid, 0)
            if os.WIFEXITED(status):
                code = os.WEXITSTATUS(status)
            elif os.WIFSIGNALED(status):
                code = 128 + os.WTERMSIG(status)
            else:
                code = -1
        except OSError:
            code = -1
        self._finish(code)

    def _emit(self, stream, text):
        if not text:
            return
        warn = False
        with self._lock:
            if self._limit_hit:
                return
            raw = text.encode("utf-8", "replace")
            remaining = OUTPUT_LIMIT - self._bytes
            if remaining <= 0:
                self._limit_hit = True
                warn = True
                text = ""
            elif len(raw) > remaining:
                text = raw[:remaining].decode("utf-8", "ignore")
                self._limit_hit = True
                warn = True
            self._bytes += len(text.encode("utf-8", "replace"))
        if text:
            self.app.on_process_output(stream, text)
        if warn:
            self.app.on_process_limit()

    def _finish(self, code):
        with self._lock:
            if self._finished:
                return
            self._finished = True
            self.running = False
            stopped = self._stopped
            duration = time.time() - self.started_at
            self.pid = None
            self._proc = None
        self._close_stdin()
        self.app.on_process_finished(code, duration, stopped)

    def _close_stdin(self):
        fd = self._stdin_fd
        self._stdin_fd = None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        proc = self._proc
        if proc is not None and proc.stdin is not None and not proc.stdin.closed:
            try:
                proc.stdin.close()
            except Exception:
                pass

    def send_input(self, text):
        if not self.running:
            return False
        data = (text + "\n").encode("utf-8")
        if platform == "android" and self._stdin_fd is not None:
            try:
                os.write(self._stdin_fd, data)
                return True
            except OSError:
                return False
        proc = self._proc
        if proc is not None and proc.stdin is not None:
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
                return True
            except Exception:
                return False
        return False

    def close_stdin(self):
        self._close_stdin()

    def stop(self):
        if not self.running:
            return
        self._stopped = True
        self.app.on_process_stopping()
        if platform == "android":
            pid = self.pid
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    return
                threading.Thread(target=self._kill_fork_later,
                                 args=(pid,), daemon=True).start()
        else:
            proc = self._proc
            if proc is not None:
                try:
                    proc.terminate()
                except Exception:
                    return
                threading.Thread(target=self._kill_proc_later,
                                 args=(proc,), daemon=True).start()

    def _kill_fork_later(self, pid):
        time.sleep(1.5)
        try:
            os.kill(pid, 0)
        except OSError:
            return
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    def _kill_proc_later(self, proc):
        time.sleep(1.5)
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass

class CodeInput(TextInput):
    indent_unit = StringProperty("    ")

    def insert_text(self, substring, from_undo=False):
        if substring == "\n":
            self._auto_newline(from_undo)
            return
        if substring == "\t":
            super().insert_text(self.indent_unit, from_undo)
            return
        super().insert_text(substring, from_undo)

    def _auto_newline(self, from_undo):
        index = self.cursor_index()
        before = self.text[:index]
        line_start = before.rfind("\n") + 1
        line = before[line_start:]
        indent = line[: len(line) - len(line.lstrip(" \t"))]
        if line.rstrip().endswith(":"):
            indent += self.indent_unit
        super().insert_text("\n" + indent, from_undo)


def wrapped_label(app, text, **kwargs):
    kwargs.setdefault("halign", "left")
    kwargs.setdefault("valign", "top")
    label = app.label(text, **kwargs)
    label.size_hint_y = None
    label.text_size = (label.width, None)
    label.bind(width=lambda inst, value: setattr(inst, "text_size", (value, None)))
    label.bind(texture_size=lambda inst, value: setattr(inst, "height", value[1]))
    return label


class HomeScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.root = ColorBox(orientation="vertical", padding=dp(18),
                             spacing=dp(10), bg=app.palette["bg"])
        self.add_widget(self.root)
        self.root.add_widget(app.label(APP_NAME, font_size=sp(26),
                                       size_hint_y=None, height=dp(48)))
        self.root.add_widget(app.label(
            "Python offline di kantong Anda. Tanpa iklan, tanpa akun.",
            muted=True, font_size=sp(13), size_hint_y=None, height=dp(36)))
        for text, callback in (("Proyek Baru", app.new_project),
                               ("Buka Proyek", app.show_projects)):
            button = Button(text=text, size_hint_y=None, height=dp(50),
                            font_size=sp(16))
            button.bind(on_release=lambda inst, cb=callback: cb())
            self.root.add_widget(button)
        self.root.add_widget(app.label("Proyek Terakhir", font_size=sp(14),
                                       muted=True, size_hint_y=None,
                                       height=dp(30)))
        scroll = ScrollView(size_hint_y=1)
        self.grid = GridLayout(cols=1, size_hint_y=None, spacing=dp(6))
        self.grid.bind(minimum_height=self.grid.setter("height"))
        scroll.add_widget(self.grid)
        self.root.add_widget(scroll)
        bottom = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        for text, callback in (("Pengaturan", app.open_settings),
                               ("Tentang", app.show_about)):
            button = Button(text=text, font_size=sp(14))
            button.bind(on_release=lambda inst, cb=callback: cb())
            bottom.add_widget(button)
        self.root.add_widget(bottom)
        self.term_button = Button(text="Proses berjalan - buka Terminal",
                                  size_hint_y=None, height=0, opacity=0,
                                  disabled=True)
        self.term_button.bind(on_release=lambda *a: app.show_terminal())
        self.root.add_widget(self.term_button)

    def on_pre_enter(self, *args):
        self.refresh()
        running = self.app.runner.running
        self.term_button.height = dp(46) if running else 0
        self.term_button.opacity = 1 if running else 0
        self.term_button.disabled = not running

    def refresh(self):
        self.grid.clear_widgets()
        names = self.app.recent_projects()
        if not names:
            self.grid.add_widget(self.app.label(
                "Belum ada proyek. Mulai dengan Proyek Baru.",
                muted=True, size_hint_y=None, height=dp(44)))
            return
        for name in names:
            button = Button(text=shorten(name, 34), size_hint_y=None,
                            height=dp(50), font_size=sp(14))
            button.bind(on_release=lambda inst, n=name: self.app.open_project(n))
            self.grid.add_widget(button)

    def apply_theme(self):
        self.root.bg = self.app.palette["bg"]


class ProjectsScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.mode = "projects"
        self.project = None
        self.root = ColorBox(orientation="vertical", bg=app.palette["bg"])
        self.add_widget(self.root)
        header = BoxLayout(size_hint_y=None, height=dp(56), spacing=dp(6),
                           padding=(dp(6), 0))
        back = Button(text="<", size_hint=(None, 1), width=dp(50),
                      font_size=sp(18))
        back.bind(on_release=lambda *a: self.back())
        header.add_widget(back)
        self.title_label = app.label("Proyek", font_size=sp(17))
        header.add_widget(self.title_label)
        self.new_button = Button(text="Proyek Baru", size_hint=(None, 1),
                                 width=dp(112), font_size=sp(13))
        self.new_button.bind(on_release=lambda *a: self._new())
        header.add_widget(self.new_button)
        self.root.add_widget(header)
        self.scroll = ScrollView()
        self.grid = GridLayout(cols=1, size_hint_y=None, spacing=dp(6),
                               padding=dp(6))
        self.grid.bind(minimum_height=self.grid.setter("height"))
        self.scroll.add_widget(self.grid)
        self.root.add_widget(self.scroll)

    def on_pre_enter(self, *args):
        self.refresh()

    def back(self):
        if self.mode == "files":
            self.mode = "projects"
            self.project = None
            self.refresh()
        else:
            self.app.go_home()

    def _new(self):
        if self.mode == "files" and self.project:
            self.app.new_file(self.project)
        else:
            self.app.new_project()

    def refresh(self):
        self.grid.clear_widgets()
        if self.mode == "files" and self.project:
            self.title_label.text = "Proyek: " + shorten(self.project, 18)
            self.new_button.text = "File Baru"
            names = self.app.list_files(self.project)
            if not names:
                label = self.app.label("Proyek kosong. Buat File Baru.",
                                       muted=True, size_hint_y=None,
                                       height=dp(44))
                self.grid.add_widget(label)
                return
            for name in names:
                self.grid.add_widget(self._row(name, True))
        else:
            self.title_label.text = "Proyek"
            self.new_button.text = "Proyek Baru"
            names = self.app.list_projects()
            if not names:
                label = self.app.label("Belum ada proyek.",
                                       muted=True, size_hint_y=None,
                                       height=dp(44))
                self.grid.add_widget(label)
                return
            for name in names:
                self.grid.add_widget(self._row(name, False))

    def _row(self, name, is_file):
        row = ColorBox(orientation="horizontal", size_hint_y=None,
                       height=dp(56), bg=self.app.palette["panel"],
                       padding=dp(6), spacing=dp(6))
        row.add_widget(self.app.label(shorten(name, 28), size_hint_x=1))
        open_button = Button(text="Buka", size_hint=(None, 1), width=dp(62),
                             font_size=sp(13))
        if is_file:
            open_button.bind(
                on_release=lambda inst, n=name: self.app.open_file(self.project, n))
        else:
            open_button.bind(
                on_release=lambda inst, n=name: self.app.open_project(n))
        row.add_widget(open_button)
        menu_button = Button(text="...", size_hint=(None, 1), width=dp(42),
                             font_size=sp(13))
        if is_file:
            menu_button.bind(
                on_release=lambda inst, n=name: self.app.item_menu(
                    "file", self.project, n))
        else:
            menu_button.bind(
                on_release=lambda inst, n=name: self.app.item_menu(
                    "project", None, n))
        row.add_widget(menu_button)
        return row

    def apply_theme(self):
        self.root.bg = self.app.palette["bg"]
        self.refresh()


class EditorScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.project = None
        self.filename = None
        self.file_path = None
        self.read_only = False
        self.dirty = False
        self.last_edit = 0.0
        self._loading = False
        self._find_pos = 0
        self.root = ColorBox(orientation="vertical", bg=app.palette["bg"])
        self.add_widget(self.root)

        header = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(6),
                           padding=(dp(6), 0))
        back = Button(text="<", size_hint=(None, 1), width=dp(46),
                      font_size=sp(18))
        back.bind(on_release=lambda *a: self.back())
        header.add_widget(back)
        self.title_label = app.label("Editor", font_size=sp(15))
        header.add_widget(self.title_label)
        terminal_button = Button(text="Terminal", size_hint=(None, 1),
                                 width=dp(88), font_size=sp(13))
        terminal_button.bind(on_release=lambda *a: app.show_terminal())
        header.add_widget(terminal_button)
        self.root.add_widget(header)

        toolbar_scroll = ScrollView(size_hint_y=None, height=dp(50),
                                    do_scroll_y=False)
        toolbar = BoxLayout(orientation="horizontal", size_hint=(None, 1),
                            spacing=dp(6), padding=(dp(6), dp(2)))
        toolbar.bind(minimum_width=lambda inst, value: setattr(inst, "width", value))
        toolbar_scroll.add_widget(toolbar)
        self.root.add_widget(toolbar_scroll)
        self.run_button = None
        for text, width, callback in (
            ("Simpan", 80, self.save_file),
            ("Simpan-Sebagai", 122, self.save_as),
            ("Jalankan", 88, self.run),
            ("Undo", 64, lambda: self.input.do_undo()),
            ("Redo", 64, lambda: self.input.do_redo()),
            ("Cari", 64, self.show_find),
        ):
            button = Button(text=text, size_hint=(None, 1), width=dp(width),
                            font_size=sp(13))
            button.bind(on_release=lambda inst, cb=callback: cb())
            toolbar.add_widget(button)
            if text == "Jalankan":
                self.run_button = button

        self.find_bar = BoxLayout(size_hint_y=None, height=0, opacity=0,
                                  disabled=True, spacing=dp(4),
                                  padding=(dp(6), dp(2)))
        self.find_input = app.textinput(multiline=False, font_size=sp(14),
                                        hint_text="Cari teks")
        self.find_input.bind(on_text_validate=lambda *a: self.do_find(False))
        self.find_bar.add_widget(self.find_input)
        for text, callback in (("<", lambda: self.do_find(True)),
                               (">", lambda: self.do_find(False)),
                               ("X", self.hide_find)):
            button = Button(text=text, size_hint=(None, 1), width=dp(42),
                            font_size=sp(13))
            button.bind(on_release=lambda inst, cb=callback: cb())
            self.find_bar.add_widget(button)
        self.root.add_widget(self.find_bar)

        self.input = CodeInput(multiline=True, write_tab=True,
                               font_size=sp(app.cfg["font_size"]),
                               background_color=app.palette["input"],
                               foreground_color=app.palette["text"],
                               cursor_color=app.palette["accent"],
                               selection_color=app.palette["selection"],
                               padding=[dp(8)] * 4, tab_width=4)
        app.inputs.append(self.input)
        self.input.bind(text=self._on_text, cursor=self._on_cursor)
        self.root.add_widget(self.input)

        status = BoxLayout(size_hint_y=None, height=dp(30),
                           padding=(dp(8), 0), spacing=dp(4))
        self.pos_label = app.label("Baris 1, Kolom 1", muted=True,
                                   font_size=sp(12))
        self.state_label = app.label("Tersimpan", muted=True,
                                     font_size=sp(12))
        status.add_widget(self.pos_label)
        status.add_widget(self.state_label)
        self.root.add_widget(status)
        self.apply_indent()

    def _on_text(self, instance, value):
        if self._loading:
            return
        self.dirty = True
        self.last_edit = time.time()
        self.update_status()

    def _on_cursor(self, instance, value):
        self.update_status()

    def update_title(self):
        if self.filename:
            self.title_label.text = shorten(self.filename, 18) + " - " + \
                shorten(self.project or "", 10)
        else:
            self.title_label.text = "Editor"

    def update_status(self):
        row = self.input.cursor_row + 1
        col = self.input.cursor_col + 1
        self.pos_label.text = "Baris %d, Kolom %d" % (row, col)
        if self.read_only:
            self.state_label.text = "Hanya baca"
        elif self.dirty:
            self.state_label.text = "Belum disimpan"
        else:
            self.state_label.text = "Tersimpan"

    def flash(self, text):
        self.state_label.text = text
        Clock.schedule_once(lambda dt: self.update_status(), 2.5)

    def apply_font_size(self):
        self.input.font_size = sp(self.app.cfg["font_size"])

    def apply_indent(self):
        if self.app.cfg["indent_style"] == "tab":
            self.input.indent_unit = "\t"
        else:
            self.input.indent_unit = " " * int(self.app.cfg["indent_size"])

    def set_running(self, running):
        if self.run_button is not None:
            self.run_button.disabled = running
            self.run_button.text = "Berjalan..." if running else "Jalankan"

    def load_file(self, project, filename):
        path = self.app.file_path(project, filename)
        if not path or not os.path.isfile(path):
            self.app.message("Gagal membuka", "File tidak ditemukan.")
            return False
        try:
            size = os.path.getsize(path)
        except OSError as exc:
            self.app.message("Gagal membuka", str(exc))
            return False
        if size > MAX_FILE:
            self.app.message("File terlalu besar",
                             "File terlalu besar untuk dibuka di editor ini "
                             "(maksimal 1 MB).")
            return False
        try:
            with open(path, "rb") as handle:
                data = handle.read(MAX_FILE + 1)
        except OSError as exc:
            self.app.message("Gagal membuka", str(exc))
            return False
        if len(data) > MAX_FILE:
            self.app.message("File terlalu besar", "File melebihi 1 MB.")
            return False
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        read_only = False
        note = ""
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", "replace")
            read_only = True
            note = "Encoding tidak valid; file dibuka hanya-baca."
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        self.project = project
        self.filename = filename
        self.file_path = path
        self.read_only = read_only
        self._loading = True
        self.input.text = text
        self.input.reset_undo()
        self.input.cursor = (0, 0)
        self._loading = False
        self.input.readonly = read_only
        self.dirty = False
        self._find_pos = 0
        self.update_title()
        self.update_status()
        if note:
            self.app.message("Peringatan", note)
        return True

    def save_file(self, silent=False):
        if not self.file_path:
            if not silent:
                self.app.message("Simpan",
                                 "File belum memiliki jalur. Gunakan Simpan-Sebagai.")
            return False
        if self.read_only:
            if not silent:
                self.app.message("Hanya baca",
                                 "File ini dibuka hanya-baca. Gunakan "
                                 "Simpan-Sebagai untuk menyimpan ke nama baru.")
            return False
        try:
            atomic_write(self.file_path, self.input.text)
        except OSError as exc:
            if silent:
                self.flash("Autosave gagal")
            else:
                self.app.message("Gagal menyimpan", str(exc))
            return False
        self.dirty = False
        self.update_status()
        return True

    def save_as(self):
        if not self.project:
            self.app.message("Simpan-Sebagai", "Tidak ada proyek aktif.")
            return

        def do(raw):
            name = validate_name(raw, True)
            path = self.app.file_path(self.project, name)
            if not path:
                raise ValueError("Nama file tidak valid.")
            exists = os.path.exists(path)
            same = self.file_path and os.path.abspath(path) == os.path.abspath(self.file_path)

            def write():
                try:
                    atomic_write(path, self.input.text)
                except OSError as exc:
                    self.app.message("Gagal menyimpan", str(exc))
                    return
                self.filename = name
                self.file_path = path
                self.read_only = False
                self.input.readonly = False
                self.dirty = False
                self.update_title()
                self.update_status()
                self.app.projects.refresh()

            if exists and not same:
                self.app.confirm("Timpa File?",
                                 'File "%s" sudah ada. Timpa?' % name,
                                 write, yes_text="Timpa")
            else:
                write()
            return name

        self.app.ask_name("Simpan Sebagai", self.filename or "", do,
                          hint="nama_file.py")

    def show_find(self):
        self.find_bar.height = dp(46)
        self.find_bar.opacity = 1
        self.find_bar.disabled = False
        self.find_input.focus = True

    def hide_find(self):
        self.find_bar.height = 0
        self.find_bar.opacity = 0
        self.find_bar.disabled = True
        self.input.focus = True

    def do_find(self, backwards=False):
        needle = self.find_input.text
        if not needle:
            return
        haystack = self.input.text
        low = haystack.lower()
        target = needle.lower()
        if backwards:
            index = low.rfind(target, 0, max(0, self._find_pos - 1))
            if index < 0:
                index = low.rfind(target)
        else:
            index = low.find(target, self._find_pos)
            if index < 0:
                index = low.find(target)
        if index < 0:
            self.flash("Tidak ditemukan")
            return
        end = index + len(needle)
        self.input.cursor = self.input.get_cursor_from_index(end)
        self.input.select_text(index, end)
        self.input.focus = True
        self._find_pos = end if not backwards else index
        self.flash("Ditemukan")

    def run(self):
        if self.app.runner.running:
            self.app.message("Proses berjalan",
                             "Hentikan proses terlebih dahulu.")
            return
        if not self.file_path:
            self.app.message("Jalankan",
                             "Buka atau simpan file terlebih dahulu.")
            return
        if self.dirty:
            self.app.ask_save_changes(
                on_save=self._save_and_run, on_discard=self._start_run,
                title="Simpan & Jalankan",
                text="File memiliki perubahan yang belum disimpan.")
            return
        self._start_run()

    def _save_and_run(self):
        if self.save_file():
            self._start_run()

    def _start_run(self):
        self.app.start_run(self.project, self.file_path, self.filename)

    def back(self):
        def go():
            if self.project:
                self.app.show_project_files(self.project)
            else:
                self.app.go_home()
        if self.dirty:
            self.app.ask_save_changes(
                on_save=lambda: (self.save_file() and go()),
                on_discard=go)
        else:
            go()

    def reset(self):
        self.project = None
        self.filename = None
        self.file_path = None
        self.read_only = False
        self.dirty = False
        self._loading = True
        self.input.text = ""
        self.input.readonly = False
        self._loading = False
        self.update_title()
        self.update_status()

    def apply_theme(self):
        self.root.bg = self.app.palette["bg"]
        self.input.background_color = self.app.palette["input"]
        self.input.foreground_color = self.app.palette["text"]
        self.input.cursor_color = self.app.palette["accent"]
        self.input.selection_color = self.app.palette["selection"]

class TerminalScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.buffer = ""
        self.follow = True
        self._pending = []
        self._flush_event = None
        self._auto_scroll = False
        self.root = ColorBox(orientation="vertical", bg=app.palette["bg"])
        self.add_widget(self.root)

        header = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(6),
                           padding=(dp(6), 0))
        back = Button(text="<", size_hint=(None, 1), width=dp(46),
                      font_size=sp(18))
        back.bind(on_release=lambda *a: app.show_editor())
        header.add_widget(back)
        header.add_widget(app.label("Terminal", font_size=sp(15)))
        for text, callback in (("Salin", self.copy_output),
                               ("Bersihkan", self.clear_output)):
            button = Button(text=text, size_hint=(None, 1), width=dp(80),
                            font_size=sp(12))
            button.bind(on_release=lambda inst, cb=callback: cb())
            header.add_widget(button)
        self.root.add_widget(header)

        self.status_label = app.label("Siap", muted=True, font_size=sp(12),
                                      size_hint_y=None, height=dp(26))
        self.root.add_widget(self.status_label)

        self.scroll = ScrollView(do_scroll_x=True, do_scroll_y=True)
        self.output = app.label("", font_size=sp(12), size_hint=(None, None),
                                halign="left", valign="top")
        self.output.text_size = (None, None)
        self.output.bind(texture_size=self._size_output)
        self.scroll.add_widget(self.output)
        self.scroll.bind(scroll_y=self._on_scroll)
        self.root.add_widget(self.scroll)

        input_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6),
                              padding=(dp(6), 0))
        self.input = app.textinput(multiline=False, font_size=sp(13),
                                   hint_text="Input untuk program")
        self.input.bind(on_text_validate=lambda *a: self.send_input())
        input_row.add_widget(self.input)
        self.send_button = Button(text="Kirim", size_hint=(None, 1),
                                  width=dp(66), font_size=sp(12))
        self.send_button.bind(on_release=lambda *a: self.send_input())
        input_row.add_widget(self.send_button)
        self.eof_button = Button(text="EOF", size_hint=(None, 1),
                                 width=dp(54), font_size=sp(12))
        self.eof_button.bind(on_release=lambda *a: self.send_eof())
        input_row.add_widget(self.eof_button)
        self.root.add_widget(input_row)

        actions = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6),
                            padding=(dp(6), 0))
        self.stop_button = Button(text="STOP", font_size=sp(13))
        self.stop_button.bind(on_release=lambda *a: app.stop_run())
        actions.add_widget(self.stop_button)
        bottom = Button(text="Ke Bawah", font_size=sp(13))
        bottom.bind(on_release=lambda *a: self.scroll_bottom())
        actions.add_widget(bottom)
        self.root.add_widget(actions)
        self.set_idle("Siap")

    def _size_output(self, *args):
        self.output.width = max(self.output.texture_size[0], self.scroll.width)
        self.output.height = max(self.output.texture_size[1], self.scroll.height)

    def _on_scroll(self, instance, value):
        if not self._auto_scroll:
            self.follow = value <= 0.02

    def scroll_bottom(self):
        self.follow = True
        self._auto_scroll = True
        self.scroll.scroll_y = 0
        Clock.schedule_once(self._end_auto_scroll, 0.05)

    def _end_auto_scroll(self, dt):
        self._auto_scroll = False

    def _append_raw(self, text):
        if not text:
            return
        self._pending.append(text)
        if self._flush_event is None:
            self._flush_event = Clock.schedule_once(self._flush, 0.05)

    def _flush(self, dt):
        self._flush_event = None
        if not self._pending:
            return
        chunk = "".join(self._pending)
        self._pending = []
        self.buffer += chunk
        if len(self.buffer) > OUTPUT_LIMIT:
            self.buffer = self.buffer[:OUTPUT_LIMIT] + \
                "\n[Output dipotong: batas 2 MB tercapai]\n"
        self.output.text = self.buffer
        self.output.texture_update()
        self._size_output()
        if self.follow:
            self.scroll_bottom()

    def add_output(self, stream, text):
        if stream == "stderr":
            text = "[err] " + text
        self._append_raw(sanitize_output(text))

    def add_system(self, text):
        self._append_raw("\n=== " + sanitize_output(str(text)) + " ===\n")

    def start_session(self, filename):
        self.buffer = ""
        self._pending = []
        self.output.text = ""
        self.output.texture_update()
        self.follow = True
        self.scroll_bottom()
        self.status_label.text = "Berjalan"
        self.input.disabled = False
        self.send_button.disabled = False
        self.eof_button.disabled = False
        self.stop_button.disabled = False
        self.add_system("RUN - " + filename)

    def set_idle(self, status):
        if status:
            self.status_label.text = status
        self.input.disabled = True
        self.send_button.disabled = True
        self.eof_button.disabled = True
        self.stop_button.disabled = True

    def set_stopping(self):
        self.status_label.text = "Menghentikan..."
        self.stop_button.disabled = True

    def send_input(self):
        if not self.app.runner.running:
            return
        text = self.input.text
        if self.app.runner.send_input(text):
            self._append_raw("\n> " + sanitize_output(text) + "\n")
            self.input.text = ""

    def send_eof(self):
        if not self.app.runner.running:
            return
        self.app.runner.close_stdin()
        self.add_system("Input ditutup (EOF).")
        self.eof_button.disabled = True

    def clear_output(self):
        self.buffer = ""
        self._pending = []
        self.output.text = ""
        self.output.texture_update()
        self._size_output()

    def copy_output(self):
        try:
            Clipboard.copy(self.buffer)
            self.add_system("Output disalin ke clipboard.")
        except Exception:
            self.add_system("Gagal menyalin output.")

    def apply_theme(self):
        self.root.bg = self.app.palette["bg"]
        self.output.color = self.app.palette["text"]


class SettingsScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        cfg = app.cfg
        self.root = ColorBox(orientation="vertical", bg=app.palette["bg"])
        self.add_widget(self.root)
        header = BoxLayout(size_hint_y=None, height=dp(56), spacing=dp(6),
                           padding=(dp(6), 0))
        back = Button(text="<", size_hint=(None, 1), width=dp(50),
                      font_size=sp(18))
        back.bind(on_release=lambda *a: app.back_from_settings())
        header.add_widget(back)
        header.add_widget(app.label("Pengaturan", font_size=sp(18)))
        self.root.add_widget(header)
        scroll = ScrollView()
        grid = GridLayout(cols=1, size_hint_y=None, spacing=dp(8),
                          padding=dp(12))
        grid.bind(minimum_height=grid.setter("height"))
        scroll.add_widget(grid)
        self.root.add_widget(scroll)

        self.theme_spinner = Spinner(
            text="Gelap" if cfg["theme"] == "dark" else "Terang",
            values=("Gelap", "Terang"), size_hint=(0.55, 1), font_size=sp(13))
        self.theme_spinner.bind(text=self._theme_changed)
        grid.add_widget(self._row("Tema", self.theme_spinner))

        self.font_value = app.label("Ukuran font: %d" % cfg["font_size"],
                                    size_hint_y=None, height=dp(26))
        grid.add_widget(self.font_value)
        self.font_slider = Slider(min=10, max=24, step=1,
                                  value=int(cfg["font_size"]),
                                  size_hint_y=None, height=dp(44))
        self.font_slider.bind(value=self._font_changed)
        grid.add_widget(self.font_slider)

        indent_labels = {2: "2 spasi", 4: "4 spasi", 8: "8 spasi"}
        current = "Tab" if cfg["indent_style"] == "tab" else \
            indent_labels.get(int(cfg["indent_size"]), "4 spasi")
        self.indent_spinner = Spinner(
            text=current, values=("2 spasi", "4 spasi", "8 spasi", "Tab"),
            size_hint=(0.55, 1), font_size=sp(13))
        self.indent_spinner.bind(text=self._indent_changed)
        grid.add_widget(self._row("Indentasi", self.indent_spinner))

        auto_control = BoxLayout(size_hint=(0.55, 1), spacing=dp(6))
        self.auto_switch = Switch(active=bool(cfg["autosave"]),
                                  size_hint=(None, 1), width=dp(56))
        self.auto_switch.bind(active=self._autosave_changed)
        auto_control.add_widget(self.auto_switch)
        self.interval_spinner = Spinner(
            text="%d detik" % cfg["autosave_interval"],
            values=("15 detik", "30 detik", "60 detik"),
            size_hint=(None, 1), width=dp(108), font_size=sp(12))
        self.interval_spinner.bind(text=self._interval_changed)
        auto_control.add_widget(self.interval_spinner)
        grid.add_widget(self._row("Autosave", auto_control))

        info = wrapped_label(
            app,
            "Versi aplikasi: %s\nStatus build: %s\nPython: %s\nKivy: %s\n\n"
            "Eksekusi kode bukan sandbox. Uninstall aplikasi menghapus proyek."
            % (APP_VERSION, BUILD_STATUS, sys.version.split()[0],
               kivy.__version__),
            font_size=sp(12))
        grid.add_widget(info)
        about_button = Button(text="Tentang Aplikasi", size_hint_y=None,
                              height=dp(48), font_size=sp(14))
        about_button.bind(on_release=lambda *a: app.show_about())
        grid.add_widget(about_button)

    def _row(self, text, control):
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(54), spacing=dp(8))
        row.add_widget(self.app.label(text, size_hint_x=0.45))
        control.size_hint = (0.55, 1)
        row.add_widget(control)
        return row

    def _theme_changed(self, spinner, value):
        self.app.set_setting("theme", "dark" if value == "Gelap" else "light")

    def _font_changed(self, slider, value):
        size = int(value)
        self.font_value.text = "Ukuran font: %d" % size
        self.app.set_setting("font_size", size)

    def _indent_changed(self, spinner, value):
        if value == "Tab":
            self.app.set_setting("indent_style", "tab")
        else:
            self.app.set_setting("indent_style", "spaces")
            size = int(value.split()[0])
            self.app.set_setting("indent_size", size)

    def _autosave_changed(self, switch, value):
        self.app.set_setting("autosave", bool(value))

    def _interval_changed(self, spinner, value):
        try:
            interval = int(value.split()[0])
        except (ValueError, IndexError):
            return
        if interval in (15, 30, 60):
            self.app.set_setting("autosave_interval", interval)

    def apply_theme(self):
        self.root.bg = self.app.palette["bg"]


class FaturCodeStudio(App):
    title = APP_NAME

    def build(self):
        self.config_store = ConfigStore(
            os.path.join(self.user_data_dir, "settings.json"))
        self.config_store.load()
        self.cfg = self.config_store.data
        self.palette = THEMES.get(self.cfg["theme"], THEMES["dark"])
        self.projects_dir = os.path.join(self.user_data_dir, "Projects")
        os.makedirs(self.projects_dir, exist_ok=True)
        self.labels = []
        self.inputs = []
        self.popups = []
        self.runner = ProcessRunner(self)
        self._settings_return = "home"

        self.sm = ScreenManager()
        self.home = HomeScreen(self, name="home")
        self.projects = ProjectsScreen(self, name="projects")
        self.editor = EditorScreen(self, name="editor")
        self.terminal = TerminalScreen(self, name="terminal")
        self.settings_screen = SettingsScreen(self, name="settings")
        for screen in (self.home, self.projects, self.editor,
                       self.terminal, self.settings_screen):
            self.sm.add_widget(screen)
        self.sm.current = "home"
        Window.clearcolor = self.palette["bg"]
        Window.bind(on_keyboard=self.on_keyboard)
        Clock.schedule_interval(lambda dt: self._autosave_tick(), 1)
        Clock.schedule_once(lambda dt: self._cleanup_stale_pid(), 0.5)
        return self.sm

    def label(self, text="", muted=False, **kwargs):
        kwargs.setdefault("color", self.palette["muted" if muted else "text"])
        widget = Label(text=text, **kwargs)
        self.labels.append((widget, "muted" if muted else "text"))
        return widget

    def textinput(self, **kwargs):
        kwargs.setdefault("background_color", self.palette["input"])
        kwargs.setdefault("foreground_color", self.palette["text"])
        kwargs.setdefault("cursor_color", self.palette["accent"])
        kwargs.setdefault("selection_color", self.palette["selection"])
        widget = TextInput(**kwargs)
        self.inputs.append(widget)
        return widget

    def save_settings(self):
        try:
            self.config_store.save()
        except OSError:
            pass

    def set_setting(self, key, value):
        self.cfg[key] = value
        self.save_settings()
        if key == "theme":
            self.apply_theme()
        elif key == "font_size":
            self.editor.apply_font_size()
        elif key in ("indent_style", "indent_size"):
            self.editor.apply_indent()

    def apply_theme(self):
        self.palette = THEMES.get(self.cfg["theme"], THEMES["dark"])
        Window.clearcolor = self.palette["bg"]
        for widget, key in list(self.labels):
            try:
                widget.color = self.palette[key]
            except Exception:
                pass
        for widget in self.inputs:
            try:
                widget.background_color = self.palette["input"]
                widget.foreground_color = self.palette["text"]
                widget.cursor_color = self.palette["accent"]
                widget.selection_color = self.palette["selection"]
            except Exception:
                pass
        for screen in (self.home, self.projects, self.editor,
                       self.terminal, self.settings_screen):
            if hasattr(screen, "apply_theme"):
                screen.apply_theme()

    def go_home(self):
        self.sm.current = "home"

    def show_projects(self):
        self.projects.mode = "projects"
        self.projects.project = None
        self.sm.current = "projects"

    def show_project_files(self, name):
        self.projects.mode = "files"
        self.projects.project = name
        self.sm.current = "projects"

    def show_editor(self):
        self.sm.current = "editor"

    def show_terminal(self):
        self.sm.current = "terminal"

    def open_settings(self):
        if self.sm.current != "settings":
            self._settings_return = self.sm.current
        self.sm.current = "settings"

    def back_from_settings(self):
        self.sm.current = self._settings_return or "home"

    def show_about(self):
        content = ColorBox(orientation="vertical", padding=dp(12),
                           spacing=dp(8), bg=self.palette["panel"])
        scroll = ScrollView()
        scroll.add_widget(wrapped_label(self, ABOUT_TEXT, font_size=sp(13)))
        content.add_widget(scroll)
        close = Button(text="Tutup", size_hint_y=None, height=dp(46),
                       font_size=sp(14))
        content.add_widget(close)
        popup = Popup(title="Tentang", content=content,
                      size_hint=(0.92, 0.85), auto_dismiss=True)
        close.bind(on_release=popup.dismiss)
        self.open_popup(popup)

    def open_popup(self, popup):
        self.popups.append(popup)

        def gone(*args):
            if popup in self.popups:
                self.popups.remove(popup)

        popup.bind(on_dismiss=gone)
        popup.open()

    def message(self, title, text):
        content = ColorBox(orientation="vertical", padding=dp(12),
                           spacing=dp(8), bg=self.palette["panel"])
        label = self.label(text, halign="left", valign="top")
        label.bind(width=lambda inst, value: setattr(inst, "text_size",
                                                     (value, None)))
        content.add_widget(label)
        close = Button(text="Tutup", size_hint_y=None, height=dp(46),
                       font_size=sp(14))
        content.add_widget(close)
        popup = Popup(title=title, content=content, size_hint=(0.9, None),
                      height=dp(250), auto_dismiss=True)
        close.bind(on_release=popup.dismiss)
        self.open_popup(popup)

    def ask_name(self, title, initial, on_ok, hint=""):
        content = ColorBox(orientation="vertical", padding=dp(12),
                           spacing=dp(8), bg=self.palette["panel"])
        field = self.textinput(text=initial, multiline=False, hint_text=hint,
                               size_hint_y=None, height=dp(46),
                               font_size=sp(15))
        error = self.label("", muted=True, size_hint_y=None, height=dp(26))
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        cancel = Button(text="Batal", font_size=sp(14))
        ok = Button(text="OK", font_size=sp(14))
        row.add_widget(cancel)
        row.add_widget(ok)
        content.add_widget(field)
        content.add_widget(error)
        content.add_widget(row)
        popup = Popup(title=title, content=content, size_hint=(0.9, None),
                      height=dp(250), auto_dismiss=False)

        def submit(*args):
            try:
                on_ok(field.text)
            except ValueError as exc:
                error.text = str(exc)
                return
            except Exception as exc:
                error.text = str(exc) or "Terjadi kesalahan."
                return
            popup.dismiss()

        cancel.bind(on_release=popup.dismiss)
        ok.bind(on_release=submit)
        field.bind(on_text_validate=submit)
        self.open_popup(popup)
        Clock.schedule_once(lambda dt: setattr(field, "focus", True), 0.15)

    def confirm(self, title, text, on_yes, yes_text="Hapus"):
        content = ColorBox(orientation="vertical", padding=dp(12),
                           spacing=dp(8), bg=self.palette["panel"])
        label = self.label(text, halign="left", valign="top")
        label.bind(width=lambda inst, value: setattr(inst, "text_size",
                                                     (value, None)))
        content.add_widget(label)
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        cancel = Button(text="Batal", font_size=sp(14))
        yes = Button(text=yes_text, font_size=sp(14))
        row.add_widget(cancel)
        row.add_widget(yes)
        content.add_widget(row)
        popup = Popup(title=title, content=content, size_hint=(0.9, None),
                      height=dp(270), auto_dismiss=False)

        def accept(*args):
            popup.dismiss()
            on_yes()

        cancel.bind(on_release=popup.dismiss)
        yes.bind(on_release=accept)
        self.open_popup(popup)

    def ask_save_changes(self, on_save, on_discard, on_cancel=None,
                         title="Perubahan belum disimpan",
                         text="Simpan perubahan sebelum melanjutkan?"):
        content = ColorBox(orientation="vertical", padding=dp(12),
                           spacing=dp(8), bg=self.palette["panel"])
        label = self.label(text, halign="left", valign="top")
        label.bind(width=lambda inst, value: setattr(inst, "text_size",
                                                     (value, None)))
        content.add_widget(label)
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        save_button = Button(text="Simpan", font_size=sp(13))
        discard_button = Button(text="Buang", font_size=sp(13))
        cancel_button = Button(text="Batal", font_size=sp(13))
        row.add_widget(save_button)
        row.add_widget(discard_button)
        row.add_widget(cancel_button)
        content.add_widget(row)
        popup = Popup(title=title, content=content, size_hint=(0.9, None),
                      height=dp(280), auto_dismiss=False)

        def do_save(*args):
            popup.dismiss()
            on_save()

        def do_discard(*args):
            popup.dismiss()
            on_discard()

        def do_cancel(*args):
            popup.dismiss()
            if on_cancel:
                on_cancel()

        save_button.bind(on_release=do_save)
        discard_button.bind(on_release=do_discard)
        cancel_button.bind(on_release=do_cancel)
        self.open_popup(popup)

    def project_path(self, name):
        try:
            safe = validate_name(name, False)
        except ValueError:
            return None
        base = os.path.realpath(self.projects_dir)
        path = os.path.realpath(os.path.join(base, safe))
        if os.path.dirname(path) != base:
            return None
        return path

    def file_path(self, project, filename):
        base = self.project_path(project)
        if not base:
            return None
        try:
            safe = validate_name(filename, True)
        except ValueError:
            return None
        path = os.path.realpath(os.path.join(base, safe))
        if os.path.dirname(path) != base:
            return None
        return path

    def list_projects(self):
        try:
            names = os.listdir(self.projects_dir)
        except OSError:
            return []
        result = []
        for name in names:
            path = self.project_path(name)
            if path and os.path.isdir(path):
                result.append(name)

        def mtime(name):
            try:
                return os.path.getmtime(self.project_path(name))
            except OSError:
                return 0

        result.sort(key=mtime, reverse=True)
        return result

    def list_files(self, project):
        base = self.project_path(project)
        if not base or not os.path.isdir(base):
            return []
        try:
            names = os.listdir(base)
        except OSError:
            return []
        return sorted(
            [n for n in names
             if n.lower().endswith(".py") and os.path.isfile(os.path.join(base, n))],
            key=str.lower)

    def recent_projects(self):
        result = []
        for name in self.cfg.get("recent", []):
            if not isinstance(name, str) or name in result:
                continue
            path = self.project_path(name)
            if path and os.path.isdir(path):
                result.append(name)
        return result[:5]

    def _recent_add(self, name):
        recent = [n for n in self.cfg.get("recent", []) if n != name]
        recent.insert(0, name)
        self.cfg["recent"] = recent[:20]
        self.save_settings()

    def _recent_remove(self, name):
        self.cfg["recent"] = [n for n in self.cfg.get("recent", []) if n != name]
        self.save_settings()

    def open_project(self, name):
        path = self.project_path(name)
        if not path or not os.path.isdir(path):
            self._recent_remove(name)
            self.message("Proyek tidak ditemukan",
                         "Proyek tidak lagi tersedia di penyimpanan.")
            self.home.refresh()
            return
        self._recent_add(name)
        self.show_project_files(name)

    def _switch_editor(self, project, filename):
        if self.editor.load_file(project, filename):
            self._recent_add(project)
            self.sm.current = "editor"

    def open_file(self, project, filename):
        path = self.file_path(project, filename)
        if not path:
            self.message("Gagal membuka", "Nama file tidak valid.")
            return
        if (self.editor.dirty and self.editor.file_path and
                os.path.abspath(self.editor.file_path) != os.path.abspath(path)):
            self.ask_save_changes(
                on_save=lambda: (self.editor.save_file() and
                                 self._switch_editor(project, filename)),
                on_discard=lambda: self._switch_editor(project, filename))
            return
        self._switch_editor(project, filename)

    def new_project(self):
        def create(raw):
            name = validate_name(raw, False)
            path = self.project_path(name)
            if not path:
                raise ValueError("Nama proyek tidak valid.")
            if os.path.exists(path):
                raise ValueError("Proyek dengan nama itu sudah ada.")
            try:
                os.makedirs(path)
            except OSError as exc:
                raise ValueError("Gagal membuat proyek: " + str(exc))
            self._recent_add(name)
            self.show_project_files(name)
            return name

        self.ask_name("Proyek Baru", "", create, hint="Nama proyek")

    def new_file(self, project):
        def create(raw):
            name = validate_name(raw, True)
            path = self.file_path(project, name)
            if not path:
                raise ValueError("Nama file tidak valid.")
            if os.path.exists(path):
                raise ValueError("File dengan nama itu sudah ada.")
            try:
                atomic_write(path, "")
            except OSError as exc:
                raise ValueError("Gagal membuat file: " + str(exc))
            self.projects.refresh()
            self._switch_editor(project, name)
            return name

        self.ask_name("File Baru", "", create, hint="nama_file.py")

    def _is_running_in(self, path):
        if not self.runner.running or not path:
            return False
        try:
            cwd = os.path.realpath(self.runner.cwd or "")
            target = os.path.realpath(path)
        except OSError:
            return False
        return cwd == target or cwd.startswith(target + os.sep)

    def _is_running_file(self, path):
        if not self.runner.running or not path:
            return False
        try:
            return os.path.realpath(self.runner.path or "") == \
                os.path.realpath(path)
        except OSError:
            return False

    def rename_project(self, old):
        old_path = self.project_path(old)
        if self._is_running_in(old_path):
            self.message("Tidak dapat mengganti nama",
                         "Hentikan proses terlebih dahulu.")
            return

        def do(raw):
            new = validate_name(raw, False)
            if new == old:
                return new
            old_path = self.project_path(old)
            new_path = self.project_path(new)
            if not old_path or not new_path:
                raise ValueError("Nama proyek tidak valid.")
            if os.path.exists(new_path):
                raise ValueError("Nama proyek sudah dipakai.")
            try:
                os.rename(old_path, new_path)
            except OSError as exc:
                raise ValueError("Gagal mengganti nama: " + str(exc))
            self.cfg["recent"] = [new if n == old else n
                                  for n in self.cfg.get("recent", [])]
            self.save_settings()
            if self.editor.project == old:
                self.editor.project = new
                if self.editor.filename:
                    self.editor.file_path = self.file_path(
                        new, self.editor.filename)
                self.editor.update_title()
            self.projects.refresh()
            return new

        self.ask_name("Ganti Nama Proyek", old, do)

    def delete_project(self, name):
        path = self.project_path(name)
        if self._is_running_in(path):
            self.message("Tidak dapat menghapus",
                         "Hentikan proses terlebih dahulu.")
            return

        def yes():
            try:
                shutil.rmtree(path)
            except OSError as exc:
                self.message("Gagal menghapus", str(exc))
                return
            self._recent_remove(name)
            if self.editor.project == name:
                self.editor.reset()
            self.projects.refresh()

        self.confirm(
            "Hapus Proyek",
            'Proyek "%s" beserta seluruh isinya akan dihapus permanen. '
            "Tindakan ini tidak dapat dibatalkan." % name,
            yes)

    def rename_file(self, project, old):
        old_path = self.file_path(project, old)
        if self._is_running_file(old_path):
            self.message("Tidak dapat mengganti nama",
                         "Hentikan proses terlebih dahulu.")
            return

        def do(raw):
            new = validate_name(raw, True)
            if new == old:
                return new
            old_path = self.file_path(project, old)
            new_path = self.file_path(project, new)
            if not old_path or not new_path:
                raise ValueError("Nama file tidak valid.")
            if os.path.exists(new_path):
                raise ValueError("Nama file sudah dipakai.")
            try:
                os.rename(old_path, new_path)
            except OSError as exc:
                raise ValueError("Gagal mengganti nama: " + str(exc))
            if self.editor.file_path and \
                    os.path.abspath(self.editor.file_path) == os.path.abspath(old_path):
                self.editor.filename = new
                self.editor.file_path = new_path
                self.editor.update_title()
            self.projects.refresh()
            return new

        self.ask_name("Ganti Nama File", old, do)

    def delete_file(self, project, name):
        path = self.file_path(project, name)
        if self._is_running_file(path):
            self.message("Tidak dapat menghapus",
                         "Hentikan proses terlebih dahulu.")
            return

        def yes():
            try:
                os.remove(path)
            except OSError as exc:
                self.message("Gagal menghapus", str(exc))
                return
            if self.editor.file_path and \
                    os.path.abspath(self.editor.file_path) == os.path.abspath(path):
                self.editor.reset()
            self.projects.refresh()

        self.confirm(
            "Hapus File",
            'File "%s" akan dihapus permanen. Tindakan ini tidak dapat '
            "dibatalkan." % name,
            yes)

    def item_menu(self, kind, project, name):
        content = ColorBox(orientation="vertical", padding=dp(12),
                           spacing=dp(8), bg=self.palette["panel"])
        popup = Popup(title=shorten(name, 28), content=content,
                      size_hint=(0.9, None), height=dp(250),
                      auto_dismiss=True)

        def action(callback):
            def run(*args):
                popup.dismiss()
                callback()
            return run

        if kind == "project":
            actions = (("Ganti Nama", lambda: self.rename_project(name)),
                       ("Hapus", lambda: self.delete_project(name)))
        else:
            actions = (("Ganti Nama", lambda: self.rename_file(project, name)),
                       ("Hapus", lambda: self.delete_file(project, name)))
        for text, callback in actions:
            button = Button(text=text, size_hint_y=None, height=dp(48),
                            font_size=sp(14))
            button.bind(on_release=action(callback))
            content.add_widget(button)
        cancel = Button(text="Batal", size_hint_y=None, height=dp(48),
                        font_size=sp(14))
        cancel.bind(on_release=popup.dismiss)
        content.add_widget(cancel)
        self.open_popup(popup)

    def start_run(self, project, path, filename):
        if self.runner.running:
            self.message("Proses berjalan", "Hentikan proses terlebih dahulu.")
            return
        self.terminal.start_session(filename)
        self.editor.set_running(True)
        if not self.runner.start(path, os.path.dirname(path)):
            error = self.runner.error or "Tidak dapat memulai proses."
            self.terminal.add_system("GAGAL: " + error)
            self.terminal.set_idle("Gagal")
            self.editor.set_running(False)
            self.message("Gagal menjalankan", error)
            return
        self._write_pid()
        self.show_terminal()

    def stop_run(self):
        if not self.runner.running:
            return
        self.terminal.set_stopping()
        self.runner.stop()

    def on_process_stopping(self):
        try:
            Clock.schedule_once(lambda dt: self.terminal.set_stopping(), 0)
        except Exception:
            pass

    def on_process_output(self, stream, text):
        Clock.schedule_once(
            lambda dt: self.terminal.add_output(stream, text), 0)

    def on_process_limit(self):
        Clock.schedule_once(
            lambda dt: self.terminal.add_system(
                "Output melebihi batas 2 MB; sebagian output tidak ditampilkan."),
            0)

    def on_process_finished(self, code, duration, stopped):
        Clock.schedule_once(
            lambda dt: self._finish_ui(code, duration, stopped), 0)

    def _finish_ui(self, code, duration, stopped):
        if stopped:
            status = "Dihentikan pengguna setelah %.2f detik" % duration
        elif code == 0:
            status = "Selesai (exit 0) dalam %.2f detik" % duration
        else:
            status = "Error (exit %s) dalam %.2f detik" % (code, duration)
        self.terminal.add_system(status)
        self.terminal.set_idle(status)
        self.editor.set_running(False)
        self._clear_pid()

    def _pid_path(self):
        return os.path.join(self.user_data_dir, "run.pid")

    def _write_pid(self):
        try:
            atomic_write(self._pid_path(), json.dumps(
                {"pid": self.runner.pid, "time": time.time()}))
        except OSError:
            pass

    def _clear_pid(self):
        try:
            os.unlink(self._pid_path())
        except OSError:
            pass

    def _cleanup_stale_pid(self):
        path = self._pid_path()
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            pid = data.get("pid") if isinstance(data, dict) else None
        except Exception:
            pid = None
        if isinstance(pid, int) and 1 < pid != os.getpid():
            try:
                with open("/proc/%d/cmdline" % pid, "rb") as handle:
                    cmdline = handle.read().decode("utf-8", "replace")
                if "main.py" in cmdline or "fatur" in cmdline.lower():
                    os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
        self._clear_pid()

    def _autosave_tick(self):
        if not self.cfg.get("autosave"):
            return
        editor = self.editor
        if editor.dirty and editor.file_path and not editor.read_only:
            if time.time() - editor.last_edit >= int(
                    self.cfg.get("autosave_interval", 30)):
                editor.save_file(silent=True)

    def on_keyboard(self, window, key, scancode, codepoint, modifiers):
        if key == 27:
            if self.popups:
                self.popups[-1].dismiss()
                return True
            current = self.sm.current
            if current == "home":
                return False
            if current == "projects":
                self.projects.back()
                return True
            if current == "editor":
                self.editor.back()
                return True
            if current == "terminal":
                self.sm.current = "editor"
                return True
            if current == "settings":
                self.back_from_settings()
                return True
        if "ctrl" in modifiers and codepoint:
            char = codepoint.lower()
            if char == "s":
                self.editor.save_file()
                return True
            if char == "z":
                self.editor.input.do_undo()
                return True
            if char == "y":
                self.editor.input.do_redo()
                return True
            if char == "f" and self.sm.current == "editor":
                self.editor.show_find()
                return True
        return False

    def on_stop(self):
        try:
            if self.runner.running:
                self.runner.stop()
        except Exception:
            pass
        self.save_settings()


if __name__ == "__main__":
    FaturCodeStudio().run()


