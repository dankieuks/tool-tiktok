#!/bin/bash

echo "================================================="
echo "       TIKTOK VIDEO MIXER - KHỞI CHẠY TOOL       "
echo "================================================="

# 1. Kiểm tra và cài đặt FFmpeg
if ! command -v ffmpeg &> /dev/null
then
    echo "[*] FFmpeg chưa được cài đặt. Tiến hành cài đặt (sẽ yêu cầu mật khẩu sudo)..."
    sudo apt update && sudo apt install -y ffmpeg
else
    echo "[+] FFmpeg đã được cài đặt."
fi

# 2. Cài đặt các thư viện Python
echo "[*] Đang thiết lập môi trường Python..."

# Thử dùng virtual environment (venv) để tránh lỗi externally-managed-environment
USE_VENV=false
if python3 -m venv venv &> /dev/null; then
    echo "[+] Đã tạo môi trường ảo (venv) thành công."
    source venv/bin/activate
    USE_VENV=true
else
    # Nếu hệ thống thiếu python3-venv, thử cài đặt venv
    echo "[*] Thử cài đặt python3-venv..."
    if sudo apt install -y python3-venv &> /dev/null && python3 -m venv venv &> /dev/null; then
        echo "[+] Đã tạo môi trường ảo (venv) thành công sau khi cài đặt gói hỗ trợ."
        source venv/bin/activate
        USE_VENV=true
    fi
fi

if [ "$USE_VENV" = true ]; then
    echo "[*] Đang cài đặt thư viện vào môi trường ảo..."
    pip install --upgrade pip
    pip install -r requirements.txt
    
    echo "[+] Đang khởi động giao diện Web UI..."
    python -m streamlit run app.py
else
    # Fallback nếu hoàn toàn không dùng được venv (cài thẳng bằng --break-system-packages)
    echo "[!] Không thể tạo môi trường ảo. Thử cài đặt trực tiếp bằng cờ --break-system-packages..."
    pip install -r requirements.txt --break-system-packages
    
    echo "[+] Đang khởi động giao diện Web UI..."
    python3 -m streamlit run app.py
fi
