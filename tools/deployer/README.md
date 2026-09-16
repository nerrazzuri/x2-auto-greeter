# X2 迎宾部署工具

给**没有工程师的客户**用的图形部署工具。客户只需要:插好网线 → 填问候语 → 点「开始部署」。

```bash
python3 tools/deployer/gui/app.py
```

需要 `PyQt6`、`paramiko`、`PyYAML`。

```bash
python3 -m pip install PyQt6 paramiko PyYAML
```

## 它做了什么

界面按顺序跑完这些步骤,每一步都在「过程记录」里报告:

| 步骤 | 说明 |
|---|---|
| 连接机器人 | 读出板子序列号和 MAC。所有 X2 共用同一套 SSH 主机密钥,只有序列号能区分是哪一台 |
| 上传程序 | 镜像整个包到 `/home/run/x2_greeter/repo`,`docs/` 留在本机 |
| 上传识别模型 | 机器人靠它认出人 |
| 编译 | 在机器人上 `colcon build` |
| 写入问候语和配置 | 生成 `phrases.yaml`,并设置后端、相机、语音三项 |
| 设置开机自启 | systemd 单元,机器人重启后自动运行 |
| 启动机器人程序 | `systemctl restart`,重新部署时确保新内容真的生效 |
| 确认机器人已就绪 | **等机器人自己报出启动行**,核对相机和后端确实是要的值 |

最后一步是关键。前面每一步都成功、服务也在运行,机器人仍然可能在做别的事——这是这个项目反复出现的故障。所以只有机器人自己说出 `camera=stereo backend=canned speech_tier=tts`,才算部署完成。

## 目录结构

```
core/       不含任何 Qt,可单独测试,Windows 版直接复用
  phrases.py     问候语的读写和校验
  robot.py       SSH/SFTP(paramiko,不依赖 bash/rsync/sshpass)
  assets.py      识别模型的查找与下载
  deployment.py  九个步骤
gui/
  app.py         窗口
```

为什么不直接调用 `tools/deploy/install.sh`:那是 bash,用到 `rsync`、`ssh-copy-id`、`nmcli`,Windows 上一个都没有。`core/` 用 Python 重写了同样的流程,行为一致——都只写 `/home/run/x2_greeter`,都不碰 `/agibot`,都不上传 `docs/`。

一个区别:**不在机器人上留钥匙**。客户用出厂密码完成一次部署即可,在不属于我们的机器上留下长期凭证不是我们该做的决定。

## 问候语

客户可以直接在表格里编辑,也可以导入文件:

- **`.txt`** — 一句一行,不用引号、不用缩进,记事本就能写
- **`.yaml`** — 之前部署生成的文件

界面会实时检查并标红有问题的行:

| 问题 | 能否自动修正 |
|---|---|
| Word 的弯引号 `'` `"`、破折号 `—` | 能 |
| 开头的编号 `1.` `2)` `3、` | 能 |
| 空行 | 能 |
| Markdown 符号 `**` `#` | 不能,要客户决定 |
| 句子太长(超过 260 字) | 不能 |
| 重复的句子 | 不能,删哪一句是客户的选择 |

不能自动修正的问题会**禁用部署按钮**——在笔记本上拦住,好过让机器人在商场里把星号念出来。

## 开发

```bash
env PYTHONPATH= python3 -m pytest -q x2_greeter_ws/src/x2_greeter/test/test_deployer_*.py
```

界面测试用离屏渲染,不需要显示器;没装 PyQt6 的环境会自动跳过。
