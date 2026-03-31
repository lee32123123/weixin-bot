import subprocess
import base64
import hashlib
import os
import shlex
import struct
import threading
import xml.etree.ElementTree as ET
from flask import Flask, request, make_response
import requests
from Crypto.Cipher import AES

app = Flask(__name__)

# ═══════════════════════════════════════════════════
#  配置
# ═══════════════════════════════════════════════════
CORP_ID          = 'wwf70d6db10034246e'
SECRET           = 'W_jkwpEjBNhlOUmznS3VpUUqNGjmeu1UGbpkNy1CJ3s'
AGENT_ID         = '1000003'
TOKEN            = 'weixin123'
ENCODING_AES_KEY = 'sitCJVb3Z6dDYPORElvpQRZfVe8PyRZryVuj3Ujqn4l'

AUTHORIZED_USERS = {'LiChunYu'}

# 危险命令黑名单
BLOCKED_COMMANDS = {
    'rm', 'mkfs', 'dd', 'format', 'del', 'rd',
    'rmdir', 'fdisk', 'diskpart'
}

# Claude 浏览器配置
CLAUDE_PROFILE_DIR = r'C:\Users\PC\claude_profile'

# ═══════════════════════════════════════════════════
#  企业微信签名 & 解密
# ═══════════════════════════════════════════════════
def _verify_signature(msg_signature, timestamp, nonce, echostr):
    items = sorted([TOKEN, timestamp, nonce, echostr])
    sha1 = hashlib.sha1(''.join(items).encode('utf-8')).hexdigest()
    return sha1 == msg_signature

def _aes_decrypt(encrypted):
    aes_key = base64.b64decode(ENCODING_AES_KEY + '=')
    cipher = AES.new(aes_key, AES.MODE_CBC, aes_key[:16])
    decrypted = cipher.decrypt(base64.b64decode(encrypted))
    pad = decrypted[-1]
    decrypted = decrypted[:-pad]
    msg_len = struct.unpack('>I', decrypted[16:20])[0]
    return decrypted[20:20 + msg_len].decode('utf-8')

# ═══════════════════════════════════════════════════
#  发送消息
# ═══════════════════════════════════════════════════
def get_access_token():
    url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={CORP_ID}&corpsecret={SECRET}"
    return requests.get(url).json().get('access_token')

def send_message(user_id, content, access_token=None):
    if access_token is None:
        access_token = get_access_token()
    if len(content) > 2048:
        content = content[:2048] + '\n...(已截断)'
    requests.post(
        f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access_token}",
        json={"touser": user_id, "msgtype": "text", "agentid": AGENT_ID,
              "text": {"content": content}}
    )

def send_image(user_id, image_path, access_token=None):
    if access_token is None:
        access_token = get_access_token()
    with open(image_path, 'rb') as f:
        resp = requests.post(
            f"https://qyapi.weixin.qq.com/cgi-bin/media/upload?access_token={access_token}&type=image",
            files={'media': f}
        )
    media_id = resp.json().get('media_id')
    if not media_id:
        send_message(user_id, "图片上传失败", access_token)
        return
    requests.post(
        f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access_token}",
        json={"touser": user_id, "msgtype": "image", "agentid": AGENT_ID,
              "image": {"media_id": media_id}}
    )

# ═══════════════════════════════════════════════════
#  命令处理
# ═══════════════════════════════════════════════════

# ── /help ──────────────────────────────────────────
def handle_help(user_id, access_token):
    send_message(user_id, (
        "═══ 手机控制电脑 ═══\n\n"
        "【系统】\n"
        "/sysinfo          CPU/内存/磁盘信息\n"
        "/ip               查看本机IP\n"
        "/screenshot       截取桌面\n"
        "/lock             锁定屏幕\n"
        "/shutdown         关机\n"
        "/restart          重启\n"
        "/sleep            睡眠\n\n"
        "【终端】\n"
        "/run <命令>        执行命令\n"
        "/tasklist         查看进程\n"
        "/kill <进程名>     结束进程\n\n"
        "【文件】\n"
        "/ls <路径>         列出文件\n"
        "/read <路径>       读取文本文件\n\n"
        "【媒体】\n"
        "/volume <0-100>   调节音量\n"
        "/mute             静音/取消静音\n\n"
        "【剪贴板】\n"
        "/clipboard        获取剪贴板内容\n"
        "/setclip <内容>   设置剪贴板\n\n"
        "【AI】\n"
        "/claude <问题>    网页版Claude回答\n"
        "/summarize <路径> AI总结文档\n"
    ), access_token)

