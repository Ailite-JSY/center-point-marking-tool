import os
import sys
import csv
import cv2
import numpy as np
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QLabel, QListView, QVBoxLayout, QWidget,
    QFileDialog, QPushButton, QHBoxLayout, QMessageBox, QScrollArea,
    QToolBar, QStatusBar, QSlider, QGroupBox
)
from PyQt5.QtCore import Qt, QSize, QAbstractListModel, QPoint, QTimer, QRectF
from PyQt5.QtGui import QPixmap, QImage, QPainter, QColor, QPen, QIcon, QFont, QWheelEvent


class ZoomableLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_tool = parent
        self.setAlignment(Qt.AlignCenter)
        self.setMouseTracking(True)
        self.setStyleSheet("border: 1px solid #aaa;")
        self._zoom = 100
        self._empty_pixmap = QPixmap(1, 1)
        self._empty_pixmap.fill(Qt.transparent)
        self._image_rect = QRectF()

    def wheelEvent(self, event: QWheelEvent):
        zoom_in = event.angleDelta().y() > 0
        self.parent_tool.adjust_zoom(zoom_in)
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.pixmap() and not self.pixmap().isNull():
            self.handle_click(event.pos())
        elif event.button() == Qt.RightButton:
            self.parent_tool.finish_polygon()

    def handle_click(self, pos):
        if not self.pixmap() or self.pixmap().isNull():
            return

        if not self._image_rect.contains(pos):
            return

        x_in_img = (pos.x() - self._image_rect.x()) / self._image_rect.width()
        y_in_img = (pos.y() - self._image_rect.y()) / self._image_rect.height()

        original_x = int(x_in_img * self.parent_tool.original_width)
        original_y = int(y_in_img * self.parent_tool.original_height)

        self.parent_tool.add_polygon_point(original_x, original_y)

    def set_zoom(self, value):
        self._zoom = max(10, min(500, value))
        self.parent_tool.update_display()


class ImageListModel(QAbstractListModel):
    def __init__(self, image_files=None):
        super().__init__()
        self.image_files = image_files or []

    def data(self, index, role):
        if role == Qt.DisplayRole:
            return os.path.basename(self.image_files[index.row()])
        elif role == Qt.DecorationRole:
            return QIcon.fromTheme("image-x-generic")

    def rowCount(self, index):
        return len(self.image_files)


