import os
import glob
import random
import gc
import re
import subprocess
import shutil
import time
import streamlit as st
import PIL.Image

# Sửa lỗi: module 'PIL.Image' has no attribute 'ANTIALIAS' trong Pillow 10+
if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS

from yt_dlp import YoutubeDL

# ==========================================
# LIVE LOG PANEL - HIỂN THỊ LOG TRỰC TIẾP
# ==========================================
class LiveLog:
    """Quản lý bảng log trực tiếp trên giao diện Streamlit (throttle render tối đa 1 lần/giây)."""
    def __init__(self, log_container, max_lines=200):
        self.log_container = log_container
        self.lines = []
        self.max_lines = max_lines
        self._last_render = 0
    
    def add(self, msg, force=False):
        timestamp = time.strftime("%H:%M:%S")
        self.lines.append(f"[{timestamp}] {msg}")
        if len(self.lines) > self.max_lines:
            self.lines = self.lines[-self.max_lines:]
        now = time.time()
        if force or (now - self._last_render) >= 1.0:
            self._render()
            self._last_render = now
    
    def flush(self):
        """Buộc render ngay lập tức (gọi khi kết thúc batch hoặc lỗi)."""
        self._render()
        self._last_render = time.time()
    
    def _render(self):
        log_text = "\n".join(self.lines)
        self.log_container.markdown(f"""
<div style="background:#0a0e17; border:1px solid rgba(255,255,255,0.08); border-radius:10px; padding:12px 16px; max-height:300px; overflow-y:auto; font-family:'JetBrains Mono',monospace; font-size:0.78rem; color:#94a3b8; line-height:1.6;">
<pre style="margin:0; white-space:pre-wrap; word-break:break-all;">{log_text}</pre>
</div>
""", unsafe_allow_html=True)

# ==========================================
# RENDER VIDEO BẰNG FFMPEG THUẦN (TIẾT KIỆM RAM)
# ==========================================
def ffmpeg_concat_videos(input_files, output_path, normalize_size=(1080, 1920), 
                         keep_audio=True, music_path=None, live_log=None):
    """Ghép video bằng ffmpeg thuần - không load frame vào RAM Python.
    
    Args:
        input_files: Danh sách đường dẫn file video đầu vào.
        output_path: Đường dẫn file video đầu ra.
        normalize_size: Tuple (width, height) chuẩn hóa.
        keep_audio: Giữ âm thanh gốc hay không.
        music_path: Đường dẫn file nhạc nền (nếu không giữ audio gốc).
        live_log: LiveLog instance để ghi log.
    """
    w, h = normalize_size
    
    # Tạo filter_complex cho từng input
    filter_parts = []
    concat_inputs = []
    
    for i, fpath in enumerate(input_files):
        # Scale + pad để fit đúng kích thước, giữ tỷ lệ
        scale_filter = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
        filter_parts.append(f"[{i}:v]{scale_filter}[v{i}]")
        
        if keep_audio:
            concat_inputs.append(f"[v{i}][{i}:a]")
        else:
            concat_inputs.append(f"[v{i}]")
    
    n = len(input_files)
    
    if keep_audio:
        concat_filter = f"{''.join(concat_inputs)}concat=n={n}:v=1:a=1[outv][outa]"
    else:
        concat_filter = f"{''.join(concat_inputs)}concat=n={n}:v=1:a=0[outv]"
    
    full_filter = ";".join(filter_parts) + ";" + concat_filter
    
    # Xây dựng lệnh ffmpeg
    cmd = ["ffmpeg", "-y"]
    for fpath in input_files:
        cmd.extend(["-i", fpath])
    
    if not keep_audio and music_path and os.path.exists(music_path):
        cmd.extend(["-i", music_path])  # Input cuối cùng là nhạc nền
    
    cmd.extend(["-filter_complex", full_filter])
    cmd.extend(["-map", "[outv]"])
    
    if keep_audio:
        cmd.extend(["-map", "[outa]"])
    elif music_path and os.path.exists(music_path):
        music_idx = len(input_files)
        cmd.extend(["-map", f"{music_idx}:a", "-shortest"])
    
    cmd.extend([
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-threads", "2",
        "-movflags", "+faststart",
        "-r", "24",
        output_path
    ])
    
    if live_log:
        live_log.add(f"🎞️ Đang render bằng FFmpeg ({n} clip)...")
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=600
        )
        if result.returncode != 0:
            # Nếu lỗi concat audio (một số video không có audio track), thử lại không audio
            if "does not match" in result.stderr or "Stream specifier" in result.stderr:
                if live_log:
                    live_log.add("⚠️ Audio không đồng nhất, thử render lại không audio...")
                return ffmpeg_concat_videos(
                    input_files, output_path, normalize_size,
                    keep_audio=False, music_path=None, live_log=live_log
                )
            if live_log:
                live_log.add(f"⚠️ FFmpeg lỗi: {result.stderr[-200:]}")
            return False
        return True
    except subprocess.TimeoutExpired:
        if live_log:
            live_log.add("⚠️ FFmpeg render quá lâu (timeout 10 phút), bỏ qua.")
        return False
    except Exception as e:
        if live_log:
            live_log.add(f"⚠️ FFmpeg exception: {str(e)[:100]}")
        return False