# ── /run ───────────────────────────────────────────
def is_command_allowed(cmd):
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return False
    if not parts:
        return False
    base = os.path.basename(parts[0]).lower().replace('.exe', '')
    return base not in BLOCKED_COMMANDS

def handle_run(user_id, cmd, access_token):
    if not is_command_allowed(cmd):
        send_message(user_id, f"命令被拒绝（黑名单）: {cmd}", access_token)
        return
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True,
            timeout=30, encoding='gbk', errors='replace'
        )
        output = result.stdout or result.stderr or "(无输出)"
        send_message(user_id, f"> {cmd}\n\n{output}", access_token)
    except subprocess.TimeoutExpired:
        send_message(user_id, f"命令超时(>30s): {cmd}", access_token)
    except Exception as e:
        send_message(user_id, f"执行失败: {e}", access_token)

# ── /screenshot ────────────────────────────────────
def handle_screenshot(user_id, access_token):
    path = os.path.join(os.environ.get('TEMP', 'C:\\Temp'), 'wx_screenshot.png')
    try:
        script = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
            "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
            "$bmp=New-Object System.Drawing.Bitmap($b.Width,$b.Height);"
            "$g=[System.Drawing.Graphics]::FromImage($bmp);"
            "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
            f"$bmp.Save('{path}');"
            "$g.Dispose();$bmp.Dispose()"
        )
        r = subprocess.run(['powershell', '-Command', script], capture_output=True, timeout=15)
        if r.returncode == 0 and os.path.exists(path):
            send_image(user_id, path, access_token)
        else:
            send_message(user_id, "截图失败", access_token)
    except Exception as e:
        send_message(user_id, f"截图异常: {e}", access_token)
    finally:
        if os.path.exists(path):
            os.remove(path)

# ── /sysinfo ───────────────────────────────────────
def handle_sysinfo(user_id, access_token):
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('C:\\')
        boot = psutil.boot_time()
        import datetime
        uptime = datetime.datetime.now() - datetime.datetime.fromtimestamp(boot)
        hours, rem = divmod(int(uptime.total_seconds()), 3600)
        minutes = rem // 60
        send_message(user_id, (
            "═══ 系统信息 ═══\n"
            f"CPU使用率:  {cpu}%\n"
            f"内存使用:   {mem.used/1024**3:.1f}GB / {mem.total/1024**3:.1f}GB ({mem.percent}%)\n"
            f"磁盘(C:):   {disk.used/1024**3:.1f}GB / {disk.total/1024**3:.1f}GB ({disk.percent}%)\n"
            f"已运行:     {hours}小时{minutes}分钟"
        ), access_token)
    except ImportError:
        # psutil 未安装时回退到 wmic
        r = subprocess.run('wmic cpu get loadpercentage', capture_output=True,
                           encoding='gbk', errors='replace', shell=True)
        send_message(user_id, r.stdout or "获取失败（建议 pip install psutil）", access_token)

# ── /ip ────────────────────────────────────────────
def handle_ip(user_id, access_token):
    r = subprocess.run('ipconfig', capture_output=True, encoding='gbk',
                       errors='replace', shell=True)
    lines = [l for l in r.stdout.splitlines()
             if 'IPv4' in l or 'IPv6' in l or '适配器' in l or 'Adapter' in l]
    send_message(user_id, '\n'.join(lines) or "获取失败", access_token)

# ── /lock / /shutdown / /restart / /sleep ──────────
def handle_power(user_id, action, access_token):
    cmds = {
        'lock':     'rundll32.exe user32.dll,LockWorkStation',
        'shutdown': 'shutdown /s /t 10',
        'restart':  'shutdown /r /t 10',
        'sleep':    'rundll32.exe powrprof.dll,SetSuspendState 0,1,0',
    }
    cmd = cmds.get(action)
    if not cmd:
        return
    labels = {'lock': '锁屏', 'shutdown': '10秒后关机', 'restart': '10秒后重启', 'sleep': '睡眠'}
    subprocess.Popen(cmd, shell=True)
    send_message(user_id, f"已执行: {labels[action]}", access_token)