class AnnotationTool(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("图像制导目标打击点标注工具v2.0")
        self.setMinimumSize(1200, 800)
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f5f5f5;
            }
            QGroupBox {
                border: 1px solid #ddd;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 15px;
            }
            QPushButton {
                min-width: 80px;
                padding: 5px;
            }
        """)

        # 初始化变量
        self.image_dir = ""
        self.image_paths = []
        self.current_index = 0
        self.original_image = None
        self.original_width = 0
        self.original_height = 0
        self.scale_factor = 1.0
        self.annotations = {}
        self.current_polygon = []
        self.current_centroid = None
        self.auto_advance = True
        self.marked_count = 0  # 已标注图片计数

        # 创建UI
        self.setup_ui()
        self.setup_toolbar()
        self.setup_statusbar()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QHBoxLayout(central_widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(15)

        # 左侧控制面板
        panel = QWidget()
        panel.setFixedWidth(300)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(5, 5, 5, 5)

        # 操作说明组
        help_group = QGroupBox("操作说明")
        help_layout = QVBoxLayout()
        help_label = QLabel(
            "1. 左键点击添加顶点\n"
            "2. 右键点击完成标注\n"
            "3. 至少需要3个顶点\n"
            "4. 使用滚轮缩放图片"
        )
        help_label.setStyleSheet("font-size: 12px;")
        help_layout.addWidget(help_label)
        help_group.setLayout(help_layout)
        panel_layout.addWidget(help_group)

        # 缩放控制组
        zoom_group = QGroupBox("缩放控制")
        zoom_layout = QVBoxLayout()
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(10, 500)
        self.zoom_slider.setValue(100)
        self.zoom_slider.valueChanged.connect(self.on_zoom_changed)  # 连接信号
        zoom_layout.addWidget(self.zoom_slider)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setAlignment(Qt.AlignCenter)
        zoom_layout.addWidget(self.zoom_label)
        zoom_group.setLayout(zoom_layout)
        panel_layout.addWidget(zoom_group)

        # 图片列表组（带滚动条）
        list_group = QGroupBox("图片列表")
        list_layout = QVBoxLayout()

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)

        self.list_view = QListView()
        self.list_view.setStyleSheet("""
            QListView {
                font: 12px;
                show-decoration-selected: 1;
            }
            QListView::item:hover {
                background: #e0e0e0;
            }
            QListView::item:selected {
                background: #0078d7;
                color: white;
            }
        """)
        self.list_model = ImageListModel()
        self.list_view.setModel(self.list_model)
        self.list_view.clicked.connect(self.load_selected_image)

        scroll_layout.addWidget(self.list_view)
        scroll_area.setWidget(scroll_content)
        list_layout.addWidget(scroll_area)
        list_group.setLayout(list_layout)
        panel_layout.addWidget(list_group)

        # 操作按钮组
        btn_group = QGroupBox("操作")
        btn_layout = QVBoxLayout()
        self.btn_open = QPushButton("打开文件夹")
        self.btn_open.setIcon(QIcon.fromTheme("folder-open"))
        self.btn_open.clicked.connect(self.open_image_folder)

        self.btn_save = QPushButton("保存标注")
        self.btn_save.setIcon(QIcon.fromTheme("document-save"))
        self.btn_save.clicked.connect(self.save_centroids)

        self.btn_clear = QPushButton("清除当前")
        self.btn_clear.setIcon(QIcon.fromTheme("edit-clear"))
        self.btn_clear.clicked.connect(self.clear_current)

        btn_layout.addWidget(self.btn_open)
        btn_layout.addWidget(self.btn_save)
        btn_layout.addWidget(self.btn_clear)
        btn_group.setLayout(btn_layout)
        panel_layout.addWidget(btn_group)

        panel_layout.addStretch()
        layout.addWidget(panel)

        # 右侧图片显示区域
        self.image_label = ZoomableLabel(self)
        layout.addWidget(self.image_label, stretch=1)

    def on_zoom_changed(self, value):
        """处理缩放滑块值变化"""
        self.image_label.set_zoom(value)
        self.zoom_label.setText(f"{value}%")

    def setup_toolbar(self):
        toolbar = self.addToolBar("工具")
        toolbar.setIconSize(QSize(24, 24))
        toolbar.addAction(QIcon.fromTheme("go-previous"), "上一张", self.prev_image)
        toolbar.addAction(QIcon.fromTheme("go-next"), "下一张", self.next_image)
        toolbar.addSeparator()
        toolbar.addAction(QIcon.fromTheme("zoom-in"), "放大", lambda: self.adjust_zoom(True))
        toolbar.addAction(QIcon.fromTheme("zoom-out"), "缩小", lambda: self.adjust_zoom(False))
        toolbar.addAction(QIcon.fromTheme("zoom-fit-best"), "适应窗口", self.zoom_to_fit)

    def setup_statusbar(self):
        self.statusBar().showMessage("准备就绪")

        # 已标注/全部图片计数
        self.marked_label = QLabel("已标注: 0/0")
        self.statusBar().addPermanentWidget(self.marked_label)

        # 当前坐标显示
        self.coord_label = QLabel()
        self.statusBar().addPermanentWidget(self.coord_label)

    def update_marked_count(self):
        """更新已标注图片计数"""
        total = len(self.image_paths)
        marked = len([f for f in self.annotations.keys() if len(self.annotations[f]["centroids"]) > 0])
        self.marked_label.setText(f"已标注: {marked}/{total}")

    def adjust_zoom(self, zoom_in):
        """调整缩放级别"""
        step = 20 if zoom_in else -20
        new_zoom = min(500, max(10, self.image_label._zoom + step))
        self.zoom_slider.setValue(new_zoom)
        self.image_label.set_zoom(new_zoom)
        self.zoom_label.setText(f"{new_zoom}%")

    def zoom_to_fit(self):
        """缩放以适应窗口"""
        self.zoom_slider.setValue(100)
        self.image_label.set_zoom(100)
        self.zoom_label.setText("100%")

    def load_selected_image(self, index):
        """加载选中的图片"""
        self.current_index = index.row()
        self.load_current_image()

    def open_image_folder(self):
        """打开图片文件夹"""
        dir_path = QFileDialog.getExistingDirectory(self, "选择图片文件夹")
        if dir_path:
            self.image_dir = dir_path
            self.image_paths = [
                os.path.join(dir_path, f)
                for f in os.listdir(dir_path)
                if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff'))
            ]

            if not self.image_paths:
                QMessageBox.warning(self, "警告", "未找到支持的图片文件！")
                return

            self.list_model = ImageListModel(self.image_paths)
            self.list_view.setModel(self.list_model)
            self.current_index = 0
            self.annotations = {}
            self.marked_count = 0
            self.load_current_image()
            self.update_marked_count()

    def load_current_image(self):
        """加载当前图片"""
        if 0 <= self.current_index < len(self.image_paths):
            image_path = self.image_paths[self.current_index]
            img = cv2.imread(image_path)
            if img is None:
                QMessageBox.warning(self, "错误", f"无法加载图片: {image_path}")
                return

            self.original_image = img
            self.original_height, self.original_width = img.shape[:2]

            # Clear current polygon and centroid when loading new image
            self.current_polygon = []
            self.current_centroid = None

            self.update_display()
            self.statusBar().showMessage(f"正在标注: {os.path.basename(image_path)}")
            self.update_marked_count()

    def update_display(self):
        """更新图片显示"""
        if self.original_image is None:
            return

        rgb_image = cv2.cvtColor(self.original_image, cv2.COLOR_BGR2RGB)
        height, width, _ = rgb_image.shape
        q_img = QImage(
            rgb_image.data, width, height, 3 * width,
            QImage.Format_RGB888
        )

        # Calculate the scale factor to fit the image in the label
        label_size = self.image_label.size()
        self.scale_factor = min(
            label_size.width() / width,
            label_size.height() / height
        )

        # Apply current zoom level
        zoom_factor = self.image_label._zoom / 100
        scaled_width = int(width * self.scale_factor * zoom_factor)
        scaled_height = int(height * self.scale_factor * zoom_factor)

        scaled_pixmap = QPixmap.fromImage(q_img).scaled(
            scaled_width,
            scaled_height,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )

        # Calculate the actual displayed image rectangle
        offset_x = (self.image_label.width() - scaled_width) / 2
        offset_y = (self.image_label.height() - scaled_height) / 2
        self.image_label._image_rect = QRectF(offset_x, offset_y, scaled_width, scaled_height)

        painter = QPainter(scaled_pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        # Draw current polygon
        if self.current_polygon:
            points = [QPoint(
                int(x * self.scale_factor * zoom_factor),
                int(y * self.scale_factor * zoom_factor)
            ) for x, y in self.current_polygon]

            painter.setPen(QPen(QColor(0, 255, 0), 2))  # Green polygon
            for i in range(len(points)):
                painter.drawLine(points[i], points[(i + 1) % len(points)])

            painter.setPen(QPen(QColor(255, 255, 0), 5))  # Yellow vertices
            for point in points:
                painter.drawPoint(point)

        # Draw centroid
        if self.current_centroid:
            cx, cy = self.current_centroid
            display_cx = int(cx * self.scale_factor * zoom_factor)
            display_cy = int(cy * self.scale_factor * zoom_factor)

            painter.setPen(QPen(QColor(255, 0, 0), 8))  # Red centroid
            painter.drawPoint(display_cx, display_cy)

            font = QFont()
            font.setPointSize(10)
            painter.setFont(font)
            painter.setPen(QPen(Qt.white, 2))
            painter.drawText(
                display_cx + 10,
                display_cy + 5,
                f"({cx}, {cy})"
            )

        painter.end()
        self.image_label.setPixmap(scaled_pixmap)

    def add_polygon_point(self, x, y):
        """添加多边形顶点"""
        self.current_polygon.append((x, y))
        self.update_display()

    def calculate_centroid(self, points):
        """计算多边形形心"""
        polygon = np.array(points, dtype=np.float32)
        moments = cv2.moments(polygon)
        if moments["m00"] == 0:
            return (0, 0)
        cx = int(moments["m10"] / moments["m00"])
        cy = int(moments["m01"] / moments["m00"])
        return (cx, cy)

    def finish_polygon(self):
        """完成当前多边形标注"""
        if len(self.current_polygon) < 3:
            QMessageBox.warning(self, "提示", "至少需要3个点才能构成多边形！")
            return

        filename = os.path.basename(self.image_paths[self.current_index])
        self.current_centroid = self.calculate_centroid(self.current_polygon)

        if filename not in self.annotations:
            self.annotations[filename] = {"centroids": []}

        self.annotations[filename]["centroids"].append(self.current_centroid)
        self.marked_count += 1
        self.update_marked_count()

        self.statusBar().showMessage(f"已保存形心: ({self.current_centroid[0]}, {self.current_centroid[1]})")
        self.current_polygon = []
        self.update_display()

        if self.auto_advance:
            QTimer.singleShot(1000, self.next_image)

    def clear_current(self):
        """清除当前标注"""
        self.current_polygon = []
        self.current_centroid = None
        self.update_display()

    def prev_image(self):
        """切换到上一张图片"""
        if self.image_paths:
            self.current_index = max(0, self.current_index - 1)
            self.load_current_image()

    def next_image(self):
        """切换到下一张图片"""
        if self.image_paths:
            self.current_index = min(len(self.image_paths) - 1, self.current_index + 1)
            self.load_current_image()
    def save_centroids(self):
        """保存形心坐标到文件"""
        if not self.annotations:
            QMessageBox.warning(self, "警告", "没有可保存的标注！")
            return

        # 让用户选择保存位置
        options = QFileDialog.Options()
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存形心坐标",
            "",  # 初始目录为空
            "CSV文件 (*.csv);;所有文件 (*)",
            options=options
        )

        if not save_path:  # 用户取消了选择
            return

        try:
            with open(save_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["filename", "centroid_x", "centroid_y"])
                for filename, data in self.annotations.items():
                    for cx, cy in data["centroids"]:
                        writer.writerow([filename, cx, cy])
            QMessageBox.information(self, "成功", f"形心坐标已保存到：\n{save_path}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存失败：{str(e)}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    # 设置默认字体
    font = QFont()
    font.setFamily("Microsoft YaHei")
    font.setPointSize(10)
    app.setFont(font)

    window = AnnotationTool()
    window.show()
    sys.exit(app.exec_())