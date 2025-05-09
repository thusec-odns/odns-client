import PyInstaller.__main__
import os
import platform
import shutil

APP_NAME = "CoreDNSController"
MAIN_SCRIPT = "main_app.py"
ICON_BASE_NAME = "app_icon" # app_icon.ico for Windows, app_icon.icns for macOS

# --- Platform Specific Configuration ---
current_os = platform.system().lower()
console_type = True  # True for console (macOS/Linux debug), False for no console (Windows GUI)
icon_file = None
coredns_binary_name = "coredns"

if current_os == "windows":
    console_type = False
    icon_file = f"{ICON_BASE_NAME}.ico"
    coredns_binary_name = "coredns.exe"
elif current_os == "darwin": # macOS
    icon_file = f"{ICON_BASE_NAME}.icns"
    # console_type = False # For a real .app bundle, you'd want no console

# --- PyInstaller Options ---
pyinstaller_options = [
    MAIN_SCRIPT,
    f"--name={APP_NAME}",
    "--onefile",  # 创建单个可执行文件
    # "--onedir", # 或者创建单目录应用 (通常初始体积较小，但分发文件多)
    "--noconfirm", # 构建时不要求确认覆盖
    "--clean",     # 构建前清理 PyInstaller 缓存
    # "--noupx",   # 如果 UPX 导致问题，取消此行注释
    "--uac-admin"
]

if console_type:
    pyinstaller_options.append("--console")
else:
    pyinstaller_options.append("--windowed") # 等同于 --noconsole

if icon_file and os.path.exists(icon_file):
    pyinstaller_options.append(f"--icon={icon_file}")
else:
    print(f"警告: 图标文件 '{icon_file}' 未找到。将不使用自定义图标。")

# --- Data files (coredns executable and corefile) ---
# PyInstaller 会将这些文件复制到与可执行文件相同的目录 (在解包时)
# 应用程序代码需要能够找到这些文件 (通常在 sys._MEIPASS 或可执行文件同级目录)
datas_to_add = []
coredns_path_src = os.path.join('.', coredns_binary_name) # 假设 coredns 在项目根目录
corefile_path_src = os.path.join('.', 'corefile')     # 假设 corefile 在项目根目录

if os.path.exists(coredns_path_src):
    datas_to_add.append(f"--add-data={coredns_path_src}{os.pathsep}.")
else:
    print(f"错误: '{coredns_path_src}' 未找到! 打包将缺少此文件。")
    # sys.exit(1) # Optionally exit if critical file is missing

if os.path.exists(corefile_path_src):
    datas_to_add.append(f"--add-data={corefile_path_src}{os.pathsep}.")
else:
    print(f"错误: '{corefile_path_src}' 未找到! 打包将缺少此文件。")
    # sys.exit(1)

pyinstaller_options.extend(datas_to_add)


# --- Exclude unnecessary modules to reduce size ---
# 注意：过度排除可能导致程序运行错误，需要仔细测试
excludes = [
    'tkinter', 'unittest', 'http', 'xml', 'email', 'pydoc_data', 'lib2to3',
    'curses', 'distutils', 'setuptools', 'ensurepip', 'bz2', 'lzma', 
    # PyQt6 modules (be careful, exclude only what's certainly not needed)
    # The application uses QtWidgets, QtCore, QtGui.
    'PyQt6.QtDesigner', 'PyQt6.QtNetwork', 'PyQt6.QtOpenGL', 'PyQt6.QtPrintSupport',
    'PyQt6.QtSql', 'PyQt6.QtSvg', 'PyQt6.QtTest', 'PyQt6.QtXml', 'PyQt6.QtQml',
    'PyQt6.QtBluetooth', 'PyQt6.QtMultimedia', 'PyQt6.QtNfc', 'PyQt6.QtPositioning',
    'PyQt6.QtQuick', 'PyQt6.QtRemoteObjects', 'PyQt6.QtSensors', 'PyQt6.QtSerialPort',
    'PyQt6.QtWebChannel', 'PyQt6.QtWebEngineCore', 'PyQt6.QtWebEngineWidgets',
    'PyQt6.QtWebSockets', 'PyQt6.Qt3DCore', 'PyQt6.Qt3DRender', 'PyQt6.Qt3DInput',
    'PyQt6.Qt3DLogic', 'PyQt6.Qt3DAnimation', 'PyQt6.Qt3DExtras',
    # Common large libraries that might be pulled in indirectly
    'numpy', 'pandas', 'scipy', 'matplotlib', 'IPython', 'jupyter_client', 'jupyter_core',
]
for ex_module in excludes:
    pyinstaller_options.append(f"--exclude-module={ex_module}")

# --- UPX Configuration ---
# 确保 UPX 在你的 PATH 中，或者使用 --upx-dir 指定路径
# pyinstaller_options.append("--upx-dir=/path/to/upx_directory/")


# --- Run PyInstaller ---
print(f"正在运行 PyInstaller 命令: pyinstaller {' '.join(pyinstaller_options)}")
try:
    PyInstaller.__main__.run(pyinstaller_options)
    print("\n构建成功!")
    print(f"可执行文件位于: dist/{APP_NAME}{'.exe' if current_os == 'windows' else ''}")

    # --- Post-build cleanup (optional) ---
    # 删除 .spec 文件和 build 目录
    if os.path.exists(f"{APP_NAME}.spec"):
        os.remove(f"{APP_NAME}.spec")
    if os.path.exists("build"):
        shutil.rmtree("build")
    print("临时构建文件已清理。")

except Exception as e:
    print(f"\n构建过程中发生错误: {e}")