# ── /tasklist ──────────────────────────────────────
def handle_tasklist(user_id, access_token):
    r = subprocess.run('tasklist /fo csv /nh', capture_output=True,
                       encoding='gbk', errors='replace', shell=True)
    lines = r.stdout.strip().splitlines()
    result = []
    for line in lines[:30]:
        parts = line.strip('"').split('","')
        if len(parts) >= 5:
            result.append(f"{parts[0][:25]:<25} PID:{parts[1]:<6} {parts[4]}")
    send_message(user_id, "═══ 进程列表(前30) ═══\n" + '\n'.join(result), access_token)

# ── /kill ──────────────────────────────────────────
def handle_kill(user_id, process_name, access_token):
    r = subprocess.run(f'taskkill /f /im {process_name}', capture_output=True,
                       encoding='gbk', errors='replace', shell=True)
    output = r.stdout or r.stderr or "无输出"
    send_message(user_id, output, access_token)

# ── /ls ────────────────────────────────────────────
def handle_ls(user_id, path, access_token):
    if not path:
        path = r'C:\Users\PC\Desktop'
    try:
        items = os.listdir(path)
        lines = []
        for item in sorted(items)[:50]:
            full = os.path.join(path, item)
            tag = '[目录]' if os.path.isdir(full) else '[文件]'
            size = '' if os.path.isdir(full) else f" {os.path.getsize(full)//1024}KB"
            lines.append(f"{tag} {item}{size}")
        send_message(user_id, f"📁 {path}\n\n" + '\n'.join(lines) or "空目录", access_token)
    except Exception as e:
        send_message(user_id, f"列目录失败: {e}", access_token)

# ── /read ──────────────────────────────────────────
def handle_read(user_id, path, access_token):
    if not os.path.exists(path):
        send_message(user_id, f"文件不存在: {path}", access_token)
        return
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read(3000)
        send_message(user_id, f"📄 {os.path.basename(path)}\n\n{content}", access_token)
    except Exception as e:
        send_message(user_id, f"读取失败: {e}", access_token)

# ── /volume ────────────────────────────────────────
def handle_volume(user_id, level_str, access_token):
    try:
        level = max(0, min(100, int(level_str)))
    except ValueError:
        send_message(user_id, "用法: /volume 0-100", access_token)
        return
    script = f"""
Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {{
    int _(int a); int __(int a); int ___(int a); int ____(int a);
    int SetMasterVolumeLevelScalar(float fLevel, System.Guid pg);
    int _____(int a);
    int GetMasterVolumeLevelScalar(out float pfLevel);
}}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {{ int Activate(ref System.Guid id, int ctx, int p, [MarshalAs(UnmanagedType.IUnknown)] out object ppv); }}
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {{ int EnumAudioEndpoints(int t,int s,out object d); int GetDefaultAudioEndpoint(int t,int r,out IMMDevice d); }}
[ComImport,Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorClass {{}}
public class VolumeControl {{
    public static void SetVolume(float v) {{
        var e = (IMMDeviceEnumerator)new MMDeviceEnumeratorClass();
        IMMDevice dev; e.GetDefaultAudioEndpoint(0,1,out dev);
        var iid = typeof(IAudioEndpointVolume).GUID;
        object vol; dev.Activate(ref iid,0,0,out vol);
        ((IAudioEndpointVolume)vol).SetMasterVolumeLevelScalar(v,System.Guid.Empty);
    }}
}}
'@
[VolumeControl]::SetVolume({level / 100.0:.2f})
"""
    try:
        subprocess.run(['powershell', '-Command', script], capture_output=True, timeout=10)
        send_message(user_id, f"音量已设置为 {level}%", access_token)
    except Exception as e:
        send_message(user_id, f"音量设置失败: {e}", access_token)

