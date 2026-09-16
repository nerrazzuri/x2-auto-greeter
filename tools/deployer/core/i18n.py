"""Every sentence a customer can see, in both languages, in one place.

Not Qt's .ts/.qm machinery: that needs lrelease in the build, a second set of
files to keep in step, and a tool nobody here will open. This is a dict, and
adding a language means adding a column.

The rule that matters is that `core/` speaks in keys, not sentences. A failure
raised deep in the deployment has to be readable in whichever language the
window happens to be in, and it cannot know that -- so it raises a key and its
parameters, and the sentence is chosen at the moment it is shown.

English is not a translation of the Chinese here. Both were written for the
person in front of the robot: the Chinese for the engineer or operator who
knows the machine, the English for a mall's staff who do not.
"""
from __future__ import annotations

import locale
from typing import Dict

LANGUAGES = ('zh', 'en')
_current = 'zh'

STRINGS: Dict[str, Dict[str, str]] = {
    # -- the window ---------------------------------------------------------
    'app.title': {
        'zh': 'X2 迎宾部署',
        'en': 'X2 Greeter Deployment'},
    'app.language': {'zh': 'English', 'en': '中文'},
    'app.more': {'zh': '更多操作', 'en': 'More actions'},

    'section.robot': {'zh': '1 · 机器人', 'en': '1 · Robot'},
    'section.phrases': {'zh': '2 · 问候语', 'en': '2 · Greetings'},
    'section.deploy': {'zh': '3 · 部署', 'en': '3 · Deploy'},
    'section.log': {'zh': '过程记录', 'en': 'Progress'},
    'log.saved_to': {
        'zh': '完整记录保存在:{path}(出问题时请把这个文件发给技术支持)',
        'en': 'Full log saved to: {path} (send this file to support if '
              'something goes wrong)'},
    'log.open': {'zh': '打开日志文件夹', 'en': 'Open log folder'},

    'robot.detect': {'zh': '检测机器人', 'en': 'Find robot'},

    # -- watching for the robot ---------------------------------------------
    'watch.no_link': {
        'zh': '没有检测到有线网络。请把网线一头插在机器人上,一头插在这台电脑上。',
        'en': 'No wired network found. Plug the cable into the robot and into '
              'this computer.'},
    'watch.wrong_subnet': {
        'zh': '本机有线网口的地址是 {address},和机器人 {host} 不在同一网段。',
        'en': 'This computer’s wired address is {address}, which is not on the '
              'same network as the robot at {host}.'},
    'watch.no_ssh': {
        'zh': '网段正确,但 {host} 没有回应。请确认机器人已开机,网线两头都插好。',
        'en': 'The network is right, but {host} is not answering. Check the '
              'robot is switched on and both ends of the cable are in.'},
    'watch.ready': {'zh': '机器人已就绪:{identity}', 'en': 'Robot ready: {identity}'},

    'watch.label_no_link': {'zh': '等待网线接入…', 'en': 'Waiting for the cable…'},
    'watch.label_wrong_subnet': {'zh': '网段不对', 'en': 'Wrong network'},
    'watch.label_no_ssh': {'zh': '机器人没有回应', 'en': 'Robot not answering'},
    'watch.label_ready': {'zh': '机器人已连接', 'en': 'Robot connected'},
    'watch.how_to_fix': {'zh': '怎么设置?', 'en': 'How do I fix this?'},
    'watch.fix_title': {'zh': '设置网络地址', 'en': 'Set the network address'},
    'robot.prompt': {
        'zh': '把网线插到机器人上,然后点「检测机器人」。',
        'en': 'Plug the network cable into the robot, then press “Find robot”.'},
    'robot.connecting': {'zh': '正在连接…', 'en': 'Connecting…'},
    'robot.target': {
        'zh': '连接目标 {host} · 用户 {user}',
        'en': 'Connecting to {host} as {user}'},
    'robot.cannot_connect': {'zh': '连不上机器人', 'en': 'Cannot reach the robot'},

    'phrases.venue': {'zh': '场地', 'en': 'Venue'},
    'phrases.venue_hint': {
        'zh': '场地名称,例如:KL Gateway Mall(可留空)',
        'en': 'Venue name, e.g. KL Gateway Mall (optional)'},
    'phrases.column': {
        'zh': '机器人会说的话(每行一句,随机挑一句)',
        'en': 'What the robot says (one per line, picked at random)'},
    'phrases.add': {'zh': '加一句', 'en': 'Add'},
    'phrases.delete': {'zh': '删除选中', 'en': 'Delete selected'},
    'phrases.open': {'zh': '打开问候语文件', 'en': 'Open greetings file'},
    'phrases.fix': {'zh': '自动修正', 'en': 'Fix automatically'},
    'phrases.save': {'zh': '保存到文件', 'en': 'Save to file'},
    'phrases.open_title': {'zh': '打开问候语文件', 'en': 'Open greetings file'},
    'phrases.save_title': {'zh': '保存问候语', 'en': 'Save greetings'},
    'phrases.filter': {
        'zh': '问候语 (phrases*.yaml *.txt);;所有文件 (*)',
        'en': 'Greetings (phrases*.yaml *.txt);;All files (*)'},
    'phrases.filter_save': {'zh': '问候语 (*.yaml)', 'en': 'Greetings (*.yaml)'},
    'phrases.not_greetings': {
        'zh': '这个文件不是问候语', 'en': 'That file has no greetings in it'},
    'phrases.opened': {'zh': '已导入 {count} 句:{path}', 'en': 'Opened {count} greetings: {path}'},
    'phrases.saved': {'zh': '已保存:{path}', 'en': 'Saved: {path}'},
    'phrases.open_failed': {'zh': '打开失败:{error}', 'en': 'Could not open: {error}'},
    'phrases.fixed': {
        'zh': '自动修正:{before} 句 → {after} 句',
        'en': 'Fixed: {before} greetings → {after}'},
    'phrases.all_good': {'zh': '✓ {count} 句,没有问题。', 'en': '✓ {count} greetings, all good.'},
    'phrases.more': {'zh': '(还有 {count} 处)', 'en': ' (and {count} more)'},
    'phrases.can_fix': {'zh': '　可以点「自动修正」', 'en': '  — press “Fix automatically”'},

    'phrases.will_use_standard': {
        'zh': '未填写问候语。直接点「开始部署」会使用内置的通用问候语。',
        'en': 'No greetings written. Pressing Deploy will use the built-in '
              'standard greetings.'},
    'phrases.standard_title': {
        'zh': '使用通用问候语?', 'en': 'Use the standard greetings?'},
    'phrases.standard_body': {
        'zh': '你还没有填写问候语。机器人将使用内置的 {count} 句通用问候语,'
              '每次随机挑一句,例如:',
        'en': 'You have not written any greetings. The robot will use the '
              '{count} built-in standard greetings, picking one at random '
              'each time. For example:'},
    'phrases.standard_use': {'zh': '就用通用的', 'en': 'Use the standard ones'},
    'phrases.standard_write': {'zh': '我要自己写', 'en': 'Let me write my own'},
    'phrases.standard_chosen': {
        'zh': '使用内置的 {count} 句通用问候语',
        'en': 'Using the {count} built-in standard greetings'},
    'phrases.none_title': {'zh': '没有问候语', 'en': 'No greetings'},
    'phrases.none_body': {
        'zh': '请先填写至少一句问候语,否则机器人看到人也不会说话。',
        'en': 'Write at least one greeting first, or the robot will see people '
              'and say nothing.'},
    'weights.ready': {'zh': '✓ 识别模型已就绪:{path}', 'en': '✓ Vision model ready: {path}'},
    'weights.missing': {
        'zh': '识别模型还没下载。机器人靠它认出人;缺了会看不清。'
              '请在能上网时点右边下载一次,以后就不用了。',
        'en': 'The vision model has not been downloaded. The robot needs it to '
              'recognise people. Press Download once, while this computer has '
              'internet; it is kept for next time.'},
    'weights.download': {'zh': '下载识别模型', 'en': 'Download vision model'},
    'weights.state_ready': {'zh': '识别模型:已就绪', 'en': 'Vision model: Ready'},
    'weights.state_missing': {'zh': '识别模型:未就绪', 'en': 'Vision model: Not ready'},
    'weights.why': {
        'zh': '机器人靠它认出人。请在能上网时下载一次,之后就一直可用。',
        'en': 'The robot needs it to recognise people. Download once while '
              'this computer has internet; it is kept for next time.'},
    'weights.downloading': {'zh': '下载 {name} {percent}%', 'en': 'Downloading {name} {percent}%'},
    'weights.downloaded': {'zh': '识别模型已下载到 {path}', 'en': 'Vision model downloaded to {path}'},
    'weights.download_failed_title': {'zh': '下载失败', 'en': 'Download failed'},
    'weights.download_failed': {'zh': '下载失败:{error}', 'en': 'Download failed: {error}'},
    'weights.needed': {'zh': '还不能部署', 'en': 'Not ready to deploy'},
    'weights.needed_body': {
        'zh': '请先下载识别模型,机器人需要它来认出人。',
        'en': 'Download the vision model first — the robot needs it to see people.'},

    'deploy.with_autostart': {
        'zh': '部署,并让机器人每次开机自动运行(推荐)',
        'en': 'Deploy, and start it every time the robot boots (recommended)'},
    'deploy.once': {
        'zh': '只部署这一次(机器人关机后就不再运行)',
        'en': 'Deploy once (stops when the robot is powered off)'},
    'deploy.start': {'zh': '开始部署', 'en': 'Deploy'},
    'deploy.idle': {'zh': '尚未开始', 'en': 'Not started'},
    'deploy.begin': {'zh': '—— 开始部署 ——', 'en': '—— Deploying ——'},
    'deploy.finished': {'zh': '—— 完成 ——', 'en': '—— Done ——'},
    'deploy.unfinished': {'zh': '—— 未完成 ——', 'en': '—— Not finished ——'},
    'deploy.done': {'zh': '完成', 'en': 'Done'},
    'deploy.failed': {'zh': '未完成', 'en': 'Not finished'},
    'deploy.success': {
        'zh': '✓ 部署完成,机器人已经开始工作。\n机器人:{identity}\n实际运行:{startup}',
        'en': '✓ Deployed. The robot is working.\nRobot: {identity}\n'
              'Actually running: {startup}'},
    'deploy.failure': {
        'zh': '✗ 没有部署成功。下面「过程记录」的最后一行说明了原因。',
        'en': '✗ Not deployed. The last line under “Progress” below says why.'},

    # -- steps --------------------------------------------------------------
    'step.check': {'zh': '检查问候语', 'en': 'Checking greetings'},
    'step.connect': {'zh': '连接机器人', 'en': 'Connecting to the robot'},
    'step.upload': {'zh': '上传程序', 'en': 'Uploading the program'},
    'step.weights': {'zh': '上传识别模型', 'en': 'Uploading the vision model'},
    'step.build': {'zh': '编译', 'en': 'Building'},
    'step.configure': {'zh': '写入问候语和配置', 'en': 'Writing greetings and settings'},
    'step.service': {'zh': '设置开机自启', 'en': 'Setting up automatic start'},
    'step.start': {'zh': '启动机器人程序', 'en': 'Starting the robot program'},
    'step.verify': {'zh': '确认机器人已就绪', 'en': 'Confirming the robot is ready'},

    'step.files': {'zh': '{count} 个文件', 'en': '{count} files'},
    'step.build_done': {'zh': '完成', 'en': 'done'},
    'step.phrase_count': {'zh': '{count} 句问候语', 'en': '{count} greetings'},
    'step.autostart_on': {'zh': '开机后会自动运行', 'en': 'will start on boot'},
    'step.started': {'zh': '已启动', 'en': 'started'},
    'step.waiting_camera': {'zh': '等待相机启动…', 'en': 'Waiting for the camera…'},

    # -- uninstalling -------------------------------------------------------
    'uninstall.button': {'zh': '从机器人卸载', 'en': 'Remove from robot'},
    'uninstall.confirm_title': {'zh': '确认卸载?', 'en': 'Remove the greeter?'},
    'uninstall.confirm_body': {
        'zh': '这会把迎宾程序从机器人上完全删除,包括开机自启和已经写好的问候语。'
              '机器人自带的其他功能不受影响。',
        'en': 'This removes the greeter from the robot completely, including '
              'the automatic start and the greetings already written. '
              'Everything else the robot does is unaffected.'},
    'uninstall.confirm_yes': {'zh': '删除', 'en': 'Remove'},
    'uninstall.confirm_no': {'zh': '取消', 'en': 'Cancel'},
    'uninstall.stop': {'zh': '停止并取消自启', 'en': 'Stopping and disabling'},
    'uninstall.remove_unit': {'zh': '删除服务定义', 'en': 'Removing the service'},
    'uninstall.remove_files': {'zh': '删除程序文件', 'en': 'Removing the files'},
    'uninstall.verify': {'zh': '确认已清理干净', 'en': 'Confirming it is gone'},
    'uninstall.stopped': {'zh': '已停止', 'en': 'stopped'},
    'uninstall.unit_removed': {'zh': '已删除', 'en': 'removed'},
    'uninstall.files_removed': {'zh': '已删除 /home/run/x2_greeter', 'en': 'removed /home/run/x2_greeter'},
    'uninstall.clean': {'zh': '机器人上已经没有残留', 'en': 'nothing left on the robot'},
    'uninstall.still_running': {
        'zh': '服务停不下来,没有继续删除。请稍后重试。',
        'en': 'The service would not stop, so nothing was deleted. Try again '
              'shortly.'},
    'uninstall.files_failed': {
        'zh': '删除文件失败:{detail}', 'en': 'Could not delete the files: {detail}'},
    'uninstall.leftovers': {
        'zh': '还有残留没清掉:{detail}', 'en': 'Something is still there: {detail}'},
    'uninstall.done': {
        'zh': '✓ 已从机器人上完全卸载。机器人自带的功能不受影响。',
        'en': '✓ Removed from the robot. Everything else it does is unaffected.'},
    'uninstall.failed': {
        'zh': '✗ 卸载没有完成。下面「过程记录」的最后一行说明了原因。',
        'en': '✗ Not removed. The last line under “Progress” below says why.'},

    # -- failures -----------------------------------------------------------
    'err.password': {
        'zh': '{user}@{host} 的密码不对。X2 出厂密码是 "1"。',
        'en': 'Wrong password for {user}@{host}. The X2 ships with "1".'},
    'err.unreachable': {
        'zh': '连不上 {host}。请检查网线是否插好,以及本机的有线网口地址是不是 10.0.1.x/24。',
        'en': 'Cannot reach {host}. Check the network cable, and that this '
              'computer’s wired adapter has an address like 10.0.1.x/24.'},
    'err.not_connected': {'zh': '还没有连接到机器人', 'en': 'Not connected to the robot yet'},
    'err.sudo': {
        'zh': 'sudo 密码不对,装不了开机自启。',
        'en': 'The sudo password was refused, so automatic start cannot be set up.'},
    'err.not_an_x2': {
        'zh': '读不到机器人的板子序列号,这可能不是一台 X2。',
        'en': 'Could not read the board serial number — this may not be an X2.'},
    'err.no_weights': {
        'zh': '缺少识别模型文件,机器人会看不清人。请在有网络的地方先下载一次。',
        'en': 'The vision model is missing, so the robot would not see people '
              'properly. Download it once somewhere with internet.'},
    'err.build': {'zh': '编译失败。{detail}', 'en': 'Build failed. {detail}'},
    'err.no_site': {
        'zh': '生成配置文件失败,可能是编译没有真正完成。',
        'en': 'The settings file was not created — the build may not have finished.'},
    'err.service': {'zh': '装开机自启失败:{detail}', 'en': 'Could not set up automatic start: {detail}'},
    'err.start': {'zh': '启动失败:{detail}', 'en': 'Could not start: {detail}'},
    'err.wrong_config': {
        'zh': '机器人起来了,但配置不对:{detail}',
        'en': 'The robot started, but with the wrong settings: {detail}'},
    'err.wrong_field': {
        'zh': '{key} 应为 {want},实际是 {got}',
        'en': '{key} should be {want}, but is {got}'},
    'err.never_ready': {
        'zh': '机器人程序启动了,但两分钟内没有报告就绪。多半是机器人自己的相机软件还没起来,'
              '可以稍后再试一次。',
        'en': 'The robot program started but did not report ready within two '
              'minutes. Its camera software is probably still starting — try again shortly.'},
    'err.download': {
        'zh': '下载识别模型失败({name})。请确认这台电脑能上网,或者在有网的地方先下载一次。',
        'en': 'Could not download the vision model ({name}). Check that this '
              'computer has internet, or download it somewhere that does.'},
    'err.checksum': {
        'zh': '下载的识别模型文件校验不通过:{names}',
        'en': 'The downloaded vision model failed its checksum: {names}'},
    'robot.identity': {
        'zh': '{host} · 序列号 {serial} · {mac}',
        'en': '{host} · serial {serial} · {mac}'},
    'err.no_block': {
        'zh': '配置文件里找不到 "{block}:" 这一段,机器人上的程序可能不完整。',
        'en': 'No "{block}:" section in the settings file — the program on the '
              'robot may be incomplete.'},

    # -- phrase problems ----------------------------------------------------
    'problem.line': {'zh': '第 {line} 句:{message}', 'en': 'Greeting {line}: {message}'},
    'problem.empty_list': {
        'zh': '一句问候语都没有,机器人不会说话',
        'en': 'There are no greetings, so the robot would say nothing'},
    'problem.blank': {'zh': '这一行是空的', 'en': 'this line is empty'},
    'problem.bad_chars': {
        'zh': '含有 TTS 读不出的字符 {chars},多半是从 Word 粘贴来的',
        'en': 'has characters the robot cannot say ({chars}) — usually pasted from Word'},
    'problem.numbering': {
        'zh': '开头的编号会被一起念出来',
        'en': 'the number at the start would be read out too'},
    'problem.markup': {
        'zh': '含有会被念出来的符号 {chars}',
        'en': 'has symbols that would be read out loud ({chars})'},
    'problem.too_long': {
        'zh': '太长了({length} 字,上限 {limit}),说完人已经走了',
        'en': 'too long ({length} characters, limit {limit}) — they will have walked off'},
    'problem.duplicate': {'zh': '和第 {line} 句重复', 'en': 'same as greeting {line}'},
    'problem.not_greetings': {
        'zh': '{name} 是部署设置文件,不是问候语。问候语文件的名字通常以 phrases- 开头,'
              '例如 phrases-klgw.yaml。',
        'en': '{name} is a settings file, not greetings. A greetings file is '
              'usually named phrases-something.yaml, for example phrases-klgw.yaml.'},
    'problem.no_phrases': {
        'zh': '{name} 里没有问候语。问候语文件应该是这样的:\n'
              'phrases:\n  - "第一句"\n  - "第二句"\n'
              '也可以直接用记事本写一个 .txt,一句一行。',
        'en': '{name} has no greetings in it. A greetings file looks like this:\n'
              'phrases:\n  - "First greeting"\n  - "Second greeting"\n'
              'A plain .txt with one greeting per line works too.'},
    'problem.phrases_not_list': {
        'zh': '{name} 里的 "phrases:" 下面应该是一句一行的列表,现在不是。',
        'en': 'In {name}, "phrases:" should be followed by one greeting per line.'},

    # -- network ------------------------------------------------------------
    'net.connected': {'zh': '已连接到 {host}', 'en': 'Connected to {host}'},
    'net.wrong_subnet': {
        'zh': '这台电脑不在机器人所在的网段 {where}。\n{fix}',
        'en': 'This computer is not on the robot’s network {where}.\n{fix}'},
    'net.here': {'zh': '(本机当前地址 {address})', 'en': '(currently {address})'},
    'net.silent': {
        'zh': '本机地址 {address} 在正确的网段上,但 {host} 没有回应。\n'
              '请检查网线两头是否都插好,以及机器人是否已经开机。',
        'en': 'This computer is on the right network ({address}) but {host} is '
              'not answering.\nCheck both ends of the cable, and that the robot is on.'},
    'net.fix_windows': {
        'zh': '请把有线网卡的 IP 设为固定地址:\n'
              '  控制面板 → 网络和 Internet → 网络连接\n'
              '  右键「以太网」→ 属性 → Internet 协议版本 4 (TCP/IPv4) → 属性\n'
              '  选「使用下面的 IP 地址」,填:\n'
              '    IP 地址   {address}\n    子网掩码  255.255.255.0\n'
              '  网关和 DNS 留空。',
        'en': 'Give the wired adapter a fixed address:\n'
              '  Control Panel → Network and Internet → Network Connections\n'
              '  Right-click Ethernet → Properties → Internet Protocol Version 4 '
              '(TCP/IPv4) → Properties\n'
              '  Choose “Use the following IP address” and enter:\n'
              '    IP address   {address}\n    Subnet mask  255.255.255.0\n'
              '  Leave the gateway and DNS empty.'},
    'net.fix_linux': {
        'zh': '请把有线网卡的 IP 设为固定地址:\n'
              '  设置 → 网络 → 有线 → 齿轮图标 → IPv4\n'
              '  选「手动」,填:\n'
              '    地址    {address}\n    子网掩码 255.255.255.0\n'
              '  网关留空。',
        'en': 'Give the wired adapter a fixed address:\n'
              '  Settings → Network → Wired → gear icon → IPv4\n'
              '  Choose Manual and enter:\n'
              '    Address  {address}\n    Netmask  255.255.255.0\n'
              '  Leave the gateway empty.'},
}


