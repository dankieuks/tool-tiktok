# TikTok Compilation & Music Video Generator (Tool Tải và Trộn Video TikTok)

Dự án này là một công cụ mã nguồn mở viết bằng Python giúp bạn tự động hóa quy trình:
1. Tải hàng loạt video TikTok theo danh sách URL (sử dụng `yt-dlp` để lấy video không watermark).
2. Tự động cắt ngẫu nhiên các phân đoạn ngắn (mặc định là 3 giây mỗi clip) từ kho video đã tải.
3. Trộn chúng lại với nhau theo thứ tự ngẫu nhiên để khớp thời lượng với file nhạc nền của bạn.
4. Lắp nhạc nền mới và xuất bản video nhạc hoàn chỉnh (`final_music_video.mp4`).

---

## 🛠️ Hướng dẫn cài đặt

### Bước 1: Di chuyển vào thư mục dự án
Mở terminal và di chuyển đến thư mục này:
```bash
cd /home/daaaaaa/CMS/tiktok_mixer
```

### Bước 2: Cài đặt các thư viện cần thiết
```bash
pip install -r requirements.txt
```

### Bước 3: Cài đặt FFmpeg (nếu máy bạn chưa có)
Thư viện `moviepy` phụ thuộc vào `ffmpeg` để xử lý video. Trên hệ điều hành Ubuntu/Debian/Linux, bạn có thể cài đặt bằng lệnh:
```bash
sudo apt update && sudo apt install -y ffmpeg
```

---

## 🖥️ Cách chạy Giao diện Web (Streamlit UI)

Bạn có thể chạy giao diện đồ họa Web UI cực kỳ hiện đại để dán link trực tiếp, tải nhạc và xem thử video kết quả:

```bash
streamlit run app.py
```

*Sau khi chạy lệnh, trình duyệt sẽ tự động mở trang web giao diện tại địa chỉ: `http://localhost:8501`*

### Tính năng của Giao diện:
*   **Dán Link Trực Tiếp:** Chỉ cần copy/paste danh sách link TikTok vào khung text.
*   **Tải Nhạc Lên:** Tải trực tiếp file nhạc `.mp3` từ máy tính lên trình duyệt mà không cần di chuyển file thủ công.
*   **Xem trước (Preview) & Tải về:** Xem trực tiếp video sau khi render và tải ngay về máy bằng nút download.

---

## 🚀 Cách chạy bằng Script Terminal (Nếu không dùng giao diện)

1. **Chuẩn bị danh sách link TikTok:**
   - Chạy script lần đầu tiên để nó tự động tạo ra file `urls.txt`:
     ```bash
     python auto_remix.py
     ```
   - Mở file `urls.txt` lên và dán các đường dẫn video TikTok bạn muốn cào (mỗi dòng 1 link).
   - *Lưu ý: Dòng có dấu `#` ở đầu sẽ được bỏ qua.*

2. **Chuẩn bị nhạc nền:**
   - Đặt file nhạc nền của bạn vào thư mục dự án này và đặt tên là `music.mp3`.

3. **Chạy Tool:**
   ```bash
   python auto_remix.py
   ```
# tool-tiktok