# ── /mute ──────────────────────────────────────────
def handle_mute(user_id, access_token):
    # 模拟按下静音键
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "[System.Windows.Forms.SendKeys]::SendWait('%{F4}')"
    )
    # 更可靠的方式：发送 VK_VOLUME_MUTE (0xAD)
    script = """
Add-Type -TypeDefinition '
using System.Runtime.InteropServices;
public class KB {
    [DllImport("user32.dll")] public static extern void keybd_event(byte k, byte s, int f, int e);
}'
[KB]::keybd_event(0xAD, 0, 0, 0)
[KB]::keybd_event(0xAD, 0, 2, 0)
"""
    try:
        subprocess.run(['powershell', '-Command', script], capture_output=True, timeout=5)
        send_message(user_id, "已切换静音状态", access_token)
    except Exception as e:
        send_message(user_id, f"静音切换失败: {e}", access_token)

# ── /open ──────────────────────────────────────────
def handle_open(user_id, app_name, access_token):
    try:
        subprocess.Popen(app_name, shell=True)
        send_message(user_id, f"已启动: {app_name}", access_token)
    except Exception as e:
        send_message(user_id, f"启动失败: {e}", access_token)

# ── /clipboard ─────────────────────────────────────
def handle_clipboard_get(user_id, access_token):
    script = "Get-Clipboard"
    r = subprocess.run(['powershell', '-Command', script], capture_output=True,
                       encoding='utf-8', errors='replace', timeout=5)
    content = r.stdout.strip() or "(剪贴板为空)"
    send_message(user_id, f"📋 剪贴板内容:\n{content}", access_token)

def handle_clipboard_set(user_id, content, access_token):
    script = f"Set-Clipboard -Value '{content}'"
    subprocess.run(['powershell', '-Command', script], capture_output=True, timeout=5)
    send_message(user_id, f"剪贴板已设置为:\n{content}", access_token)

# ── /claude (网页版自动化) ──────────────────────────
def ask_claude_web(prompt, timeout=120):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=CLAUDE_PROFILE_DIR,
            headless=False,
            args=['--start-maximized']
        )
        page = browser.new_page()
        page.goto('https://claude.ai/new')
        page.wait_for_load_state('networkidle', timeout=30000)

        # 输入内容
        editor = page.locator('[contenteditable="true"]').first
        editor.wait_for(timeout=15000)
        editor.click()
        editor.fill(prompt)

        # 发送
        page.keyboard.press('Enter')

        # 等待 Stop 按钮出现再消失（说明回答完成）
        try:
            page.wait_for_selector('button[aria-label="Stop"]', timeout=10000)
        except Exception:
            pass
        page.wait_for_function(
            "() => !document.querySelector('button[aria-label=\"Stop\"]')",
            timeout=timeout * 1000
        )

        # 获取最后一条回答
        responses = page.locator('[data-testid="assistant-message"]').all()
        result = responses[-1].inner_text() if responses else "未能获取回答"
        browser.close()
        return result

def handle_claude_web(user_id, prompt, access_token):
    send_message(user_id, f"正在调用网页版 Claude...\n问题: {prompt[:80]}", access_token)
    def run():
        try:
            result = ask_claude_web(prompt)
            send_message(user_id, f"Claude 回答：\n\n{result}", access_token)
        except Exception as e:
            send_message(user_id, f"Claude 操作失败: {e}", access_token)
    threading.Thread(target=run, daemon=True).start()

# ── /summarize (AI 总结文档) ───────────────────────
def handle_summarize(user_id, file_path, access_token):
    if not os.path.exists(file_path):
        send_message(user_id, f"文件不存在: {file_path}", access_token)
        return
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == '.txt':
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read(30000)
        elif ext == '.docx':
            try:
                from docx import Document
                doc = Document(file_path)
                content = '\n'.join(p.text for p in doc.paragraphs if p.text.strip())[:30000]
            except ImportError:
                send_message(user_id, "请先安装: pip install python-docx", access_token)
                return
        elif ext == '.pdf':
            try:
                import pdfplumber
                with pdfplumber.open(file_path) as pdf:
                    content = '\n'.join(p.extract_text() or '' for p in pdf.pages)[:30000]
            except ImportError:
                send_message(user_id, "请先安装: pip install pdfplumber", access_token)
                return
        else:
            send_message(user_id, f"不支持的格式: {ext}（支持 .txt .docx .pdf）", access_token)
            return
    except Exception as e:
        send_message(user_id, f"读取文件失败: {e}", access_token)
        return

    if not content.strip():
        send_message(user_id, "文件内容为空", access_token)
        return

    prompt = f"请对以下文档内容进行简洁总结，提取核心要点，用中文回答：\n\n{content}"
    handle_claude_web(user_id, prompt, access_token)

