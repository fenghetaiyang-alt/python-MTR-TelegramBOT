vi /root/mtr_tg_monitor.py 

#!/root/mtr_env/bin/python3
# -*- coding: utf-8 -*-

import os
import re
import time
import subprocess
import threading
import requests

# ==================== 🛠️ 核心配置区域 ====================
TG_TOKEN = "8990602991:AAEVQPpf50WqsPgWKXM8JhyeN9uAxWd5k10"
CHAT_ID = "-5454849963"
TARGET = "8.8.4.4"

# 📊 业务触发配置
# 0.5秒发包，一共发10个包，休息5秒，合计10秒一轮
LOSS_THRESHOLD = 30.0                    # 目的地触发丢包阈值：30%（测试连通性时可改为 0.0）
INTERVAL_SEC = 10                        # 探测基础频率（秒）
REMIND_INTERVAL_SEC = 20 * 60            # 持续故障时，每 20 分钟重新提醒一次
CONSECUTIVE_UNKNOWN_LIMIT = 3            # 连续 3 跳 ??? 则判定为中间断路故障
# ========================================================

LOG_DIR = "/var/log/mtr_flash"
os.makedirs(LOG_DIR, exist_ok=True)

LINK_STATES = {}
state_lock = threading.Lock()

LINKS = {
    "BRO-HK": "10.49.251.3",
    "XTY-HK": "10.49.251.4",
    "MKN-HK": "10.49.251.5"
}

for name in LINKS.keys():
    LINK_STATES[name] = {
        "status": "OK",
        "fail_count": 0,
        "ok_count": 0,
        "last_alert_time": 0
    }

def send_tg_msg(html_text):
    """
    具备断网容灾、智能路由的反向代理发送函数
    """
    clean_token = TG_TOKEN.strip()
    if not clean_token.startswith("bot"):
        clean_token = f"bot{clean_token}"
        
    payload = {"chat_id": CHAT_ID, "parse_mode": "HTML", "text": html_text}
    
    # 🩹 智能双通道：如果原生接口被墙或超时，自动无缝切换到高可用代理接口
    urls = [
        f"https://api.telegram.org/{clean_token}/sendMessage",
        f"https://tgproxy.cc{clean_token}/sendMessage",          # 备用容灾通道1
        f"https://telegram-proxy.org{clean_token}/sendMessage" # 备用容灾通道2
    ]
    
    for url in urls:
        try:
            response = requests.post(url, json=payload, timeout=6)
            if response.status_code == 200 and response.json().get("ok"):
                return  # 发送成功，直接退出
        except:
            continue  # 失败了就默默尝试下一个备用通道

