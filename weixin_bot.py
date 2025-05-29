from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# 这里填入你的企业微信的CorpID和Secret
CORP_ID = '你的CorpID'
SECRET = '你的Secret'

# 获取access_token的方法
def get_access_token():
    url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={CORP_ID}&secret={SECRET}"
    response = requests.get(url)
    return response.json().get('access_token')

# 接收消息和处理
@app.route('/callback', methods=['POST'])
def callback():
    data = request.json
    user_message = data.get('text', {}).get('content', '')
    group_id = data.get('group_id', '')

    # 这里根据user_message判断省份和触发的内容
    province = ""
    if "广东" in user_message:
        province = "guangdong"
    elif "江苏" in user_message:
        province = "jiangsu"

    # 根据省份转发消息
    if province:
        forward_to_group(group_id, user_message, province)

    return jsonify({"status": "success"})

# 转发消息的函数
def forward_to_group(group_id, message, province):
    access_token = get_access_token()
    # 假设不同省份群 ID 是预先定义的
    group_mapping = {
        'guangdong': '广东群ID',
        'jiangsu': '江苏群ID'
    }
    target_group_id = group_mapping.get(province)

    if target_group_id:
        url = f"https://api.weixin.qq.com/cgi-bin/message/send?access_token={access_token}"
        message_data = {
            "touser": target_group_id,
            "msgtype": "text",
            "text": {
                "content": message
            }
        }
        requests.post(url, json=message_data)

if __name__ == '__main__':
    app.run(port=5000)