# ═══════════════════════════════════════════════════
#  路由
# ═══════════════════════════════════════════════════
@app.route('/callback', methods=['GET'])
def verify():
    msg_signature = request.args.get('msg_signature', '')
    timestamp     = request.args.get('timestamp', '')
    nonce         = request.args.get('nonce', '')
    echostr       = request.args.get('echostr', '')
    if not _verify_signature(msg_signature, timestamp, nonce, echostr):
        return make_response('invalid signature', 403)
    try:
        return make_response(_aes_decrypt(echostr), 200)
    except Exception as e:
        return make_response(f'decrypt failed: {e}', 500)


@app.route('/callback', methods=['POST'])
def callback():
    msg_signature = request.args.get('msg_signature', '')
    timestamp     = request.args.get('timestamp', '')
    nonce         = request.args.get('nonce', '')

    try:
        xml_tree = ET.fromstring(request.data)
        encrypt  = xml_tree.findtext('Encrypt', '')
    except Exception:
        return make_response('', 200)

    if not _verify_signature(msg_signature, timestamp, nonce, encrypt):
        return make_response('', 200)

    try:
        decrypted_xml = _aes_decrypt(encrypt)
        msg_tree = ET.fromstring(decrypted_xml)
    except Exception:
        return make_response('', 200)

    user_id  = msg_tree.findtext('FromUserName', '')
    msg_type = msg_tree.findtext('MsgType', '')
    text     = msg_tree.findtext('Content', '').strip()

    print(f"[消息] user={user_id} type={msg_type} text={text[:60]}")

    if msg_type != 'text':
        return make_response('', 200)

    if user_id not in AUTHORIZED_USERS:
        print(f"[拒绝] 未授权用户: {user_id}")
        return make_response('', 200)

    access_token = get_access_token()

    # ── 命令路由 ──────────────────────────────────
    if text == '/help':
        handle_help(user_id, access_token)

    elif text.startswith('/run '):
        handle_run(user_id, text[5:].strip(), access_token)

    elif text == '/screenshot':
        handle_screenshot(user_id, access_token)

    elif text == '/sysinfo':
        handle_sysinfo(user_id, access_token)

    elif text == '/ip':
        handle_ip(user_id, access_token)

    elif text in ('/lock', '/shutdown', '/restart', '/sleep'):
        handle_power(user_id, text[1:], access_token)

    elif text == '/tasklist':
        handle_tasklist(user_id, access_token)

    elif text.startswith('/kill '):
        handle_kill(user_id, text[6:].strip(), access_token)

    elif text.startswith('/ls'):
        handle_ls(user_id, text[3:].strip(), access_token)

    elif text.startswith('/read '):
        handle_read(user_id, text[6:].strip(), access_token)

    elif text.startswith('/volume '):
        handle_volume(user_id, text[8:].strip(), access_token)

    elif text == '/mute':
        handle_mute(user_id, access_token)

    elif text.startswith('/open '):
        handle_open(user_id, text[6:].strip(), access_token)

    elif text == '/clipboard':
        handle_clipboard_get(user_id, access_token)

    elif text.startswith('/setclip '):
        handle_clipboard_set(user_id, text[9:].strip(), access_token)

    elif text.startswith('/claude '):
        handle_claude_web(user_id, text[8:].strip(), access_token)

    elif text.startswith('/summarize '):
        handle_summarize(user_id, text[11:].strip(), access_token)

    else:
        send_message(user_id, "未知命令，发送 /help 查看所有命令", access_token)

    return make_response('', 200)


if __name__ == '__main__':
    app.run(port=5000, debug=True)