def monitor_link(isp_name, src_ip):
    global LINK_STATES
    
    cmd = ["mtr", "-n", "-c", "10", "-i", "0.5", "-r", "-a", src_ip, TARGET]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
        if result.returncode != 0: return
        raw_output = result.stdout
    except: 
        return

    lines = raw_output.strip().split("\n")
    hops_data = []
    
    for line in lines:
        if "Start:" in line or "HOST" in line or not line.strip(): continue
        parts = line.split()
        if len(parts) < 3: continue
        
        hop_match = re.search(r"\d+", parts[0])
        if not hop_match: continue
        hop = int(hop_match.group())
        
        if "???" in parts or "???" in line:
            hops_data.append({
                "hop": hop, "ip": "???", "loss": 100.0,
                "last": "--", "avg": "--", "max": "--"
            })
            continue
            
        ip = parts[1]
        try:
            loss_str = parts[2].replace("%", "")
            loss = float(loss_str) if loss_str and loss_str != "-" else 0.0
        except: 
            loss = 0.0
            
        # 🟢 【健壮性加固】：防御式读取延迟，如果列数不够则安全降级，防止数组越界奔溃
        last_lat, avg_lat, max_lat = "0.0", "0.0", "0.0"
        try:
            if len(parts) >= 8:
                last_lat = parts[4]
                avg_lat = parts[5]
                max_lat = parts[7]
            elif len(parts) >= 6:
                last_lat = parts[-4]
                avg_lat = parts[-3]
                max_lat = parts[-1]
        except: 
            pass

        hops_data.append({
            "hop": hop, "ip": ip, "loss": loss,
            "last": last_lat, "avg": avg_lat, "max": max_lat
        })

    if not hops_data: return

    # 计算连续 ??? 数量
    max_consecutive_unknown = 0
    current_consecutive = 0
    for node in hops_data:
        if node["ip"] == "???":
            current_consecutive += 1
            max_consecutive_unknown = max(max_consecutive_unknown, current_consecutive)
        else:
            current_consecutive = 0  

    dest_node = hops_data[-1]
    dest_loss = dest_node["loss"]

    is_fault = (dest_loss >= LOSS_THRESHOLD) or (max_consecutive_unknown >= CONSECUTIVE_UNKNOWN_LIMIT)

    # 组装对齐状态墙
    state_wall = ""
    for node in hops_data:
        pad_hop = f"{node['hop']:02d}"
        if node["ip"] == "???":
            state_wall += f"{pad_hop} | [骨干网隐身节点] | 100%  |    -- /    -- /    --\n"
        else:
            try: loss_int = int(node["loss"])
            except: loss_int = 0
            state_wall += f"{pad_hop} | {node['ip']:<15} | {loss_int:<4}% | {node['last']:>5} / {node['avg']:>5} / {node['max']:>5}\n"

    now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    now_ts = time.time()
    
    with state_lock:
        state = LINK_STATES[isp_name]
        
        if is_fault:
            state["ok_count"] = 0        
            state["fail_count"] += 1     
            
            should_send_alert = False
            if state["fail_count"] <= 2:
                should_send_alert = True
            elif now_ts - state["last_alert_time"] >= REMIND_INTERVAL_SEC:
                should_send_alert = True
                
            if should_send_alert:
                state["status"] = "FAIL"
                state["last_alert_time"] = now_ts
                
                reason = f"目的地丢包过高 ({dest_loss}%)" if dest_loss >= LOSS_THRESHOLD else f"检测到连续 {max_consecutive_unknown} 跳断路黑洞"
                
                html_msg = (
                    f"🚨 <b>[链路故障报警]</b>\n"
                    f"🕒 <b>探测时间</b>: <code>{now_str}</code>\n"
                    f"🌐 <b>受影响链路</b>: <b>{isp_name}</b> ({src_ip})\n"
                    f"🎯 <b>拨测目的地</b>: <code>{TARGET}</code>\n"
                    f"⚠️ <b>可能故障原因</b>: <b>{reason}</b> (故障计数: #{state['fail_count']})\n\n"
                    f"📋 <b>全链路状态:</b>\n"
                    f"<pre><code>"
                    f"Hop| Node_IP         | Loss  |  Last /  Avg  /  Max  (ms)\n"
                    f"---|-----------------|-------|---------------------------\n"
                    f"{state_wall}"
                    f"</code></pre>"
                )
                send_tg_msg(html_msg)
                save_to_log(isp_name, html_msg)
                
        else:
            state["ok_count"] += 1
            if state["status"] == "FAIL" and state["ok_count"] >= 3:
                state["status"] = "OK"
                state["fail_count"] = 0  
                state["last_alert_time"] = 0
                
                msg_lines = [
                    f"🟢 <b>[链路恢复通知] 状态已恢复正常</b>",
                    f"🕒 <b>恢复时间</b>: <code>{now_str}</code>",
                    f"🌐 <b>正常链路</b>: <b>{isp_name}</b> ({src_ip})",
                    f"🎯 <b>拨测目的地</b>: <code>{TARGET}</code>",
                    # 💡 如果不想显示下面这行判定依据，随时在行首加 # 注释即可，绝对不崩：
                    # f"✅ <b>判定依据</b>: 链路已连续 3 次探测恢复（目的地丢包 {dest_loss}%，且无连续断路）",
                    f"",
                    f"📋 <b>当前全链路状态墙:</b>",
                    f"<pre><code>",
                    f"Hop| Node_IP         | Loss  |  Last /  Avg  /  Max  (ms)",
                    f"---|-----------------|-------|---------------------------",
                    f"{state_wall.strip()}",
                    f"</code></pre>"
                ]
                html_msg = "\n".join(msg_lines)

                send_tg_msg(html_msg)
                save_to_log(isp_name, html_msg)

def save_to_log(isp_name, html_msg):
    clean_name = isp_name.replace("-", "_").lower() + ".log"
    log_file = os.path.join(LOG_DIR, clean_name)
    try:
        clean_log_text = re.sub(r"<[^>]+>", "", html_msg)
        with open(log_file, "a", encoding="utf-8") as f: 
            f.write(f"{'='*45}\n{clean_log_text}\n\n")
    except: pass

def main():
    while True:
        start_time = time.time()
        threads = []
        for isp_name, src_ip in LINKS.items():
            t = threading.Thread(target=monitor_link, args=(isp_name, src_ip))
            threads.append(t)
            t.start()
        for t in threads: t.join()
        sleep_time = max(0, INTERVAL_SEC - (time.time() - start_time))
        time.sleep(sleep_time)

if __name__ == "__main__": 
    main()
