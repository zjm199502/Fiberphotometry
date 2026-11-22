# 打包安装包 / Building the installers

PyInstaller 只能在目标系统上打包：Windows 版要在 Windows 上打，macOS 版要在 Mac 上打。
PyInstaller only builds for the system it runs on, so build the Windows app on Windows and the macOS app on a Mac.

## 方式一：GitHub Actions（推荐）/ GitHub Actions (recommended)

把整个项目推送到 GitHub 仓库后，`.github/workflows/build.yml` 会自动在 Windows 和 macOS 上运行测试并打包。
在仓库的 **Actions** 页面打开最近一次运行，底部 **Artifacts** 里下载 Windows 安装程序（`FiberLume-<版本>-Windows-Setup.exe`，由 Inno Setup 生成）
和 macOS 安装包（`FiberLume-<版本>-macOS-AppleSilicon.dmg`，仅 M 系列芯片）。
也可以在 Actions 页面点击 **Run workflow** 手动重新打包。

Push the project to a GitHub repository; the workflow tests and builds a Windows Setup.exe (Inno Setup) and macOS
.dmg for Apple silicon. Download them from the run's **Artifacts** on the **Actions** page.

## 方式二：在自己电脑上打包 / Building on your own computer

1. 安装 Python 3.11 或更新版本（https://www.python.org/downloads/ ，Windows 安装时勾选 “Add python.exe to PATH”）。
2. 解压项目，在项目文件夹中打开终端（Windows：在文件夹地址栏输入 `cmd` 回车；Mac：右键文件夹 → 新建位于文件夹位置的终端窗口）。
3. 运行：

```bash
python -m pip install -e ".[gui]" pyinstaller
python src/fiberphoto/render_sources.py
pyinstaller packaging/fiberphoto.spec --noconfirm
```

结果 / Output:

- Windows: `dist/fiberphoto/` 文件夹，双击 `fiberphoto.exe`。使用 Inno Setup 运行 `iscc /DAppVersion=0.6.0 packaging/windows_installer.iss` 生成 Setup.exe。
- macOS: `dist/fiberphoto.app`。在 Apple silicon Mac 上运行 `bash packaging/make_dmg.sh 0.6.0 AppleSilicon` 生成 DMG。


## 发布基础安装版及软件内补丁

- 基础安装版使用 `vX.Y.0` 标签，版本号与源码、发布说明保持一致。两个平台的安装版通过验收后，工作流上传安装包，并更新 `updates` 分支的 `channel.json`。
- 兼容的小版本使用 `X.Y.1` 至 `X.Y.20`。更新 main 的源码和版本号后，在 Actions 中从 main 运行 **Publish in-app patch**。工作流在 Windows 和 macOS 的已发布基础安装版上加载补丁并运行分析，成功后才公开清单和带 SHA-256 的补丁包。启动器、运行依赖或打包配置变更需要发布新的基础安装版。
- 用户打开“帮助 → 检查更新”读取通道，确认后下载。补丁经校验后存入当前用户目录，重启生效。

## 更新用户手册 / Updating the manual

修改 `docs/manual/*.md` 后运行 `pip install markdown`，再运行
`python docs/manual/build_pdf.py <Chrome 或 Chromium 的路径>`，然后重新打包。
