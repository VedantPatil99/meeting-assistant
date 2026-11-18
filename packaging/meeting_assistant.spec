# PyInstaller recipe for the Windows app. Build with packaging\build.ps1.
# One folder (not one file): it starts faster, and antivirus tools flag one-file builds more often.

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

datas = (
    collect_data_files("faster_whisper")      # the voice-activity model it ships with
    + copy_metadata("keyring")                # keyring finds its Windows backend through package metadata
)
binaries = collect_dynamic_libs("ctranslate2") + collect_dynamic_libs("onnxruntime")
hiddenimports = collect_submodules("keyring.backends") + ["win32ctypes.core", "pyaudiowpatch"]

# Qt parts the app never uses; leaving them out saves a lot of space
unused_qt = ["QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "QtWebChannel", "QtQuick",
             "QtQuick3D", "QtQml", "Qt3DCore", "Qt3DRender", "QtMultimedia", "QtCharts", "QtGraphs",
             "QtDataVisualization", "QtPdf", "QtPdfWidgets", "QtDesigner", "QtBluetooth", "QtNfc",
             "QtPositioning", "QtLocation", "QtSensors", "QtSerialPort", "QtSql", "QtTest", "QtHelp",
             "QtOpenGL", "QtOpenGLWidgets", "QtRemoteObjects", "QtScxml", "QtSpatialAudio",
             "QtTextToSpeech", "QtWebSockets", "QtHttpServer"]

a = Analysis(
    ["launch.py"],
    pathex=["../src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["av", "tkinter", "pytest", "matplotlib", "IPython", "notebook"] + [f"PySide6.{m}" for m in unused_qt],
    runtime_hooks=["rthook_no_av.py"],  # a placeholder for PyAV, see that file
    noarchive=False,
)

# Files the Qt hooks bring along that a widgets-only, English-only app doesn't load:
# the software OpenGL fallback, Qt Quick/QML (pulled in by plugins we don't use), the PDF image
# plugin, and Qt's own translations.
UNUSED_FILES = ("opengl32sw.dll", "Qt6Quick", "Qt6Qml", "Qt6Pdf", "qpdf.dll", "av.libs/")


def wanted(entry) -> bool:
    dest = entry[0].replace("\\", "/")
    return not any(part in dest for part in UNUSED_FILES) and "PySide6/translations/" not in dest


a.binaries = [b for b in a.binaries if wanted(b)]
a.datas = [d for d in a.datas if wanted(d)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MeetingAssistant",
    console=False,
    icon="build/icon.ico",
)
coll = COLLECT(exe, a.binaries, a.datas, name="MeetingAssistant")
