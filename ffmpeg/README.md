### 关于自压工具
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
