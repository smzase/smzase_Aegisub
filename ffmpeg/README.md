### 关于自压工具---auto_encode_fluent.py
你要去安装
```
pip install PyQt6 PyQt6-Fluent-Widgets PyQt6-Frameless-Window
```
```
pip install "PyQt-Fluent-Widgets[full]" -i https://pypi.org/simple/
```
然后你要下 ffmpeg 并添加到环境变量中

若出现以下问题
> QWidget: Must construct a QApplication before a QWidget

请检查是否有 PyQt5，并卸载
```
pip uninstall PyQt5 PyQt5-Qt5 PyQt5-sip PyQt5-Frameless-Window PyQt-Fluent-Widgets -y
```

打包命令
```
python -m nuitka --standalone --onefile --windows-disable-console --enable-plugin=pyqt6 --enable-plugin=numpy --windows-icon-from-ico=konoha.ico --include-data-file=konoha.ico=konoha.ico auto_encode_fluent.py
```
