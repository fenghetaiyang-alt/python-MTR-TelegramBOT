# python-MTR-TelegramBOT
###  1 安装了 Python3 的 HTTP 请求库 requests（用于发 TG 消息）：bash# 安装 Python3 和 requests 库
dnf install python3 -y  
pip3 install requests

#### 2 安装 Python 虚拟环境的核心组件（RockyLinux 默认可能未包含）
dnf install python3-pip python3-virtualenv -y  
#### 3 在 /root 下创建一个专门存放监控虚拟环境的目录（比如叫 mtr_env）
python3 -m venv /root/mtr_env  
#### 4 在当前终端激活这个虚拟环境
source /root/mtr_env/bin/activate  
💡 激活成功后，你的终端提示符最前面会出现 (mtr_env) 字样。  
#### 5 在隔离环境内安装 requests 依赖包
pip install --upgrade pip  
pip install requests  