def ffmpeg_get_duration(filepath):
    """Lấy thời lượng video bằng ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", filepath],
            capture_output=True, text=True, timeout=10
        )
        return float(result.stdout.strip())
    except:
        return 0.0

def ffmpeg_random_mix_videos(input_files, output_path, music_path, segment_duration=3.0,
                              normalize_size=(1080, 1920), live_log=None):
    """Trộn ngẫu nhiên các đoạn video ngắn khớp với nhạc nền bằng ffmpeg thuần.
    
    Cắt các đoạn ngẫu nhiên từ video nguồn, ghép lại thành video dài khớp nhạc nền.
    """
    w, h = normalize_size
    
    # Lấy thời lượng nhạc nền
    music_dur = ffmpeg_get_duration(music_path)
    if music_dur <= 0:
        if live_log:
            live_log.add("⚠️ Không đọc được thời lượng nhạc nền.")
        return False
    
    if live_log:
        live_log.add(f"🎵 Nhạc nền: {music_dur:.1f}s • Segment: {segment_duration}s")
    
    # Lấy thời lượng từng video
    video_durations = {}
    usable_files = []
    for fpath in input_files:
        dur = ffmpeg_get_duration(fpath)
        if dur > segment_duration:
            video_durations[fpath] = dur
            usable_files.append(fpath)
    
    if not usable_files:
        if live_log:
            live_log.add("⚠️ Không có video nào đủ dài để cắt segment.")
        return False
    
    # Tạo danh sách các đoạn cắt ngẫu nhiên
    temp_segments = []
    current_dur = 0.0
    seg_idx = 0
    
    try:
        while current_dur < music_dur:
            src_file = random.choice(usable_files)
            src_dur = video_durations[src_file]
            start_t = random.uniform(0, src_dur - segment_duration)
            
            # Cắt segment bằng ffmpeg
            seg_path = os.path.join(os.path.dirname(output_path), f"_seg_{seg_idx}.mp4")
            
            vf_filter = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
            
            cut_cmd = [
                "ffmpeg", "-y",
                "-ss", f"{start_t:.2f}",
                "-i", src_file,
                "-t", f"{segment_duration:.2f}",
                "-vf", vf_filter,
                "-an",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-r", "24",
                seg_path
            ]
            
            result = subprocess.run(cut_cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and os.path.exists(seg_path):
                temp_segments.append(seg_path)
                current_dur += segment_duration
                seg_idx += 1
            else:
                continue
        
        if not temp_segments:
            if live_log:
                live_log.add("⚠️ Không cắt được segment nào.")
            return False
        
        if live_log:
            live_log.add(f"✂️ Đã cắt {len(temp_segments)} segment ngẫu nhiên")
        
        # Tạo file danh sách để concat
        concat_list_path = os.path.join(os.path.dirname(output_path), "_concat_list.txt")
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for seg in temp_segments:
                f.write(f"file '{seg}'\n")
        
        # Ghép tất cả segment + nhạc nền
        concat_cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_list_path,
            "-i", music_path,
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            "-map", "0:v", "-map", "1:a",
            "-shortest",
            "-movflags", "+faststart",
            output_path
        ]
        
        if live_log:
            live_log.add(f"🎞️ Đang ghép {len(temp_segments)} segment + nhạc nền...")
        
        result = subprocess.run(concat_cmd, capture_output=True, text=True, timeout=600)
        
        return result.returncode == 0 and os.path.exists(output_path)
    
    except Exception as e:
        if live_log:
            live_log.add(f"⚠️ Lỗi trộn ngẫu nhiên: {str(e)[:100]}")
        return False
    finally:
        # Dọn dẹp file tạm segment
        for seg in temp_segments:
            try:
                os.remove(seg)
            except:
                pass
        try:
            os.remove(concat_list_path)
        except:
            pass

# ==========================================
# CẤU HÌNH PWA & WEB ICON
# ==========================================
def patch_streamlit_pwa():
    import shutil
    try:
        # 1. Tìm thư mục static của streamlit đang cài trong máy
        st_dir = os.path.dirname(st.__file__)
        static_dir = os.path.join(st_dir, "static")
        
        # 2. Copy icon ứng dụng sang thư mục static
        project_dir = os.path.dirname(os.path.abspath(__file__))
        src_icon = os.path.join(project_dir, "app_icon.png")
        dest_icon = os.path.join(static_dir, "app_icon.png")
        
        if os.path.exists(src_icon):
            shutil.copy(src_icon, dest_icon)
            
        # 3. Tạo file manifest.json cho Android
        manifest_content = """{
  "name": "TikTok Video Mixer",
  "short_name": "TiktokMixer",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#0e1117",
  "theme_color": "#FF007F",
  "icons": [
    {
      "src": "app_icon.png",
      "sizes": "512x512",
      "type": "image/png"
    }
  ]
}"""
        with open(os.path.join(static_dir, "manifest.json"), "w", encoding="utf-8") as f:
            f.write(manifest_content)

        # 4. Chèn meta tags vào index.html
        index_path = os.path.join(static_dir, "index.html")
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                html = f.read()
            
            meta_tags = '\n  <link rel="apple-touch-icon" href="app_icon.png">\n  <meta name="apple-mobile-web-app-capable" content="yes">\n  <link rel="manifest" href="manifest.json">'
            
            if "manifest.json" not in html:
                html = html.replace("<head>", f"<head>{meta_tags}")
                with open(index_path, "w", encoding="utf-8") as f:
                    f.write(html)
    except Exception as e:
        print(f"PWA patching ignored: {e}")

# Thực hiện patch khi khởi chạy ứng dụng
patch_streamlit_pwa()

# ==========================================
# CẤU HÌNH
# ==========================================
# Cấu hình thư mục tạm nội bộ chạy tool
DOWNLOAD_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_dl"))
OUTPUT_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_out"))
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "final_music_video.mp4")

def check_and_cleanup_disk_space(required_mb=500, live_log=None, current_batch_num=None):
    """Kiểm tra dung lượng đĩa trống, tự động xóa cache/tệp cũ của các batch trước nếu dung lượng dưới mức yêu cầu."""
    total, used, free = shutil.disk_usage(os.path.dirname(os.path.abspath(__file__)))
    free_mb = free / (1024 * 1024)
    
    if free_mb < required_mb:
        msg = f"⚠️ Cảnh báo: Dung lượng đĩa thấp ({free_mb:.1f} MB < {required_mb} MB). Đang tự động dọn dẹp cache..."
        if live_log:
            live_log.add(msg)
        else:
            print(msg)
            
        # 1. Chỉ dọn dẹp DOWNLOAD_DIR nếu không có batch nào đang chạy (ví dụ: lúc khởi chạy)
        if current_batch_num is None:
            if os.path.exists(DOWNLOAD_DIR):
                for f in glob.glob(os.path.join(DOWNLOAD_DIR, "*")):
                    try:
                        if os.path.isdir(f):
                            shutil.rmtree(f)
                        else:
                            os.remove(f)
                    except:
                        pass
        
        # 2. Dọn dẹp OUTPUT_DIR: xóa các batch cũ để nhường chỗ cho batch mới nếu cần thiết
        if os.path.exists(OUTPUT_DIR):
            for f in glob.glob(os.path.join(OUTPUT_DIR, "*")):
                filename = os.path.basename(f)
                
                # Không xóa nhạc nền temp_music.mp3
                if filename == "temp_music.mp3":
                    continue
                
                # Nếu là file batch mp4
                if filename.startswith("batch_") and filename.endswith(".mp4"):
                    match = re.search(r'batch_(\d+)', filename)
                    if match:
                        f_batch_num = int(match.group(1))
                        if current_batch_num is not None and f_batch_num >= current_batch_num:
                            continue # Không xóa file batch hiện tại đang tạo hoặc sắp tạo
                    
                    try:
                        os.remove(f)
                        if live_log:
                            live_log.add(f"  🗑️ Đã xóa batch cũ: {filename} để giải phóng dung lượng.")
                    except:
                        pass
                else:
                    # Xóa các file rác khác (file tạm của ffmpeg/moviepy còn sót lại)
                    try:
                        if os.path.isdir(f):
                            shutil.rmtree(f)
                        else:
                            os.remove(f)
                    except:
                        pass
                        
        gc.collect()
        
        # Kiểm tra lại dung lượng
        total, used, free = shutil.disk_usage(os.path.dirname(os.path.abspath(__file__)))
        new_free_mb = free / (1024 * 1024)
        msg_done = f"✅ Đã dọn dẹp xong. Dung lượng trống hiện tại: {new_free_mb:.1f} MB"
        if live_log:
            live_log.add(msg_done)
        else:
            print(msg_done)

def init_temp_dirs():
    """Khởi tạo và làm sạch các thư mục tạm lúc khởi chạy."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    # Dọn dẹp hết file cũ khi bắt đầu chạy app
    check_and_cleanup_disk_space(required_mb=999999)

