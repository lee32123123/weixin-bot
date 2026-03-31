import subprocess
import base64
import hashlib
import os
import shlex
import struct
from flask import Flask, request, jsonify, make_response
import requests
from Crypto.Cipher import AES

app = Flask(__name__)

CORP_ID = 'lee'
SECRET = 'W_jkwpEjBNhlOUmznS3VpUUqNGjmeu1UGbpkNy1CJ3s'
AGENT_ID = '1000003'
TOKEN = 'weixin123'
ENCODING_AES_KEY = 'sitCJVb3Z6dDYPORElvpQRZfVe8PyRZryVuj3Ujqn4l'

AUTHORIZED_USERS = {'LiChunYu'}
BLOCKED_COMMANDS = {'rm', 'mkfs', 'dd', 'shutdown', 'reboot', 'halt', 'poweroff', 'format', 'del', 'rd'}


# ── 企业微信签名与解密 ──────────────────────────────

def _verify_signature(msg_signature, timestamp, nonce, echostr):
    items = sorted([TOKEN, timestamp, nonce, echostr])
    sha1 = hashlib.sha1(''.join(items).encode('utf-8')).hexdigest()
    return sha1 == msg_signature


def _decrypt_echostr(encrypted):
    aes_key = base64.b64decode(ENCODING_AES_KEY + '=')
    cipher = AES.new(aes_key, AES.MODE_CBC, aes_key[:16])
    decrypted = cipher.decrypt(base64.b64decode(encrypted))
    # 去掉 PKCS7 padding
    pad = decrypted[-1]
    decrypted = decrypted[:-pad]
    # 格式：16字节随机数 + 4字节消息长度 + 消息内容 + corpid
    msg_len = struct.unpack('>I', decrypted[16:20])[0]
    return decrypted[20:20 + msg_len].decode('utf-8')


# ── 企业微信消息发送 ───────────────────────────────

def get_access_token():
    url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={CORP_ID}&corpsecret={SECRET}"
    return requests.get(url).json().get('access_token')


def send_message(user_id, content, access_token=None):
    if access_token is None:
        access_token = get_access_token()
    url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access_token}"
    requests.post(url, json={
        "touser": user_id,
        "msgtype": "text",
        "agentid": AGENT_ID,
        "text": {"content": content}
    })


def send_image(user_id, image_path, access_token=None):
    if access_token is None:
        access_token = get_access_token()
    upload_url = f"https://qyapi.weixin.qq.com/cgi-bin/media/upload?access_token={access_token}&type=image"
    with open(image_path, 'rb') as f:
        resp = requests.post(upload_url, files={'media': f})
    media_id = resp.json().get('media_id')
    if not media_id:
        send_message(user_id, "截图上传失败", access_token)
        return
    requests.post(
        f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access_token}",
        json={"touser": user_id, "msgtype": "image", "agentid": AGENT_ID, "image": {"media_id": media_id}}
    )


# ── 命令处理 ──────────────────────────────────────

def is_command_allowed(cmd):
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return False
    if not parts:
        return False
    base_cmd = os.path.basename(parts[0]).lower().replace('.exe', '')
    return base_cmd not in BLOCKED_COMMANDS


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
        if len(output) > 2000:
            output = output[:2000] + "\n...(输出已截断)"
        send_message(user_id, f"> {cmd}\n\n{output}", access_token)
    except subprocess.TimeoutExpired:
        send_message(user_id, f"命令超时（>30s）: {cmd}", access_token)
    except Exception as e:
        send_message(user_id, f"执行失败: {e}", access_token)


def handle_screenshot(user_id, access_token):
    screenshot_path = os.path.join(os.environ.get('TEMP', 'C:\\Temp'), 'weixin_screenshot.png')
    try:
        script = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
            "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
            "$bmp=New-Object System.Drawing.Bitmap($b.Width,$b.Height);"
            "$g=[System.Drawing.Graphics]::FromImage($bmp);"
            "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
            f"$bmp.Save('{screenshot_path}');"
            "$g.Dispose();$bmp.Dispose()"
        )
        result = subprocess.run(['powershell', '-Command', script], capture_output=True, timeout=15)
        if result.returncode == 0 and os.path.exists(screenshot_path):
            send_image(user_id, screenshot_path, access_token)
        else:
            send_message(user_id, "截图失败", access_token)
    except Exception as e:
        send_message(user_id, f"截图异常: {e}", access_token)
    finally:
        if os.path.exists(screenshot_path):
            os.remove(screenshot_path)


def handle_help(user_id, access_token):
    send_message(user_id, (
        "手机控制电脑 - 可用命令：\n\n"
        "/run <命令>   执行命令\n"
        "  例：/run dir C:\\Users\\PC\\Desktop\n\n"
        "/screenshot   截取当前桌面\n\n"
        "/help         显示此帮助"
    ), access_token)


# ── 路由 ──────────────────────────────────────────

@app.route('/callback', methods=['GET'])
def verify():
    """企业微信回调URL验证"""
    msg_signature = request.args.get('msg_signature', '')
    timestamp = request.args.get('timestamp', '')
    nonce = request.args.get('nonce', '')
    echostr = request.args.get('echostr', '')

    if not _verify_signature(msg_signature, timestamp, nonce, echostr):
        return make_response('invalid signature', 403)

    try:
        decrypted = _decrypt_echostr(echostr)
        return make_response(decrypted, 200)
    except Exception as e:
        return make_response(f'decrypt failed: {e}', 500)


@app.route('/callback', methods=['POST'])
def callback():
    """接收企业微信消息"""
    data = request.json or {}
    user_id = data.get('FromUserName') or data.get('from_user', '')
    msg_type = data.get('MsgType') or data.get('msgtype', '')

    if msg_type != 'text':
        return jsonify({"status": "ignored"})

    text = (data.get('Content') or data.get('text', {}).get('content', '')).strip()

    if user_id not in AUTHORIZED_USERS:
        return jsonify({"status": "unauthorized"})

    access_token = get_access_token()

    if text.startswith('/run '):
        handle_run(user_id, text[5:].strip(), access_token)
    elif text == '/screenshot':
        handle_screenshot(user_id, access_token)
    elif text == '/help':
        handle_help(user_id, access_token)

    return jsonify({"status": "success"})


if __name__ == '__main__':
    app.run(port=5000)
