# Blinko STUDIO2 - Cyberbrick 控制程序

同济大学设计创意学院 · 7组 · 作品：Blinko

基于 MicroPython 的 Cyberbrick 表情机器人控制程序，包含两个模块：

---

## 项目结构

```
├── cyberbrick-debug/     # 桌面调试控制器
│   ├── server.py         # Flask 本地服务（代码编辑面板）
│   ├── start.command     # macOS 启动脚本
│   ├── current_code.py   # 当前编辑的代码
│   ├── default_code.py   # 默认示例（LED2 红灯常亮）
│   └── src/
│       └── cyberbrick_led.py  # Cyberbrick LED API 封装
│
└── cyberbrick-display/   # 表情展示页面
    ├── server.py         # Flask 本地服务（上传 + 展示）
    ├── start.command     # macOS 启动脚本
    ├── generate_gifs.py  # GIF 生成工具
    ├── slots.json        # 模式配置
    ├── assets/           # 表情素材（6 组 GIF/PNG）
    ├── files/            # 可上传的示例代码
    └── py/               # 随机模式程序（fin/swing/noding）
```

## 环境要求

- Python 3.11+
- Flask
- Cyberbrick 硬件 + `mpremote`

## 使用方式

### 调试控制器

双击 `cyberbrick-debug/start.command`，浏览器打开代码编辑面板，在线编辑并运行 Cyberbrick Python 代码。

### 表情展示

双击 `cyberbrick-display/start.command`，浏览器打开展示页面，上传模式程序并展示 6 种表情动画。