# Khởi tạo thư mục tạm và xóa rác cũ ngay khi khởi động
init_temp_dirs()

# ==========================================
# GIAO DIỆN
# ==========================================
project_icon = "app_icon.png" if os.path.exists("app_icon.png") else "🎬"
st.set_page_config(
    page_title="TikTok Video Mixer",
    page_icon=project_icon,
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

* { font-family: 'Inter', sans-serif; }

.main-header {
    text-align: center;
    padding: 20px 0 10px 0;
}
.main-header h1 {
    font-size: 2.4rem;
    font-weight: 800;
    background: linear-gradient(135deg, #FF007F, #7F00FF, #00D4FF);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 4px;
}
.main-header p {
    color: #94a3b8;
    font-size: 1rem;
}

.stButton>button {
    background: linear-gradient(135deg, #FF007F, #7F00FF) !important;
    color: white !important;
    border: none !important;
    padding: 14px 28px !important;
    font-weight: 700 !important;
    font-size: 1.05rem !important;
    border-radius: 12px !important;
    transition: all 0.3s ease !important;
    width: 100% !important;
    letter-spacing: 0.5px;
}
.stButton>button:hover {
    opacity: 0.92 !important;
    transform: translateY(-2px) !important;
    box-shadow: 0 8px 25px rgba(255, 0, 127, 0.35) !important;
}

.card {
    background: linear-gradient(145deg, #1e293b, #162032);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 12px;
    padding: 16px 20px;
    margin-bottom: 16px;
    box-shadow: 0 4px 15px rgba(0,0,0,0.2);
}
.card h3 {
    margin-top: 0;
    margin-bottom: 8px;
    font-size: 1.1rem;
    color: #e2e8f0;
}

.section-header {
    background: linear-gradient(145deg, #1e293b, #162032);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 12px;
    padding: 12px 20px;
    margin-bottom: 16px;
    box-shadow: 0 4px 15px rgba(0,0,0,0.2);
}
.section-header h3 {
    margin: 0;
    font-size: 1.1rem;
    color: #e2e8f0;
}

.status-box {
    padding: 16px 20px;
    border-radius: 12px;
    background: linear-gradient(145deg, #1e293b, #162032);
    border-left: 4px solid #FF007F;
    margin-bottom: 12px;
    font-size: 0.95rem;
}
.status-box b { color: #f1f5f9; }
.status-box .url { color: #60a5fa; word-break: break-all; font-size: 0.85rem; }

.badge {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 0.75rem;
    font-weight: 600;
    margin-right: 6px;
}
.badge-pink { background: rgba(255,0,127,0.15); color: #FF007F; }
.badge-purple { background: rgba(127,0,255,0.15); color: #a78bfa; }
.badge-blue { background: rgba(0,212,255,0.15); color: #00D4FF; }

.feature-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    margin-top: 12px;
}
.feature-item {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 10px;
    padding: 12px 14px;
    font-size: 0.85rem;
    color: #cbd5e1;
}
.feature-item .icon { font-size: 1.2rem; margin-bottom: 4px; }

.stTextArea textarea {
    background-color: #0f172a !important;
    border: 1px solid rgba(255,255,255,0.1) !important;
    border-radius: 12px !important;
    color: #e2e8f0 !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.9rem !important;
}
.stTextArea textarea:focus {
    border-color: #FF007F !important;
    box-shadow: 0 0 0 2px rgba(255,0,127,0.2) !important;
}

.stSelectbox > div > div {
    background-color: #0f172a !important;
    border: 1px solid rgba(255,255,255,0.1) !important;
    border-radius: 10px !important;
}

.stProgress > div > div > div > div {
    background: linear-gradient(90deg, #FF007F, #7F00FF, #00D4FF) !important;
    border-radius: 8px;
}

div[data-testid="stDownloadButton"] button {
    background: linear-gradient(135deg, #00c853, #00bfa5) !important;
    font-weight: 700 !important;
    border-radius: 12px !important;
    padding: 14px !important;
    font-size: 1rem !important;
}
div[data-testid="stDownloadButton"] button:hover {
    box-shadow: 0 8px 25px rgba(0,200,83,0.3) !important;
    transform: translateY(-2px) !important;
}
div[data-testid="stVideo"], video {
    max-width: 150px !important;
    max-height: 300px !important;
    margin: 0 auto !important;
    border-radius: 12px !important;
    overflow: hidden !important;
    display: block !important;
}
</style>
""", unsafe_allow_html=True)

# Header
st.markdown("""
<div class="main-header">
    <h1>🎬 TikTok Video Mixer</h1>
    <p>Tải hàng loạt • Trộn tự động • Ghép nhạc • Xuất video</p>
    <div style="margin-top:10px;">
        <span class="badge badge-pink">yt-dlp</span>
        <span class="badge badge-purple">moviepy</span>
        <span class="badge badge-blue">ffmpeg</span>
    </div>
</div>
""", unsafe_allow_html=True)

st.markdown("---")

# ==========================================
# LAYOUT 2 CỘT
# ==========================================
col_left, col_right = st.columns([1, 1], gap="large")

with col_left:
    st.markdown("""
    <div class="section-header">
        <h3>📋 Chọn nguồn đầu vào</h3>
    </div>
    """, unsafe_allow_html=True)

    input_mode = st.radio(
        "Hình thức đầu vào:",
        ["🔗 Dán danh sách link video", "📺 Tải từ Kênh TikTok"],
        horizontal=True,
        label_visibility="collapsed"
    )

    valid_urls = []
    num_channel_videos = 10
    
    if input_mode == "🔗 Dán danh sách link video":
        urls_input = st.text_area(
            "Dán link TikTok vào đây (mỗi link 1 dòng):",
            placeholder="https://www.tiktok.com/@user1/video/111111\nhttps://www.tiktok.com/@user2/video/222222\nhttps://www.tiktok.com/@user3/video/333333",
            height=180,
            label_visibility="collapsed"
        )
        raw_urls = [u.strip() for u in urls_input.split("\n") if u.strip() and not u.strip().startswith("#")]
        for u in raw_urls:
            # Tự động trích xuất link (bỏ qua các văn bản thừa khi copy từ điện thoại)
            match = re.search(r'(https?://[^\s]+)', u)
            if match:
                clean_url = match.group(1)
                if "?" in clean_url:
                    clean_url = clean_url.split("?")[0]
                valid_urls.append(clean_url)
            
        if valid_urls:
            st.caption(f"✅ Đã nhận **{len(valid_urls)}** link hợp lệ")
        else:
            st.caption("⏳ Chưa có link nào được nhập")
    else:
        channel_url = st.text_input(
            "Nhập link kênh TikTok:",
            placeholder="https://www.tiktok.com/@username"
        )
        num_channel_videos = st.slider(
            "Số lượng video mới nhất cần tải từ kênh:",
            min_value=1, max_value=20, value=10, step=1
        )
        cleaned_channel_url = channel_url.strip()
        match = re.search(r'(https?://[^\s]+)', cleaned_channel_url)
        if match:
            cleaned_channel_url = match.group(1)
            if "?" in cleaned_channel_url:
                cleaned_channel_url = cleaned_channel_url.split("?")[0]
            valid_urls = [cleaned_channel_url]
            st.caption(f"✅ Đã nhận kênh: **{cleaned_channel_url}** (Sẽ lấy **{num_channel_videos}** bài mới nhất)")
        else:
            st.caption("⏳ Chưa nhập link kênh")

    st.markdown("")

    # Cấu hình
    st.markdown("""
    <div class="section-header">
        <h3>⚙️ Cấu hình xử lý</h3>
    </div>
    """, unsafe_allow_html=True)

    mix_mode = st.selectbox(
        "Chế độ ghép video:",
        ["🔗 Ghép nối tiếp nguyên bản", "🎲 Trộn ngẫu nhiên theo nhạc"],
        index=0
    )

    keep_audio = st.toggle("🔊 Giữ âm thanh gốc của video TikTok", value=True)

    music_file = None
    segment_duration = 3.0

    if not keep_audio:
        st.info("🎵 Hãy tải lên file nhạc nền để ghép thay thế âm thanh gốc.")
        music_file = st.file_uploader("Chọn file nhạc nền:", type=["mp3", "wav", "m4a"])

    if mix_mode == "🎲 Trộn ngẫu nhiên theo nhạc":
        segment_duration = st.slider(
            "Độ dài mỗi phân đoạn (giây):",
            min_value=1.0, max_value=10.0, value=3.0, step=0.5
        )

    # Phối lại âm thanh
    st.markdown("")
    st.markdown("""
    <div class="section-header">
        <h3>🎛️ Phối lại âm thanh (né Content ID)</h3>
    </div>
    """, unsafe_allow_html=True)
    
    audio_remix = st.toggle("🎛️ Bật phối lại âm thanh", value=False)
    
    audio_pitch = 0
    audio_speed = 1.0
    audio_bass = 0
    audio_reverb = False
    audio_eq_random = False
    audio_compressor = False
    audio_stereo = False
    
    if audio_remix:
        remix_level = st.radio(
            "Mức độ phối lại:",
            ["🟢 Nhẹ (khó nhận ra)", "🟡 Vừa (an toàn)", "🔴 Mạnh (né YouTube Content ID)"],
            index=1,
            horizontal=True
        )
        
        if remix_level == "🟢 Nhẹ (khó nhận ra)":
            audio_pitch = 1
            audio_speed = 1.02
            audio_bass = 2
            audio_reverb = False
            audio_eq_random = False
            audio_compressor = False
            audio_stereo = False
        elif remix_level == "🟡 Vừa (an toàn)":
            audio_pitch = 2
            audio_speed = 1.04
            audio_bass = 4
            audio_reverb = True
            audio_eq_random = True
            audio_compressor = False
            audio_stereo = False
        else:  # Mạnh
            audio_pitch = 3
            audio_speed = 1.06
            audio_bass = 5
            audio_reverb = True
            audio_eq_random = True
            audio_compressor = True
            audio_stereo = True
        
        with st.expander("⚙️ Tuỳ chỉnh chi tiết", expanded=False):
            audio_pitch = st.slider(
                "🎵 Cao độ (semitone):",
                min_value=-5, max_value=5, value=audio_pitch, step=1,
                help="Dương = cao hơn, Âm = trầm hơn."
            )
            audio_speed = st.slider(
                "⏩ Tốc độ:",
                min_value=0.90, max_value=1.15, value=audio_speed, step=0.01,
                help="1.04-1.06 hiệu quả nhất."
            )
            audio_bass = st.slider(
                "🔊 Bass boost (dB):",
                min_value=0, max_value=10, value=audio_bass, step=1
            )
            audio_reverb = st.toggle("🏛️ Reverb (vang)", value=audio_reverb)
            audio_eq_random = st.toggle("🎚️ Random EQ (biến dạng tần số)", value=audio_eq_random,
                help="Ngẫu nhiên boost/cut các dải tần — rất hiệu quả phá fingerprint.")
            audio_compressor = st.toggle("🔧 Nén âm thanh (Compressor)", value=audio_compressor,
                help="Thay đổi dynamic range, phá vỡ dấu vân tay âm thanh.")
            audio_stereo = st.toggle("🎧 Mở rộng stereo", value=audio_stereo,
                help="Thay đổi pha stereo, rất khó bị nhận diện lại.")


    st.markdown("")
    start_btn = st.button("🚀 BẮT ĐẦU XỬ LÝ", use_container_width=True)

    # Tính năng
    st.markdown("""
    <div class="card">
        <h3>⚡ Tính năng tích hợp</h3>
        <div class="feature-grid">
            <div class="feature-item"><div class="icon">🚀</div>Tối ưu hóa FFmpeg siêu nhẹ</div>
            <div class="feature-item"><div class="icon">🔇</div>Tách/Giữ âm thanh gốc</div>
            <div class="feature-item"><div class="icon">🔀</div>Trộn thứ tự ngẫu nhiên</div>
            <div class="feature-item"><div class="icon">🔁</div>Tự động loop nhạc nền</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

with col_right:
    st.markdown("""
    <div class="section-header">
        <h3>🖥️ Trạng thái & Kết quả</h3>
    </div>
    """, unsafe_allow_html=True)

    status_area = st.empty()
    progress_bar = st.progress(0)
    video_preview = st.empty()
    download_area = st.empty()
    
    # Bảng log trực tiếp
    st.markdown("")
    st.markdown("""
    <div class="section-header">
        <h3>📋 Nhật ký xử lý (Live Log)</h3>
    </div>
    """, unsafe_allow_html=True)
    live_log_area = st.empty()
    log_area = st.empty()

    if not start_btn:
        status_area.markdown("""
        <div class="status-box">
            <b>⏳ Đang chờ lệnh...</b><br>
            Nhập link TikTok vào cột bên trái và nhấn <b>"BẮT ĐẦU XỬ LÝ"</b> để bắt đầu.
        </div>
        """, unsafe_allow_html=True)

# ==========================================
# XỬ LÝ CHÍNH
# ==========================================
if start_btn:
    error_logs = []
    live_log = LiveLog(live_log_area)
    BATCH_SIZE = 10
    
    if not valid_urls:
        status_area.error("⚠️ Vui lòng nhập ít nhất 1 link TikTok hợp lệ.")
    elif not keep_audio and music_file is None and mix_mode == "🎲 Trộn ngẫu nhiên theo nhạc":
        status_area.error("⚠️ Bạn chọn chế độ trộn theo nhạc nhưng chưa tải lên file nhạc nền.")
    else:
        # Chia batch
        batches = [valid_urls[i:i+BATCH_SIZE] for i in range(0, len(valid_urls), BATCH_SIZE)]
        total_batches = len(batches)
        completed_videos = []  # Lưu danh sách video đã hoàn thành
        
        live_log.add(f"🚀 Bắt đầu xử lý {len(valid_urls)} link → {total_batches} batch (tối đa {BATCH_SIZE} link/batch)")
        
        # Lưu nhạc nền tạm nếu có upload (chỉ lưu 1 lần)
        music_path = None
        if not keep_audio and music_file:
            music_path = os.path.join(OUTPUT_DIR, "temp_music.mp3")
            with open(music_path, "wb") as f:
                f.write(music_file.getbuffer())
            live_log.add(f"🎵 Đã lưu nhạc nền: {music_file.name}")
        
        for batch_idx, batch_urls in enumerate(batches):
            batch_num = batch_idx + 1
            batch_output = os.path.join(OUTPUT_DIR, f"batch_{batch_num}.mp4")
            
            live_log.add(f"")
            live_log.add(f"{'='*40}")
            live_log.add(f"📦 BATCH {batch_num}/{total_batches} ({len(batch_urls)} link)")
            live_log.add(f"{'='*40}")
            
            status_area.markdown(f"""
            <div class="status-box">
                <b>📦 Batch {batch_num}/{total_batches} — Đang xử lý {len(batch_urls)} link...</b>
            </div>
            """, unsafe_allow_html=True)
            
            try:
                # Kiểm tra dung lượng đĩa trống trước khi chạy batch mới
                check_and_cleanup_disk_space(required_mb=500, live_log=live_log, current_batch_num=batch_num)
                
                # Dọn dẹp thư mục tạm để chuẩn bị tải batch mới
                for f in glob.glob(os.path.join(DOWNLOAD_DIR, "*")):
                    try:
                        if os.path.isdir(f):
                            shutil.rmtree(f)
                        else:
                            os.remove(f)
                    except:
                        pass
                gc.collect()
                
                # ========== BƯỚC 1: TẢI VIDEO ==========
                live_log.add(f"⬇️ [1/2] Tải {len(batch_urls)} video...")
                batch_pct_base = int((batch_idx / total_batches) * 100)
                batch_pct_range = int(100 / total_batches)
                
                ydl_opts = {
                    'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
                    'format': 'best[ext=mp4]/best',
                    'referer': 'https://www.tiktok.com/',
                    'headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                    },
                    'quiet': True,
                    'no_warnings': True,
                    'concurrent_fragment_downloads': 3,
                    'socket_timeout': 30,
                    'retries': 3,
                }
                
                success_count = 0
                fail_count = 0
                with YoutubeDL(ydl_opts) as ydl:
                    for i, url in enumerate(batch_urls):
                        # Đảm bảo đủ dung lượng trước khi tải từng video
                        check_and_cleanup_disk_space(required_mb=500, live_log=live_log, current_batch_num=batch_num)
                        pct = batch_pct_base + int((i / len(batch_urls)) * batch_pct_range * 0.4)
                        progress_bar.progress(min(99, pct))
                        
                        download_success = False
                        last_error = ""
                        
                        for attempt in range(1, 4):
                            attempt_str = f" (lần {attempt})" if attempt > 1 else ""
                            status_area.markdown(f"""
                            <div class="status-box">
                                <b>📦 Batch {batch_num}/{total_batches} — ⬇️ Tải video {i+1}/{len(batch_urls)}{attempt_str}</b><br>
                                <span class="url">{url[:60]}</span>
                            </div>
                            """, unsafe_allow_html=True)
                            try:
                                if "@" in url and "/video/" not in url:
                                    live_log.add(f"📺 Kênh: {url} → {num_channel_videos} bài")
                                    channel_opts = ydl_opts.copy()
                                    channel_opts['playlist_items'] = f'1-{num_channel_videos}'
                                    with YoutubeDL(channel_opts) as ydl_channel:
                                        ydl_channel.download([url])
                                else:
                                    live_log.add(f"⬇️ Video {i+1}/{len(batch_urls)}: {url[:50]}...")
                                    ydl.download([url])
                                download_success = True
                                success_count += 1
                                live_log.add(f"✅ Xong video {i+1}/{len(batch_urls)}")
                                break
                            except Exception as e:
                                last_error = str(e)
                                live_log.add(f"⚠️ Lỗi (lần {attempt}): {str(e)[:60]}")
                                if attempt < 3:
                                    time.sleep(0.5)
                        
                        if not download_success:
                            fail_count += 1
                            error_logs.append(f"❌ Batch {batch_num} - Video {i+1} ({url}): {last_error}")
                            live_log.add(f"❌ Bỏ qua video {i+1}.")
                        
                        gc.collect()
                
                downloaded_files = sorted(glob.glob(os.path.join(DOWNLOAD_DIR, "*.mp4")))
                live_log.add(f"📁 Tải xong: {success_count} OK, {fail_count} lỗi → {len(downloaded_files)} file")
                live_log.flush()
                
                if not downloaded_files:
                    live_log.add(f"⚠️ Batch {batch_num}: Không có video nào, bỏ qua.", force=True)
                    continue
                
                # ========== BƯỚC 2: GHÉP & RENDER VIDEO (FFMPEG THUẦN) ==========
                pct = batch_pct_base + int(batch_pct_range * 0.5)
                progress_bar.progress(min(99, pct))
                live_log.add(f"✂️ [2/2] Ghép & Render video bằng FFmpeg...")
                
                status_area.markdown(f"""
                <div class="status-box">
                    <b>📦 Batch {batch_num}/{total_batches} — 🎞️ Đang ghép & render video...</b>
                </div>
                """, unsafe_allow_html=True)
                
                # Thu thập thông tin video
                for idx_p, path in enumerate(downloaded_files):
                    dur = ffmpeg_get_duration(path)
                    live_log.add(f"  📂 {os.path.basename(path)} ({dur:.1f}s)")
                
                # Kiểm tra dung lượng trước khi render
                check_and_cleanup_disk_space(required_mb=500, live_log=live_log, current_batch_num=batch_num)
                
                # Render bằng ffmpeg thuần (KHÔNG load frame vào RAM Python)
                if mix_mode == "🎲 Trộn ngẫu nhiên theo nhạc":
                    # Chế độ trộn ngẫu nhiên: cắt segment + ghép nhạc nền
                    if not music_path or not os.path.exists(music_path):
                        status_area.error("❌ Chế độ trộn ngẫu nhiên yêu cầu phải có file nhạc nền.")
                        live_log.add("❌ Thiếu nhạc nền cho chế độ trộn ngẫu nhiên.", force=True)
                        continue
                    
                    render_ok = ffmpeg_random_mix_videos(
                        input_files=downloaded_files,
                        output_path=batch_output,
                        music_path=music_path,
                        segment_duration=segment_duration,
                        normalize_size=(1080, 1920),
                        live_log=live_log
                    )
                else:
                    # Chế độ ghép nối tiếp nguyên bản
                    render_ok = ffmpeg_concat_videos(
                        input_files=downloaded_files,
                        output_path=batch_output,
                        normalize_size=(1080, 1920),
                        keep_audio=keep_audio,
                        music_path=music_path,
                        live_log=live_log
                    )
                
                if not render_ok or not os.path.exists(batch_output):
                    live_log.add(f"❌ Batch {batch_num}: Render thất bại, bỏ qua.", force=True)
                    error_logs.append(f"❌ Batch {batch_num}: FFmpeg render thất bại.")
                    continue
                
                pct = batch_pct_base + int(batch_pct_range * 0.85)
                progress_bar.progress(min(99, pct))
                gc.collect()
                
                # ========== PHỐI LẠI ÂM THANH (nếu bật) ==========
                if audio_remix:
                    live_log.add(f"🎛️ Đang phối lại âm thanh (né Content ID)...")
                    status_area.markdown(f"""
                    <div class="status-box">
                        <b>📦 Batch {batch_num}/{total_batches} — 🎛️ Phối lại âm thanh...</b>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    # Xây dựng chuỗi audio filter cho ffmpeg
                    af_filters = []
                    
                    # 1. Pitch shift
                    if audio_pitch != 0:
                        pitch_factor = 2 ** (audio_pitch / 12.0)
                        af_filters.append(f"asetrate=44100*{pitch_factor:.4f}")
                        af_filters.append("aresample=44100")
                        live_log.add(f"  🎵 Pitch: {'+' if audio_pitch > 0 else ''}{audio_pitch} semitone")
                    
                    # 2. Speed change
                    if audio_speed != 1.0:
                        af_filters.append(f"atempo={audio_speed:.2f}")
                        live_log.add(f"  ⏩ Speed: {audio_speed:.2f}x")
                    
                    # 3. Random EQ — phá fingerprint tần số
                    if audio_eq_random:
                        # Ngẫu nhiên boost/cut 3 dải tần khác nhau
                        freqs = random.sample([200, 400, 800, 1200, 2000, 3500, 5000, 8000], 3)
                        for freq in freqs:
                            gain = random.choice([-4, -3, -2, 2, 3, 4, 5])
                            width = random.choice([100, 200, 300])
                            af_filters.append(f"equalizer=f={freq}:t=h:w={width}:g={gain}")
                        live_log.add(f"  🎚️ Random EQ: {len(freqs)} dải tần")
                    
                    # 4. Bass boost
                    if audio_bass > 0:
                        af_filters.append(f"bass=g={audio_bass}")
                        live_log.add(f"  🔊 Bass: +{audio_bass}dB")
                    
                    # 5. Audio Compressor — thay đổi dynamic range
                    if audio_compressor:
                        af_filters.append("acompressor=threshold=-20dB:ratio=4:attack=5:release=50")
                        live_log.add(f"  🔧 Compressor: ON")
                    
                    # 6. Stereo widening — thay đổi pha stereo
                    if audio_stereo:
                        af_filters.append("stereotools=mlev=0.015:slev=1.3:sbal=0.1")
                        af_filters.append("aphaser=type=t:speed=0.5:decay=0.3")
                        live_log.add(f"  🎧 Stereo widen + Phaser: ON")
                    
                    # 7. Reverb
                    if audio_reverb:
                        af_filters.append("aecho=0.8:0.85:40:0.3")
                        live_log.add(f"  🏛️ Reverb: ON")
                    
                    # 8. Highpass — loại bỏ sub-bass (thay đổi waveform)
                    af_filters.append("highpass=f=60")
                    
                    if af_filters:
                        remixed_output = batch_output.replace(".mp4", "_remixed.mp4")
                        af_string = ",".join(af_filters)
                        
                        ffmpeg_cmd = [
                            "ffmpeg", "-y", "-i", batch_output,
                            "-af", af_string,
                            "-c:v", "copy",
                            "-c:a", "aac",
                            "-b:a", "192k",
                            remixed_output
                        ]
                        
                        try:
                            result = subprocess.run(
                                ffmpeg_cmd,
                                capture_output=True, text=True, timeout=180
                            )
                            if result.returncode == 0 and os.path.exists(remixed_output):
                                os.replace(remixed_output, batch_output)
                                live_log.add(f"  ✅ Phối lại âm thanh thành công!")
                            else:
                                live_log.add(f"  ⚠️ FFmpeg lỗi: {result.stderr[:150]}")
                                error_logs.append(f"⚠️ Batch {batch_num} remix lỗi: {result.stderr[:150]}")
                        except subprocess.TimeoutExpired:
                            live_log.add(f"  ⚠️ Phối âm thanh quá lâu, bỏ qua.")
                        except FileNotFoundError:
                            live_log.add(f"  ⚠️ Không tìm thấy ffmpeg, bỏ qua remix.")
                        finally:
                            if os.path.exists(remixed_output):
                                try:
                                    os.remove(remixed_output)
                                except:
                                    pass
                        gc.collect()
                
                total_dur = ffmpeg_get_duration(batch_output)
                file_size_mb = os.path.getsize(batch_output) / (1024 * 1024)
                completed_videos.append({
                    'path': batch_output,
                    'batch_num': batch_num,
                    'clip_count': len(downloaded_files),
                    'duration': total_dur,
                    'size_mb': file_size_mb,
                })
                live_log.add(f"🎉 Batch {batch_num} HOÀN THÀNH! {file_size_mb:.1f} MB", force=True)
                
                # Dọn dẹp toàn bộ file tải về của batch này ngay sau khi hoàn thành kết xuất để giải phóng đĩa
                for f in glob.glob(os.path.join(DOWNLOAD_DIR, "*")):
                    try:
                        if os.path.isdir(f):
                            shutil.rmtree(f)
                        else:
                            os.remove(f)
                    except:
                        pass
                gc.collect()
                
            except Exception as e:
                error_logs.append(f"❌ Batch {batch_num} lỗi: {str(e)}")
                live_log.add(f"💥 LỖI BATCH {batch_num}: {str(e)}", force=True)
                # Giải phóng RAM nếu bị lỗi
                gc.collect()
                continue
        
        # ========== HIỂN THỊ TẤT CẢ KẾT QUẢ ==========
        progress_bar.progress(100)
        
        if completed_videos:
            live_log.add(f"")
            live_log.add(f"{'='*40}")
            live_log.add(f"🏁 HOÀN THÀNH TẤT CẢ: {len(completed_videos)}/{total_batches} batch thành công!")
            live_log.add(f"{'='*40}")
            
            status_area.markdown(f"""
            <div class="status-box" style="border-left-color: #00c853;">
                <b>🎉 Hoàn thành! {len(completed_videos)}/{total_batches} batch thành công.</b><br>
                📊 Tổng cộng <b>{len(completed_videos)}</b> video đã sẵn sàng tải về.
            </div>
            """, unsafe_allow_html=True)
            
            # Hiển thị danh sách video
            with video_preview.container():
                for vid in completed_videos:
                    st.markdown(f"""
                    <div class="card" style="margin-bottom: 10px;">
                        <h4>📦 Batch {vid['batch_num']} — {vid['clip_count']} clip • {vid['duration']:.1f}s • {vid['size_mb']:.1f} MB</h4>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    st.video(vid['path'])
                    st.download_button(
                        label=f"📥 TẢI BATCH {vid['batch_num']} ({vid['size_mb']:.1f} MB)",
                        data=open(vid['path'], 'rb'),
                        file_name=f"tiktok_batch_{vid['batch_num']}.mp4",
                        mime="video/mp4",
                        use_container_width=True,
                        key=f"dl_batch_{vid['batch_num']}"
                    )
                    st.markdown("---")
        else:
            status_area.error("❌ Không có batch nào hoàn thành thành công.")
            progress_bar.progress(0)
        
        if error_logs:
            with log_area.container():
                st.markdown("---")
                st.markdown("### ⚠️ Nhật ký lỗi (Logs)")
                st.code("\n".join(error_logs), language="text")

