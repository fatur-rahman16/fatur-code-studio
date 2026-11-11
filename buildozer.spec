[app]
title = Fatur Code Studio
package.name = faturcodestudio
package.domain = com.fatur

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json,ttf
source.exclude_dirs = tests,bin,.git,__pycache__
source.exclude_patterns = *.pyc,*.md

version = 1.0.0

# Kivy 2.3.x sesuai PRD. Pygments opsional, kode memakai fallback aman.
requirements = python3,kivy==2.3.1,pygments

orientation = all
fullscreen = 0

# Tidak ada izin berbahaya. Aplikasi offline, tanpa INTERNET.
android.permissions =
android.api = 34
android.minapi = 24
android.ndk_api = 24
android.archs = arm64-v8a, armeabi-v7a
android.allow_backup = False
android.accept_sdk_license = True
android.release_artifact = apk
android.debug_artifact = apk
android.private_storage = True
android.presplash_color = #0E1116
p4a.bootstrap = sdl2

[buildozer]
log_level = 2
warn_on_root = 1
