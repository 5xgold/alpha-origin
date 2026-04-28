#!/usr/bin/env python3
"""NeoData 金融数据查询客户端"""
import os, sys, json, uuid, argparse

try:
    import requests
except ImportError:
    print("需要安装 requests: pip install requests")
    sys.exit(1)

PROXY_PORT = os.getenv("AUTH_GATEWAY_PORT", "19000")
BASE_URL = f"http://localhost:{PROXY_PORT}/proxy/api"
REMOTE_URL = "https://jprx.m.qq.com/aizone/skillserver/v1/proxy/teamrouter_neodata/query"

def query_neodata(query, sub_channel="qclaw", data_type="api", request_id=None):
    payload = {
        "channel": "neodata",
        "sub_channel": sub_channel,
        "query": query,
        "request_id": request_id or uuid.uuid4().hex,
        "data_type": data_type,
        "se_params": {},
        "extra_params": {},
    }
    headers = {
        "Content-Type": "application/json",
        "Remote-URL": REMOTE_URL,
    }
    resp = requests.post(BASE_URL, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", "-q", required=True)
    parser.add_argument("--sub-channel", "-s", default="qclaw")
    parser.add_argument("--data-type", "-d", default="api", choices=["all","api","doc"])
    args = parser.parse_args()
    result = query_neodata(args.query, args.sub_channel, args.data_type)
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
