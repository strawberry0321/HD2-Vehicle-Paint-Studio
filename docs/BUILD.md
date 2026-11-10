# 构建

这是 Windows 桌面工具。Python 负责涂装和导出，Three.js 负责预览，C# / WebView2 负责桌面窗口，Go 负责启动器与补丁校验。

准备 Python 3.12 或更新版本、Go 1.25 或更新版本，以及 .NET Framework 4.8 的 C# 编译器。
从本仓库 Release 下载完整程序 ZIP，作为运行库和载具资源的基础包；这些文件不重复放进 Git。

在仓库根目录运行：

```powershell
python scripts/build.py --base-package "载具替换工具_V19_Windows_x64.zip"
```

生成目录为 `dist/VehiclePaintStudio-19.0.0`。构建会读取基础包、覆盖当前源码并重建桌面窗口和启动程序，输出到独立目录。
基础包中的 `settings.json` 会改为相对数据目录，不会带入已有素材和工作台。

如果修改了 `native/reader/`，在命令后加 `--rebuild-reader`；首次构建需要 Go 下载其余依赖。
校验器使用的 Filediver 代码已放在 `third_party/filediver/`，固定为上游提交 `9f52b0e6c2dac074c121f5c295c53b27cb20f262`。
这里只收录实际使用的读取包，保留上游许可证和文件哈希；对应上游仓库为 https://github.com/xypwn/filediver 。

完成后打包：

```powershell
python scripts/package.py "dist/VehiclePaintStudio-19.0.0"
```

输出 ZIP 和 SHA-256 文件。不打包用户数据、运行日志、缓存或开发用目录。

二次开发时保留右下角原作者水印和许可文件，发布时说明自己的改动。
