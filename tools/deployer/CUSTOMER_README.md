# X2 迎宾机器人 · 部署工具 / Deployment Tool

下载一个文件,双击,按界面上的三步走完即可。不需要安装 Python 或任何其他软件。

Download one file, double-click it, and follow the three steps in the window.
Nothing else needs to be installed.

---

## 下载 / Download

| 你的电脑 / Your computer | 下载 / Download |
|---|---|
| Windows | `X2AutoGreeter-Windows.exe` |
| Linux | `X2AutoGreeter-Linux` |

---

## Windows:第一次打开会看到安全提示

Windows 对所有从网上下载的新程序都会这样提示,不代表程序有问题。

1. 双击 `X2AutoGreeter-Windows.exe`
2. 出现「**Windows 已保护你的电脑**」时,点左下角的「**更多信息**」
3. 再点出现的「**仍要运行**」

如果杀毒软件拦截,请把这个文件加入信任列表。

### Windows: the first time you open it

Windows shows this for every new program downloaded from the internet. It does
not mean anything is wrong with the file.

1. Double-click `X2AutoGreeter-Windows.exe`
2. When **“Windows protected your PC”** appears, click **More info**
3. Then click **Run anyway**

If your antivirus blocks it, add the file to its allowed list.

---

## Linux:第一次打开前要加执行权限

浏览器下载后会去掉执行权限。在文件上右键 → 属性 → 权限 → 勾选「允许作为程序执行」,然后双击。

或者在终端里:

```bash
chmod +x X2AutoGreeter-Linux
./X2AutoGreeter-Linux
```

### Linux: allow it to run

Browsers remove the executable permission on download. Right-click the file →
Properties → Permissions → tick “Allow executing file as program”, then
double-click it. Or in a terminal, use the two commands above.

---

## 使用 / Using it

### 1 · 接上网线 / Connect the cable

把网线一头插在机器人上,一头插在这台电脑上,然后点「**检测机器人**」。

工具会显示这台机器人的序列号——请记录下来,这是区分多台机器人的唯一方式。

如果连不上,工具会告诉你是网线的问题还是网络地址的问题,并给出具体怎么改。

Plug the network cable into the robot and into this computer, then press
**Find robot**. The tool shows the robot's serial number — write it down, it is
the only way to tell two robots apart. If it cannot connect, it will say
whether the problem is the cable or the network address, and what to change.

### 2 · 填问候语 / Write the greetings

窗口打开时已经有一组**示例问候语**,可以直接用它们部署,先确认整套流程是通的。

确认之后,把它们换成你自己的话术:

- 在表格里直接编辑,每行一句
- 或者点「**打开问候语文件**」,导入一个记事本写的 `.txt`(一句一行)或 `.yaml`
- 有问题的行会标红并说明原因;多数问题点「**自动修正**」即可

改好之后点「**保存到文件**」,**只要起个名字**(例如场地名),不用选路径——工具会存在自己的文件夹里,并且**下次打开时自动载入**。所以一份维护好的问候语只需要导入一次。

机器人每次问候会从这些句子里**随机挑一句**。

The window opens with **sample greetings** already filled in — deploy with
those first, to confirm everything works. Then replace them with your own
wording: edit the table directly, or press **Open greetings file** to import a
plain `.txt` with one greeting per line. Problem lines are highlighted with a
reason; most are cleared by **Fix automatically**.

**Save to file** asks only for a name — no folders to choose. The tool keeps it
and reopens it the next time you start, so a list you have worked on only ever
has to be imported once. The robot picks one at random each time it greets
someone.

### 3 · 点「开始部署」/ Press Deploy

大约需要三分钟。全部完成后,工具会显示机器人自己报告的运行状态——只有机器人确认无误,才算部署成功。

机器人会在开机后自动运行,不需要每次手动启动。

It takes about three minutes. When it finishes, the tool shows what the robot
itself reports it is running — the deployment only counts as successful once
the robot confirms it. The robot starts automatically after a reboot.

---

## 让机器人做手势 / Gestures

机器人只有在**力控站立**状态下才会做手势。请用遥控器把机器人切到力控站立——部署工具永远不会自己改变机器人的运动状态。

The robot only gestures when it is in force-control stand. Use the remote to
put it there. The deployment tool never changes the robot's motion mode by
itself.

---

## 开关机器人的问候功能 / Turning it on and off

部署完成后,如果需要临时关闭,在机器人上执行:

```
sudo systemctl stop x2-greeter       # 停止(重启后仍会自动运行)
sudo systemctl disable --now x2-greeter   # 彻底关闭
sudo systemctl enable --now x2-greeter    # 重新开启
```

关闭迎宾功能**不影响机器人自带的其他功能**。

Stopping the greeter does not affect anything else the robot does.

---

## 界面语言 / Language

右上角可以在中文和 English 之间切换,已经填好的内容不会丢失。

Switch between 中文 and English in the top-right corner. Nothing you have
typed is lost.
