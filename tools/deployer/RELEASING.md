# 发版流程

可执行文件走 **GitHub Releases**,不进仓库。

一个 78 MB 的二进制几乎不可压缩(gzip 后还是 78 MB),git 也无法对它做增量存储——提交一次两个平台就是 157 MB,之后**每重编一次再涨 78 MB**,而且写进历史后要清理就得改写历史。仓库现在 3.5 MB,保持这样。

Releases 没有这个问题:单文件上限 2 GB,不进 git 历史,客户拿到的是一个下载链接。

## 每次发版

### 1. 在两台机器上各编译一次

PyInstaller **不能交叉编译**。Windows 的 `.exe` 必须在 Windows 上生成,Linux 的必须在 Linux 上生成。

```bash
git pull
python3 tools/deployer/build.py
```

产出在仓库根目录:`X2AutoGreeter-Linux` 或 `X2AutoGreeter-Windows.exe`。

编译前确认识别模型在本机——`python3 tools/fetch_model.py --dest ~/x2-models` 跑过一次即可。**模型会被打进可执行文件**,这样客户在没有外网的现场也能部署。构建脚本会打印是否打包了模型,留意那一行。

### 2. 打 tag

```bash
git tag -a v1.0.0 -m "部署工具 v1.0.0"
git push origin v1.0.0
```

### 3. 建 Release 并上传两个文件

GitHub → Releases → Draft a new release → 选刚才的 tag,上传:

- `X2AutoGreeter-Windows.exe`
- `X2AutoGreeter-Linux`

说明正文用 `CUSTOMER_README.md` 的内容,客户在下载页就能看到该怎么用、以及 Windows 那个安全警告怎么过。

## 版本号

改动只在部署工具本身(界面、问候语校验、打包)就升次版本号;改动涉及**发到机器人上的代码**(`x2_greeter_ws/`)就升主版本号——那意味着已部署的机器人和新工具不再是同一套东西。

## 可以自动化

推 tag 时自动在 Windows 和 Linux 上各编译一次并上传,用 GitHub Actions 的 `windows-latest` 和 `ubuntu-latest` 两个 runner。这样台式机都不用开。现在还没做,需要时说一声。

## 一个还没解决的问题

Windows 的 `.exe` 没有代码签名,客户下载后双击会看到「Windows 已保护你的电脑」,默认那个界面上**只有"不运行"一个按钮**,要点「更多信息」才会出现「仍要运行」。

非技术客户看到这个多半会停下来打电话。`CUSTOMER_README.md` 里写了这一步,但真正的解决办法是买一张代码签名证书(每年约 200–400 美元)。在客户量少的时候,提前说明比买证书划算;客户多了就该买。

杀毒软件误报也是同类问题——PyInstaller 单文件程序运行时自解压,常被启发式规则命中。
