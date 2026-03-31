import subprocess
import base64
import os
import shlex
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# 企业微信配置
CORP_ID = 'lee'
SECRET = 'W_jkwpEjBNhlOUmznS3VpUUqNGjmeu1UGbpkNy1CJ3s'
AGENT_ID = '1000003'

# 授权用户列表（只有这些用户ID可以执行命令）
AUTHORIZED_USERS = {'李纯宇'}

# 允许的命令前缀白名单（防止危险操作）
BLOCKED_COMMANDS = {'rm', 'mkfs', 'dd', 'shutdown', 'reboot', 'halt', 'poweroff', 'format'}


def get_access_token():
    url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={CORP_ID}&corpsecret={SECRET}"
    response = requests.get(url)
    return response.json().get('access_token')


def send_message(user_id, content, access_token=None):
    """发送文本消息给指定用户"""
    if access_token is None:
        access_token = get_access_token()
    url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access_token}"
    payload = {
        "touser": user_id,
        "msgtype": "text",
        "agentid": AGENT_ID,
        "text": {"content": content}
    }
    requests.post(url, json=payload)


def send_image(user_id, image_path, access_token=None):
    """上传并发送图片给指定用户"""
    if access_token is None:
        access_token = get_access_token()
    upload_url = f"https://qyapi.weixin.qq.com/cgi-bin/media/upload?access_token={access_token}&type=image"
    with open(image_path, 'rb') as f:
        resp = requests.post(upload_url, files={'media': f})
    media_id = resp.json().get('media_id')
    if not media_id:
        send_message(user_id, "截图上传失败", access_token)
        return
    url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access_token}"
    payload = {
        "touser": user_id,
        "msgtype": "image",
        "agentid": AGENT_ID,
        "image": {"media_id": media_id}
    }
    requests.post(url, json=payload)


def is_command_allowed(cmd):
    """检查命令是否在黑名单中"""
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return False
    if not parts:
        return False
    base_cmd = os.path.basename(parts[0])
    return base_cmd not in BLOCKED_COMMANDS


def handle_run(user_id, cmd, access_token):
    """执行 shell 命令并返回输出"""
    if not is_command_allowed(cmd):
        send_message(user_id, f"命令被拒绝（黑名单）: {cmd}", access_token)
        return
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        output = result.stdout or result.stderr or "(无输出)"
        # 截断过长输出
        if len(output) > 2000:
            output = output[:2000] + "\n...(输出已截断)"
        send_message(user_id, f"$ {cmd}\n\n{output}", access_token)
    except subprocess.TimeoutExpired:
        send_message(user_id, f"命令超时（>30s）: {cmd}", access_token)
    except Exception as e:
        send_message(user_id, f"执行失败: {e}", access_token)


def handle_screenshot(user_id, access_token):
    """截取当前桌面并发送"""
    screenshot_path = "/tmp/weixin_screenshot.png"
    try:
        # 优先使用 scrot，其次 gnome-screenshot，再次 import(ImageMagick)
        for cmd in [
            f"scrot {screenshot_path}",
            f"gnome-screenshot -f {screenshot_path}",
            f"import -window root {screenshot_path}",
        ]:
            result = subprocess.run(cmd, shell=True, capture_output=True, timeout=10)
            if result.returncode == 0 and os.path.exists(screenshot_path):
                break
        else:
            send_message(user_id, "截图失败：未找到可用截图工具（scrot/gnome-screenshot/imagemagick）", access_token)
            return
        send_image(user_id, screenshot_path, access_token)
    except Exception as e:
        send_message(user_id, f"截图异常: {e}", access_token)
    finally:
        if os.path.exists(screenshot_path):
            os.remove(screenshot_path)


def handle_help(user_id, access_token):
    help_text = (
        "手机控制电脑 - 可用命令：\n\n"
        "/run <命令>   执行 shell 命令\n"
        "  例：/run ls -la ~/Desktop\n\n"
        "/screenshot   截取当前桌面\n\n"
        "/help         显示此帮助"
    )
    send_message(user_id, help_text, access_token)


@app.route('/callback', methods=['POST'])
def callback():
    data = request.json
    user_id = data.get('FromUserName') or data.get('from_user', '')
    msg_type = data.get('MsgType') or data.get('msgtype', '')

    if msg_type != 'text':
        return jsonify({"status": "ignored"})

    text = (data.get('Content') or data.get('text', {}).get('content', '')).strip()

    # 鉴权：只允许授权用户操作
    if user_id not in AUTHORIZED_USERS:
        return jsonify({"status": "unauthorized"})

    access_token = get_access_token()

    if text.startswith('/run '):
        cmd = text[5:].strip()
        handle_run(user_id, cmd, access_token)
    elif text == '/screenshot':
        handle_screenshot(user_id, access_token)
    elif text == '/help':
        handle_help(user_id, access_token)
    else:
        # 保留原有省份转发逻辑
        province = ""
        if "广东" in text:
            province = "guangdong"
        elif "江苏" in text:
            province = "jiangsu"
        if province:
            forward_to_group(data.get('group_id', ''), text, province, access_token)

    return jsonify({"status": "success"})


def forward_to_group(group_id, message, province, access_token):
    group_mapping = {
        'guangdong': '广东群ID',
        'jiangsu': '江苏群ID'
    }
    target_group_id = group_mapping.get(province)
    if target_group_id:
        url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access_token}"
        payload = {
            "touser": target_group_id,
            "msgtype": "text",
            "agentid": AGENT_ID,
            "text": {"content": message}
        }
        requests.post(url, json=payload)


if __name__ == '__main__':
    app.run(port=5000)
