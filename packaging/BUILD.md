# 打包安装包 / Building the installers

PyInstaller 只能在目标系统上打包：Windows 版要在 Windows 上打，macOS 版要在 Mac 上打。
PyInstaller only builds for the system it runs on, so build the Windows app on Windows and the macOS app on a Mac.

## 方式一：GitHub Actions（推荐）/ GitHub Actions (recommended)

把整个项目推送到 GitHub 仓库后，`.github/workflows/build.yml` 会自动在 Windows 和 macOS 上运行测试并打包。
在仓库的 **Actions** 页面打开最近一次运行，底部 **Artifacts** 里下载 `fiberphoto-windows` 和 `fiberphoto-macos`。
也可以在 Actions 页面点击 **Run workflow** 手动重新打包。

Push the project to a GitHub repository; the workflow tests and builds on Windows and macOS. Download
`fiberphoto-windows` and `fiberphoto-macos` from the run's **Artifacts** on the **Actions** page.

## 方式二：在自己电脑上打包 / Building on your own computer

1. 安装 Python 3.11 或更新版本（https://www.python.org/downloads/ ，Windows 安装时勾选 “Add python.exe to PATH”）。
2. 解压项目，在项目文件夹中打开终端（Windows：在文件夹地址栏输入 `cmd` 回车；Mac：右键文件夹 → 新建位于文件夹位置的终端窗口）。
3. 运行：

```bash
python -m pip install -e ".[gui]" pyinstaller
pyinstaller packaging/fiberphoto.spec --noconfirm
```

结果 / Output:

- Windows: `dist/fiberphoto/` 文件夹，双击 `fiberphoto.exe`。把整个文件夹压缩成 zip 发给同学。
- macOS: `dist/fiberphoto.app`。右键压缩成 zip 发给同学（同学第一次需右键 → 打开）。

在 Mac 上要打包给 Intel 和 Apple 芯片两种电脑时，请分别在两种 Mac 上打包，或使用 GitHub Actions。

## 更新用户手册 / Updating the manual

修改 `docs/manual/*.md` 后运行 `pip install markdown`，再运行
`python docs/manual/build_pdf.py <Chrome 或 Chromium 的路径>`，然后重新打包。