def set_language(code: str) -> None:
    global _current
    if code not in LANGUAGES:
        raise ValueError(f'unknown language {code!r}')
    _current = code


def language() -> str:
    return _current


def use_system_language() -> str:
    """Chinese for a Chinese system, English for anything else.

    English is the safer default for an unknown locale: a Malaysian mall's
    staff will read it, and a Chinese operator reads both.
    """
    try:
        code = (locale.getlocale()[0] or locale.getdefaultlocale()[0] or '')
    except (ValueError, TypeError):
        code = ''
    set_language('zh' if code.lower().startswith(('zh', 'chinese')) else 'en')
    return _current


def t(key: str, /, **params) -> str:
    """One sentence, in the language in force. Unknown keys surface as keys.

    `key` is positional-only so a message may itself have a parameter called
    "key" -- err.wrong_field does, and naming it collided with this function's
    own argument, which surfaced as a TypeError in place of the sentence
    explaining that a robot had come up with the wrong backend.

    A missing translation shows the key rather than raising: a customer
    halfway through a deployment is better served by `step.upload` on screen
    than by the window closing.
    """
    entry = STRINGS.get(key)
    if entry is None:
        return key
    text = entry.get(_current) or entry.get('zh') or key
    try:
        return text.format(**params) if params else text
    except (KeyError, IndexError):
        return text
