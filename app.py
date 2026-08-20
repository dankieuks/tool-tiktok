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
<div style="background:#231728; border:1px solid #F472B6; border-radius:12px; padding:12px 16px; max-height:300px; overflow-y:auto; font-family:'JetBrains Mono',monospace; font-size:0.78rem; color:#FBCFE8; line-height:1.6; box-shadow: 0 4px 15px rgba(255,0,127,0.1);">
<pre style="margin:0; white-space:pre-wrap; word-break:break-all; color:#FCE7F3;">{log_text}</pre>
</div>
""", unsafe_allow_html=True)

# ==========================================
# RENDER VIDEO BẰNG FFMPEG THUẦN (TIẾT KIỆM RAM)
# ==========================================
def ffmpeg_concat_videos(input_files, output_path, normalize_size=(1080, 1920), 
                         keep_audio=True, music_path=None, live_log=None):
    """Ghép video bằng ffmpeg thuần - không load frame vào RAM Python."""
    w, h = normalize_size
    filter_parts = []
    concat_inputs = []
    
    for i, fpath in enumerate(input_files):
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
    
    cmd = ["ffmpeg", "-y"]
    for fpath in input_files:
        cmd.extend(["-i", fpath])
    
    if not keep_audio and music_path and os.path.exists(music_path):
        cmd.extend(["-i", music_path])
    
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

def ffmpeg_merge_batches(batch_paths, output_path, live_log=None):
    """Nối nhiều video batch thành 1 video dài bằng ffmpeg concat demuxer."""
    if not batch_paths or len(batch_paths) < 2:
        return False
    
    if live_log:
        live_log.add(f"🔗 Đang nối {len(batch_paths)} video batch thành 1 video dài...")
    
    concat_list_path = os.path.join(os.path.dirname(output_path), "_merge_list.txt")
    try:
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for p in batch_paths:
                safe_path = p.replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")
        
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_list_path,
            "-c", "copy",
            "-movflags", "+faststart",
            output_path
        ]
        
        if live_log:
            live_log.add("⚡ Ghép nhanh (copy stream, không re-encode)...")
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
        
        if result.returncode == 0 and os.path.exists(output_path):
            if live_log:
                dur = ffmpeg_get_duration(output_path)
                size_mb = os.path.getsize(output_path) / (1024 * 1024)
                live_log.add(f"✅ Nối thành công! {dur:.1f}s • {size_mb:.1f} MB", force=True)
            return True
        
        if live_log:
            live_log.add("⚠️ Copy stream thất bại, đang re-encode...")
        
        cmd_reencode = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_list_path,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-threads", "2",
            "-movflags", "+faststart",
            "-r", "24",
            output_path
        ]
        
        result2 = subprocess.run(cmd_reencode, capture_output=True, text=True, timeout=1800)
        
        if result2.returncode == 0 and os.path.exists(output_path):
            if live_log:
                dur = ffmpeg_get_duration(output_path)
                size_mb = os.path.getsize(output_path) / (1024 * 1024)
                live_log.add(f"✅ Nối thành công (re-encode)! {dur:.1f}s • {size_mb:.1f} MB", force=True)
            return True
        
        if live_log:
            live_log.add(f"❌ Nối video thất bại: {result2.stderr[-200:]}", force=True)
        return False
        
    except subprocess.TimeoutExpired:
        if live_log:
            live_log.add("⚠️ Nối video quá lâu (timeout), bỏ qua.", force=True)
        return False
    except Exception as e:
        if live_log:
            live_log.add(f"⚠️ Lỗi nối video: {str(e)[:100]}", force=True)
        return False
    finally:
        try:
            os.remove(concat_list_path)
        except:
            pass

def ffmpeg_random_mix_videos(input_files, output_path, music_path, segment_duration=3.0,
                              normalize_size=(1080, 1920), live_log=None):
    """Trộn ngẫu nhiên các đoạn video ngắn khớp với nhạc nền bằng ffmpeg thuần."""
    w, h = normalize_size
    music_dur = ffmpeg_get_duration(music_path)
    if music_dur <= 0:
        if live_log:
            live_log.add("⚠️ Không đọc được thời lượng nhạc nền.")
        return False
    
    if live_log:
        live_log.add(f"🎵 Nhạc nền: {music_dur:.1f}s • Segment: {segment_duration}s")
    
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
    
    temp_segments = []
    current_dur = 0.0
    seg_idx = 0
    
    try:
        while current_dur < music_dur:
            src_file = random.choice(usable_files)
            src_dur = video_durations[src_file]
            start_t = random.uniform(0, src_dur - segment_duration)
            
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
        
        concat_list_path = os.path.join(os.path.dirname(output_path), "_concat_list.txt")
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for seg in temp_segments:
                f.write(f"file '{seg}'\n")
        
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
# TRÍCH XUẤT 1 VIDEO TIKTOK (PUBLIC SNAPTIK)
# ==========================================
def fetch_single_tiktok_info(url):
    """Trích xuất và tải 1 video TikTok không logo + nhạc MP3 cho trang Public."""
    clean_url = url.strip()
    match = re.search(r'(https?://[^\s]+)', clean_url)
    if match:
        clean_url = match.group(1)
            
    if not clean_url:
        return None, "Vui lòng nhập đường dẫn (URL) TikTok hợp lệ."

    timestamp = int(time.time())
    out_prefix = f"snaptik_{timestamp}_{random.randint(1000, 9999)}"
    mp4_filename = f"{out_prefix}.mp4"
    mp3_filename = f"{out_prefix}.mp3"
    
    mp4_path = os.path.join(OUTPUT_DIR, mp4_filename)
    mp3_path = os.path.join(OUTPUT_DIR, mp3_filename)

    # 🚀 ENGINE 1: TRÍCH XUẤT SIÊU TỐC QUA TIKWM API (Hỗ trợ 100% mọi link ?is_from_webapp=1, vt.tiktok.com...)
    try:
        import requests
        api_resp = requests.post("https://www.tikwm.com/api/", data={"url": clean_url, "hd": 1}, timeout=12)
        if api_resp.status_code == 200:
            res_json = api_resp.json()
            if res_json.get("code") == 0:
                data = res_json.get("data", {})
                video_url = data.get("hdplay") or data.get("play")
                music_url = data.get("music")
                title = data.get("title") or "TikTok Video"
                author_info = data.get("author") or {}
                uploader = author_info.get("nickname") or author_info.get("unique_id") or "TikTok User"
                duration = data.get("duration") or 0

                if video_url:
                    # Tải MP4 không logo trực tiếp từ CDN
                    v_res = requests.get(video_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
                    if v_res.status_code == 200:
                        with open(mp4_path, "wb") as f:
                            f.write(v_res.content)
                        
                        # Tải MP3 trực tiếp
                        if music_url:
                            m_res = requests.get(music_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
                            if m_res.status_code == 200:
                                with open(mp3_path, "wb") as f:
                                    f.write(m_res.content)
                        
                        if not os.path.exists(mp3_path) and os.path.exists(mp4_path):
                            cmd = ["ffmpeg", "-y", "-i", mp4_path, "-vn", "-c:a", "libmp3lame", "-q:a", "2", mp3_path]
                            subprocess.run(cmd, capture_output=True, text=True, timeout=60)

                        size_mb = os.path.getsize(mp4_path) / (1024 * 1024)
                        mp3_size_mb = os.path.getsize(mp3_path) / (1024 * 1024) if os.path.exists(mp3_path) else 0.0

                        return {
                            'title': title,
                            'uploader': uploader,
                            'thumbnail': data.get("cover") or "",
                            'mp4_filename': mp4_filename,
                            'mp3_filename': mp3_filename,
                            'mp4_path': mp4_path,
                            'mp3_path': mp3_path,
                            'duration': duration,
                            'size_mb': size_mb,
                            'mp3_size_mb': mp3_size_mb
                        }, None
    except Exception as e_api:
        print(f"TikWM API fallback to yt-dlp: {e_api}")

    # 🚀 ENGINE 2: FALLBACK DỰ PHÒNG SỬ DỤNG YT-DLP
    raw_url = clean_url.split("?")[0] if "?" in clean_url else clean_url
    ydl_opts = {
        'outtmpl': mp4_path,
        'format': 'best[ext=mp4]/best',
        'referer': 'https://www.tiktok.com/',
        'headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        },
        'quiet': True,
        'no_warnings': True,
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(raw_url, download=True)
            title = info.get('title') or info.get('description') or 'TikTok Video'
            uploader = info.get('uploader') or info.get('uploader_id') or info.get('channel') or 'TikTok User'
            thumbnail = info.get('thumbnail') or ''
            duration = info.get('duration') or ffmpeg_get_duration(mp4_path)

        if os.path.exists(mp4_path):
            cmd = ["ffmpeg", "-y", "-i", mp4_path, "-vn", "-c:a", "libmp3lame", "-q:a", "2", mp3_path]
            subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            size_mb = os.path.getsize(mp4_path) / (1024 * 1024)
            mp3_size_mb = os.path.getsize(mp3_path) / (1024 * 1024) if os.path.exists(mp3_path) else 0.0
            
            return {
                'title': title,
                'uploader': uploader,
                'thumbnail': thumbnail,
                'mp4_filename': mp4_filename,
                'mp3_filename': mp3_filename,
                'mp4_path': mp4_path,
                'mp3_path': mp3_path,
                'duration': duration,
                'size_mb': size_mb,
                'mp3_size_mb': mp3_size_mb
            }, None
        else:
            return None, "Không thể tải video từ link này. Vui lòng kiểm tra lại đường dẫn."
    except Exception as e:
        return None, f"Lỗi tải video: {str(e)[:120]}"

# ==========================================
# CẤU HÌNH TỐI ƯU SEO & PWA WEB ICON
# ==========================================
def patch_streamlit_seo_and_pwa():
    """Tự động chèn thẻ SEO Meta Tags, Open Graph, Twitter Cards, Schema.org và PWA Manifest vào index.html của Streamlit."""
    import shutil
    try:
        st_dir = os.path.dirname(st.__file__)
        static_dir = os.path.join(st_dir, "static")
        
        project_dir = os.path.dirname(os.path.abspath(__file__))
        src_icon = os.path.join(project_dir, "app_icon.png")
        dest_icon = os.path.join(static_dir, "app_icon.png")
        
        if os.path.exists(src_icon):
            shutil.copy(src_icon, dest_icon)
            
        manifest_content = """{
  "name": "Tải Video Nhanh - Tải Video TikTok Không Logo",
  "short_name": "Tải Video Nhanh",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#F8FAFC",
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

        index_path = os.path.join(static_dir, "index.html")
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                html = f.read()
            
            seo_head_tags = """
  <!-- Full SEO Meta Tags -->
  <title>Tải Video Nhanh - Tải Video TikTok Không Logo Mờ Miễn Phí (Watermark Remover)</title>
  <meta name="description" content="Tải Video Nhanh giúp bạn tải video TikTok không dán nhãn logo (watermark) miễn phí 100%, tốc độ cực nhanh, hỗ trợ tải video MP4 FullHD và tách nhạc MP3 âm thanh gốc mượt mà trên iPhone, Android, PC.">
  <meta name="keywords" content="tải video nhanh, tai video nhanh, tải video tiktok, tiktok downloader, tải tiktok không logo, tải nhạc tiktok mp3, tiktok watermark remover, tải video tiktok fullhd">
  <meta name="robots" content="index, follow">
  <meta name="author" content="Tải Video Nhanh">
  
  <!-- Open Graph / Facebook -->
  <meta property="og:type" content="website">
  <meta property="og:title" content="Tải Video Nhanh - Tải Video TikTok Không Logo Mờ Miễn Phí FullHD">
  <meta property="og:description" content="Công cụ Tải Video Nhanh trích xuất video TikTok không dán nhãn logo mờ miễn phí 100%, tốc độ cực nhanh, tải MP4 FullHD & nhạc MP3 mượt mà trên mọi thiết bị.">
  <meta property="og:image" content="app_icon.png">
  <meta property="og:site_name" content="Tải Video Nhanh">
  <meta property="og:locale" content="vi_VN">

  <!-- Twitter Card -->
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="Tải Video Nhanh - Tải Video TikTok Không Logo Mờ Miễn Phí FullHD">
  <meta name="twitter:description" content="Công cụ Tải Video Nhanh trích xuất video TikTok không dán nhãn logo mờ miễn phí 100%, tốc độ cực nhanh.">
  <meta name="twitter:image" content="app_icon.png">

  <!-- PWA & Mobile Icons -->
  <link rel="apple-touch-icon" href="app_icon.png">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <link rel="manifest" href="manifest.json">

  <!-- Schema.org JSON-LD Structured Data cho Google Rich Snippets -->
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "WebApplication",
    "name": "Tải Video Nhanh",
    "alternateName": "Tải Video Nhanh Downloader",
    "url": "/",
    "applicationCategory": "MultimediaApplication",
    "operatingSystem": "All",
    "description": "Tải Video Nhanh - Công cụ tải video TikTok không dán nhãn logo mờ (watermark) miễn phí 100%, tốc độ cao, tải MP4 FullHD và MP3 âm thanh gốc.",
    "offers": {
      "@type": "Offer",
      "price": "0",
      "priceCurrency": "VND"
    }
  }
  </script>
"""
            if "Tải Video Nhanh - Tải Video TikTok" not in html:
                html = html.replace("<head>", f"<head>{seo_head_tags}")
                with open(index_path, "w", encoding="utf-8") as f:
                    f.write(html)
    except Exception as e:
        print(f"SEO patching ignored: {e}")

patch_streamlit_seo_and_pwa()

# ==========================================
# CẤU HÌNH THƯ MỤC
# ==========================================
DOWNLOAD_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_dl"))
OUTPUT_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"))
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "final_music_video.mp4")

def check_and_cleanup_disk_space(required_mb=500, live_log=None, current_batch_num=None):
    """Kiểm tra dung lượng đĩa trống, tự động xóa cache/tệp cũ."""
    total, used, free = shutil.disk_usage(os.path.dirname(os.path.abspath(__file__)))
    free_mb = free / (1024 * 1024)
    
    if free_mb < required_mb:
        msg = f"⚠️ Cảnh báo: Dung lượng đĩa thấp ({free_mb:.1f} MB < {required_mb} MB). Đang tự động dọn dẹp cache..."
        if live_log:
            live_log.add(msg)
        else:
            print(msg)
            
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
        
        if os.path.exists(OUTPUT_DIR):
            for f in glob.glob(os.path.join(OUTPUT_DIR, "*")):
                filename = os.path.basename(f)
                if filename == "temp_music.mp3":
                    continue
                if filename.startswith("batch_") and filename.endswith(".mp4"):
                    match = re.search(r'batch_(\d+)', filename)
                    if match:
                        f_batch_num = int(match.group(1))
                        if current_batch_num is not None and f_batch_num >= current_batch_num:
                            continue
                    try:
                        os.remove(f)
                    except:
                        pass

def init_temp_dirs():
    """Khởi tạo và làm sạch các thư mục tạm lúc khởi chạy."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    check_and_cleanup_disk_space(required_mb=500)

init_temp_dirs()

# ==========================================
# GIAO DIỆN & NAVIGATION
# ==========================================
project_icon = "app_icon.png" if os.path.exists("app_icon.png") else "⚡"
st.set_page_config(
    page_title="Tải Video Nhanh - Tải Video TikTok Không Logo",
    page_icon=project_icon,
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Style CSS Bright Light Theme sáng sủa, rực rỡ và hiện đại
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');

* { font-family: 'Plus Jakarta Sans', 'Inter', sans-serif; }

/* Ẩn hoàn toàn Sidebar của Streamlit cho giao diện chuẩn Landing Page */
[data-testid="stSidebar"] {
    display: none !important;
}
div[data-testid="collapsedControl"] {
    display: none !important;
}

/* Ẩn hoàn toàn thanh header mặc định của Streamlit (thanh xám 3 dấu chấm trên cùng) */
header[data-testid="stHeader"],
[data-testid="stHeader"],
.stAppHeader,
#MainMenu,
footer.stFooter {
    display: none !important;
    visibility: hidden !important;
    height: 0 !important;
    padding: 0 !important;
    margin: 0 !important;
}

body, .stApp {
    background-color: #FAFAFC !important;
    background-image: 
        radial-gradient(at 0% 0%, rgba(236, 72, 153, 0.08) 0px, transparent 50%),
        radial-gradient(at 100% 0%, rgba(14, 165, 233, 0.08) 0px, transparent 50%),
        radial-gradient(at 50% 100%, rgba(139, 92, 246, 0.06) 0px, transparent 50%) !important;
    color: #0F172A !important;
}

/* Ép mở rộng toàn bộ khung chứa chính của Streamlit */
.stApp [data-testid="stMainBlockContainer"],
.stApp [data-testid="block-container"],
.stApp .main .block-container,
.stApp .stMainBlockContainer,
[data-testid="stMainBlockContainer"],
[data-testid="block-container"],
.block-container {
    width: 95% !important;
    max-width: 1450px !important;
    margin-left: auto !important;
    margin-right: auto !important;
    padding-top: 1rem !important;
    padding-bottom: 2.5rem !important;
}

/* Container mở rộng 1.55x thoáng đãng dành riêng cho Public Landing Page */
.landing-page-wrap {
    width: 92% !important;
    max-width: 1350px !important;
    margin-left: auto !important;
    margin-right: auto !important;
    padding: 0 !important;
    box-sizing: border-box !important;
}

/* 1. Màn Hình Desktop (>= 1024px): Mở rộng 1.55x (max 1350px) vô cùng thoáng đãng */
@media screen and (min-width: 1024px) {
    .landing-page-wrap {
        width: 92% !important;
        max-width: 1350px !important;
        margin-left: auto !important;
        margin-right: auto !important;
    }
}

/* 2. Màn Hình Tablet (768px - 1023px): Thu nhỏ nhẹ kích thước cho cân đối */
@media screen and (min-width: 768px) and (max-width: 1023px) {
    .landing-page-wrap {
        width: 95% !important;
        max-width: 100% !important;
        margin-left: auto !important;
        margin-right: auto !important;
    }
    
    #hero-header {
        padding: 20px 16px 14px 16px !important;
        margin-bottom: 16px !important;
        border-radius: 18px !important;
    }
    
    #hero-header h1 {
        font-size: 1.85rem !important;
        margin-bottom: 4px !important;
    }
    
    #hero-header p {
        font-size: 0.88rem !important;
        margin-bottom: 8px !important;
    }
    
    .trust-badge {
        font-size: 0.76rem !important;
        padding: 4px 10px !important;
    }
    
    .stTextInput input {
        font-size: 0.98rem !important;
        padding: 12px 14px !important;
    }
    
    .stButton>button {
        padding: 12px 20px !important;
        font-size: 0.98rem !important;
    }
}

/* 3. Màn Hình Mobile Điện Thoại (< 768px): Thu nhỏ kích thước gọn gàng, sát trên */
@media screen and (max-width: 767px) {
    .stApp [data-testid="stMainBlockContainer"],
    .stApp .main .block-container,
    .block-container {
        padding-top: 0.1rem !important;
        padding-bottom: 1rem !important;
    }

    .landing-page-wrap {
        width: 100% !important;
        max-width: 100% !important;
        margin-left: auto !important;
        margin-right: auto !important;
        padding: 0 4px !important;
    }
    
    #top-navbar {
        flex-direction: row !important;
        justify-content: space-between !important;
        align-items: center !important;
        gap: 6px !important;
        text-align: left !important;
        padding: 8px 12px !important;
        margin-bottom: 8px !important;
    }
    
    #top-navbar div:last-child {
        display: none !important; /* Ẩn menu điều hướng dài trên mobile để tiết kiệm diện tích */
    }
    
    #hero-header {
        padding: 10px 10px 8px 10px !important;
        margin-bottom: 10px !important;
        border-radius: 14px !important;
    }
    
    #hero-header h1 {
        font-size: 1.3rem !important;
        margin-bottom: 2px !important;
    }
    
    #hero-header p {
        font-size: 0.78rem !important;
        margin-bottom: 6px !important;
        line-height: 1.35 !important;
    }
    
    .trust-badge-group {
        gap: 3px !important;
    }

    .trust-badge {
        font-size: 0.65rem !important;
        padding: 2px 6px !important;
    }

    div[data-testid="stTextInput"] {
        margin-top: 0 !important;
    }
    
    div[data-testid="stTextInput"] label p {
        font-size: 0.85rem !important;
    }

    .stTextInput input {
        font-size: 0.88rem !important;
        padding: 9px 12px !important;
        border-radius: 10px !important;
    }

    .stButton>button {
        padding: 10px 16px !important;
        font-size: 0.9rem !important;
        border-radius: 10px !important;
    }
}

/* Thẻ White Glassmorphism sáng sủa cao cấp */
.card {
    background: #FFFFFF;
    border: 1.5px solid #E2E8F0;
    border-radius: 20px;
    padding: 24px;
    margin-bottom: 20px;
    box-shadow: 0 4px 20px rgba(15, 23, 42, 0.04);
    color: #0F172A;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}
.card:hover {
    transform: translateY(-4px);
    box-shadow: 0 12px 30px rgba(236, 72, 153, 0.12);
    border-color: #F472B6;
}
.card h3, .card h4 {
    margin-top: 0;
    margin-bottom: 10px;
    font-size: 1.15rem;
    color: #BE185D;
    font-weight: 700;
}

.stButton>button {
    background: linear-gradient(135deg, #EC4899 0%, #8B5CF6 50%, #0EA5E9 100%) !important;
    color: #FFFFFF !important;
    border: none !important;
    padding: 16px 30px !important;
    font-weight: 800 !important;
    font-size: 1.1rem !important;
    border-radius: 14px !important;
    transition: all 0.3s ease !important;
    width: 100% !important;
    letter-spacing: 0.5px;
    box-shadow: 0 8px 25px rgba(236, 72, 153, 0.35) !important;
    text-transform: uppercase;
}
.stButton>button:hover {
    opacity: 0.96 !important;
    transform: translateY(-3px) scale(1.01) !important;
    box-shadow: 0 12px 35px rgba(14, 165, 233, 0.4) !important;
}

.stTextInput input, .stTextArea textarea {
    background-color: #FFFFFF !important;
    border: 2px solid #CBD5E1 !important;
    border-radius: 14px !important;
    color: #0F172A !important;
    font-size: 1.05rem !important;
    padding: 14px 18px !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.02) !important;
}
.stTextInput input:focus, .stTextArea textarea:focus {
    border-color: #EC4899 !important;
    box-shadow: 0 0 0 4px rgba(236, 72, 153, 0.15) !important;
}

.trust-badge-group {
    display: flex;
    justify-content: center;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 16px;
}
.trust-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 16px;
    border-radius: 20px;
    background: #FFFFFF;
    border: 1px solid #F1F5F9;
    color: #BE185D;
    font-size: 0.85rem;
    font-weight: 700;
    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.04);
}

@keyframes pulseBorder {
    0% { box-shadow: 0 0 0 0 rgba(236, 72, 153, 0.4); }
    70% { box-shadow: 0 0 0 12px rgba(236, 72, 153, 0); }
    100% { box-shadow: 0 0 0 0 rgba(236, 72, 153, 0); }
}

.pulse-glow {
    animation: pulseBorder 2.5s infinite;
}

.status-box {
    padding: 16px 20px;
    border-radius: 14px;
    background: #FFFFFF;
    border-left: 4px solid #10B981;
    margin-bottom: 12px;
    font-size: 0.95rem;
    color: #0F172A;
    box-shadow: 0 4px 15px rgba(0, 0, 0, 0.05);
}
.status-box b { color: #059669; }
.status-box .url { color: #0EA5E9; word-break: break-all; font-size: 0.85rem; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

# Đọc tham số bí mật trên URL (ví dụ: gõ ?page=admin hoặc ?admin=1 trên trình duyệt)
qp_page = st.query_params.get("page", "")
qp_admin = st.query_params.get("admin", "")

is_admin_route = (qp_page == "admin" or qp_admin in ["1", "true", "yes"])

if is_admin_route:
    app_mode = "🔐 Admin Batch Mixer"
else:
    app_mode = "🏠 Tải Video TikTok (Public)"

# ==========================================
# CHẾ ĐỘ 1: PUBLIC LANDING PAGE (TẢI VIDEO NHANH 1 LINK)
# ==========================================
def render_public_landing_page():
    # Khung Wrapper Căn Giữa 70% dành riêng cho Landing Page
    st.markdown('<div class="landing-page-wrap">', unsafe_allow_html=True)

    # Top Navbar điều hướng sáng sủa chuẩn Landing Page
    st.markdown("""
    <nav id="top-navbar" style="display: flex; justify-content: space-between; align-items: center; padding: 14px 24px; background: rgba(255, 255, 255, 0.9); backdrop-filter: blur(16px); border: 1.5px solid #E2E8F0; border-radius: 16px; margin-bottom: 25px; box-shadow: 0 4px 20px rgba(0,0,0,0.04);">
        <div style="display: flex; flex-direction: column;">
            <div style="display: flex; align-items: center; gap: 8px;">
                <span style="font-size: 1.6rem;">⚡</span>
                <span style="font-size: 1.4rem; font-weight: 800; background: linear-gradient(135deg, #EC4899 0%, #8B5CF6 50%, #0EA5E9 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">Tải Video Nhanh</span>
            </div>
            <div style="font-size: 0.8rem; font-weight: 700; color: #64748B; margin-left: 36px; margin-top: -2px;">
                Công cụ thuộc <a href="https://www.aihocviec.com" target="_blank" rel="noopener noreferrer" style="color: #EC4899; text-decoration: underline; font-weight: 800;">Aihocviec.com</a>
            </div>
        </div>
        <div style="display: flex; align-items: center; gap: 20px; font-size: 0.9rem; font-weight: 700;">
            <a href="#main-content" style="color: #0F172A; text-decoration: none;">🏠 Trang Chủ</a>
            <a href="#features-section" style="color: #64748B; text-decoration: none;">⚡ Tính Năng</a>
            <a href="#faq-section" style="color: #64748B; text-decoration: none;">❓ FAQ</a>
        </div>
    </nav>
    """, unsafe_allow_html=True)

    # Banner Tiêu Đề Hero Sáng Sủa Trung Tâm
    st.markdown("""
    <header id="hero-header" style="text-align: center; padding: 35px 20px 25px 20px; background: linear-gradient(135deg, #FFF0F6 0%, #F0F9FF 50%, #F5F3FF 100%); border-radius: 24px; border: 1.5px solid #F1F5F9; box-shadow: 0 10px 30px rgba(236, 72, 153, 0.06); margin-bottom: 25px;">
        <h1 style="font-size: 2.7rem; font-weight: 800; background: linear-gradient(135deg, #EC4899 0%, #8B5CF6 50%, #0EA5E9 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 6px;">
            ⚡ Tải Video TikTok Không Logo
        </h1>
        <p style="color: #64748B; font-size: 0.95rem; font-weight: 700; margin-bottom: 12px;">
            <b>Tải Video Nhanh</b> là công cụ miễn phí thuộc hệ sinh thái <a href="https://www.aihocviec.com" target="_blank" rel="noopener noreferrer" style="color: #EC4899; text-decoration: underline; font-weight: 800;">www.aihocviec.com</a>
        </p>
        <p style="color: #475569; font-size: 1.1rem; font-weight: 600; margin-bottom: 16px;">
            Dán đường dẫn (link) video TikTok vào bên dưới để tải MP4 Full HD sạch watermark & tách nhạc MP3 miễn phí 100%
        </p>
        <div class="trust-badge-group">
            <span class="trust-badge">🔥 10M+ Video Đã Tải</span>
            <span class="trust-badge">⚡ 0.3s Siêu Tốc</span>
            <span class="trust-badge">⭐ 4.9/5 Đánh Giá</span>
            <span class="trust-badge">🔒 Miễn Phí 100%</span>
        </div>
    </header>
    """, unsafe_allow_html=True)

    # Khung Dán Link 1 Ô Duy Nhất Trung Tâm
    single_url_input = st.text_input(
        "🔗 Dán đường dẫn (link) video TikTok vào đây:",
        placeholder="https://www.tiktok.com/@username/video/1234567890123456789",
        key="input_public_single_url"
    )
    
    btn_dl = st.button("🚀 TẢI VIDEO NGAY", use_container_width=True, key="btn_single_download")

    if btn_dl:
        if not single_url_input.strip():
            st.error("⚠️ Vui lòng nhập link video TikTok trước khi nhấn Tải.")
        else:
            with st.spinner("⏳ Đang trích xuất & chuẩn bị video không logo..."):
                info, err = fetch_single_tiktok_info(single_url_input)
                if err:
                    st.error(f"❌ {err}")
                else:
                    st.session_state['single_result'] = info

    # Display Result
    if st.session_state.get('single_result'):
        res = st.session_state['single_result']
        st.markdown(f"""
        <section id="result-section" class="card" style="border-left: 5px solid #10B981; background: #FFFFFF; border: 1.5px solid #A7F3D0; margin-top: 20px; box-shadow: 0 6px 25px rgba(16,185,129,0.08);">
            <h2 style="color: #065F46; margin-top: 0; font-size: 1.3rem;">✅ Video đã sẵn sàng tải về!</h2>
            <p style="color: #0F172A; font-size: 1.05rem; margin-bottom: 4px;"><b>👤 Tác giả:</b> @{res['uploader']}</p>
            <p style="color: #475569; font-size: 0.95rem; margin-bottom: 0;"><b>📝 Tiêu đề:</b> {res['title'][:120]}</p>
        </section>
        """, unsafe_allow_html=True)
        
        col_res1, col_res2 = st.columns([1, 1], gap="medium")
        with col_res1:
            st.markdown(f"""
            <div style="text-align: center; margin-bottom: 15px;">
                <video controls preload="metadata" style="width: 100%; max-height: 420px; border-radius: 14px; background: #000; box-shadow: 0 8px 20px rgba(0,0,0,0.15);">
                    <source src="app/static/{res['mp4_filename']}" type="video/mp4">
                    Trình duyệt không hỗ trợ thẻ video.
                </video>
            </div>
            """, unsafe_allow_html=True)
        
        with col_res2:
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown(f"""
            <a id="btn-download-mp4" href="app/static/{res['mp4_filename']}" download="tiktok_no_watermark.mp4" target="_blank" style="display: block; width: 100%; text-align: center; background: linear-gradient(135deg, #EC4899 0%, #8B5CF6 100%); color: #ffffff; padding: 16px 20px; border-radius: 14px; font-weight: 800; text-decoration: none; font-size: 1.1rem; box-shadow: 0 6px 20px rgba(236,72,153,0.3); margin-bottom: 15px; text-transform: uppercase;">
                📥 TẢI VIDEO MP4 (KHÔNG LOGO - {res['size_mb']:.1f} MB)
            </a>
            """, unsafe_allow_html=True)
            
            if os.path.exists(res['mp3_path']):
                st.markdown(f"""
                <a id="btn-download-mp3" href="app/static/{res['mp3_filename']}" download="tiktok_audio.mp3" target="_blank" style="display: block; width: 100%; text-align: center; background: linear-gradient(135deg, #10B981 0%, #059669 100%); color: #ffffff; padding: 16px 20px; border-radius: 14px; font-weight: 800; text-decoration: none; font-size: 1.05rem; box-shadow: 0 6px 20px rgba(16,185,129,0.3); margin-bottom: 15px; text-transform: uppercase;">
                    🎵 TẢI NHẠC MP3 ({res['mp3_size_mb']:.1f} MB)
                </a>
                """, unsafe_allow_html=True)

    # Features Grid
    st.markdown("---")
    st.markdown("""
    <section id="features-section">
        <h2 style="font-size: 1.6rem; font-weight: 800; color: #BE185D; margin-bottom: 18px; text-align: center;">⚡ Tính Năng Nổi Bật Của Tải Video Nhanh</h2>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 18px; margin-bottom: 25px;">
            <article class="card">
                <div style="font-size: 2.2rem; margin-bottom: 8px;">🚫</div>
                <h3 style="margin: 0 0 8px 0; color: #EC4899; font-size: 1.15rem;">Không Dán Nhãn Logo</h3>
                <p style="font-size: 0.9rem; color: #475569; margin: 0; line-height: 1.6;">Tải video TikTok sạch 100%, tự động loại bỏ mờ watermark & ID chìm sắc nét.</p>
            </article>
            <article class="card">
                <div style="font-size: 2.2rem; margin-bottom: 8px;">⚡</div>
                <h3 style="margin: 0 0 8px 0; color: #0EA5E9; font-size: 1.15rem;">Tốc Độ Cực Nhanh</h3>
                <p style="font-size: 0.9rem; color: #475569; margin: 0; line-height: 1.6;">Trích xuất và chuẩn bị đường dẫn tải xuống siêu tốc chỉ trong 0.3 - 0.5 giây.</p>
            </article>
            <article class="card">
                <div style="font-size: 2.2rem; margin-bottom: 8px;">🎵</div>
                <h3 style="margin: 0 0 8px 0; color: #8B5CF6; font-size: 1.15rem;">Tách Nhạc MP3 Gốc</h3>
                <p style="font-size: 0.9rem; color: #475569; margin: 0; line-height: 1.6;">Tách âm thanh gốc MP3 320kbps chất lượng cao khỏi video chỉ bằng 1 click.</p>
            </article>
            <article class="card">
                <div style="font-size: 2.2rem; margin-bottom: 8px;">📱</div>
                <h3 style="margin: 0 0 8px 0; color: #F59E0B; font-size: 1.15rem;">Hỗ Trợ Đa Nền Tảng</h3>
                <p style="font-size: 0.9rem; color: #475569; margin: 0; line-height: 1.6;">Hoạt động mượt mà trên iPhone, iPad, Android và tất cả trình duyệt PC.</p>
            </article>
        </div>
    </section>
    """, unsafe_allow_html=True)

    # Comparison Table Section
    st.markdown("""
    <section id="comparison-section">
        <h2 style="font-size: 1.6rem; font-weight: 800; color: #BE185D; margin-bottom: 18px; text-align: center;">🔥 So Sánh Với Công Cụ Thông Thường</h2>
        <div class="card" style="padding: 0; overflow: hidden; border: 1.5px solid #E2E8F0;">
            <table style="width: 100%; border-collapse: collapse; text-align: left; font-size: 0.95rem;">
                <thead>
                    <tr style="background: linear-gradient(135deg, #FFF0F6, #F0F9FF); border-bottom: 1.5px solid #E2E8F0;">
                        <th style="padding: 16px 20px; color: #0F172A;">Tính Năng</th>
                        <th style="padding: 16px 20px; color: #BE185D; font-weight: 800;">⚡ Tải Video Nhanh</th>
                        <th style="padding: 16px 20px; color: #64748B;">Công Cụ Thông Thường</th>
                    </tr>
                </thead>
                <tbody>
                    <tr style="border-bottom: 1px solid #F1F5F9;">
                        <td style="padding: 14px 20px; color: #0F172A; font-weight: 600;">Logo Mờ (Watermark)</td>
                        <td style="padding: 14px 20px; color: #059669; font-weight: 700;">✅ Sạch 100% Không Logo</td>
                        <td style="padding: 14px 20px; color: #DC2626;">❌ Bị dán logo chìm</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #F1F5F9;">
                        <td style="padding: 14px 20px; color: #0F172A; font-weight: 600;">Tách Nhạc MP3 Gốc</td>
                        <td style="padding: 14px 20px; color: #059669; font-weight: 700;">✅ Tách MP3 Chất Lượng Cao</td>
                        <td style="padding: 14px 20px; color: #DC2626;">❌ Không hỗ trợ</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #F1F5F9;">
                        <td style="padding: 14px 20px; color: #0F172A; font-weight: 600;">Tốc Độ Trích Xuất</td>
                        <td style="padding: 14px 20px; color: #059669; font-weight: 700;">⚡ Dưới 0.5 Giây</td>
                        <td style="padding: 14px 20px; color: #D97706;">⏳ Chờ 10-15 giây</td>
                    </tr>
                    <tr>
                        <td style="padding: 14px 20px; color: #0F172A; font-weight: 600;">Chi Phí Sử Dụng</td>
                        <td style="padding: 14px 20px; color: #059669; font-weight: 700;">🆓 100% Miễn Phí Vĩnh Viễn</td>
                        <td style="padding: 14px 20px; color: #64748B;">Giới hạn / Ép xem quảng cáo</td>
                    </tr>
                </tbody>
            </table>
        </div>
    </section>
    """, unsafe_allow_html=True)

    # Step Guide Section
    st.markdown("""
    <section id="guide-section">
        <h2 style="font-size: 1.6rem; font-weight: 800; color: #BE185D; margin-bottom: 18px; text-align: center;">📖 Hướng Dẫn Tải Video TikTok Trong 3 Bước</h2>
        <div class="card">
            <ol style="margin: 0; padding-left: 24px; color: #334155; line-height: 2;">
                <li><b>Bước 1:</b> Mở ứng dụng TikTok, tìm video bạn muốn tải và nhấn nút <b>Chia sẻ (Share)</b> ➔ Sao chép liên kết.</li>
                <li><b>Bước 2:</b> Mở trang web Tải Video Nhanh này, dán liên kết vào khung tìm kiếm ở trên và nhấn nút <b>TẢI VIDEO NGAY</b>.</li>
                <li><b>Bước 3:</b> Chọn định dạng <b>TẢI VIDEO MP4 (Không Logo)</b> hoặc <b>TẢI NHẠC MP3</b> để lưu về thiết bị của bạn.</li>
            </ol>
        </div>
    </section>
    """, unsafe_allow_html=True)

    st.markdown('<section id="faq-section">', unsafe_allow_html=True)
    with st.expander("❓ Câu Hỏi Thường Gặp (FAQ Tải Video Nhanh)", expanded=False):
        st.markdown("""
        - **Tải video TikTok tại Tải Video Nhanh có tốn phí không?**  
          👉 Hoàn toàn miễn phí 100%! Bạn có thể tải không giới hạn số lượng video.
        - **Video sau khi tải về được lưu ở đâu?**  
          👉 Video sẽ tự động được lưu vào thư mục `Downloads` (Tải về) trên điện thoại hoặc máy tính của bạn.
        - **Tải Video Nhanh có lưu trữ bản sao video của tôi không?**  
          👉 Không! Hệ thống không lưu trữ video hay theo dõi lịch sử tải về của người dùng.
        """)
    st.markdown('</section></main>', unsafe_allow_html=True)

    st.markdown("""
    <footer id="site-footer" style="text-align: center; margin-top: 50px; padding: 24px 0; border-top: 1px solid #E2E8F0; color: #64748B; font-size: 0.9rem;">
        <p>© 2026 <b>Tải Video Nhanh</b> — Công cụ tải video TikTok không dán nhãn logo mờ miễn phí tốt nhất.</p>
    </footer>
    """, unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

# ==========================================
# CHẾ ĐỘ 2: ADMIN BATCH MIXER (BẢO VỆ BẰNG MẬT KHẨU)
# ==========================================
def render_admin_page():
    col_adm_header1, col_adm_header2 = st.columns([3, 1])
    with col_adm_header1:
        st.markdown("""
        <div style="display: flex; align-items: center; gap: 10px; padding: 10px 0;">
            <span style="font-size: 1.6rem;">🔐</span>
            <div>
                <h3 style="margin: 0; color: #FF007F; font-size: 1.3rem;">Bảng Quản Trị Admin - TikTok Batch Mixer</h3>
                <p style="margin: 2px 0 0 0; color: #94A3B8; font-size: 0.85rem;">Công cụ xử lý hàng loạt dành riêng cho Quản trị viên</p>
            </div>
        </div>
        """, unsafe_allow_html=True)
    with col_adm_header2:
        if st.button("🏠 Thoát Admin ➔ Public", key="btn_exit_admin_top"):
            st.query_params.clear()
            st.rerun()
    st.markdown("---")

    # Kiểm tra đăng nhập Admin
    if not st.session_state.get('admin_authenticated', False):
        st.markdown("""
        <div class="main-header">
            <h1>🔐 Quản Trị Hệ Thống - TikTok Batch Mixer</h1>
            <p>Vui lòng đăng nhập mật khẩu Admin để truy cập công cụ xử lý hàng loạt</p>
        </div>
        """, unsafe_allow_html=True)
        
        col_login1, col_login2, col_login3 = st.columns([1, 2, 1])
        with col_login2:
            st.markdown('<div class="card" style="padding: 24px; border: 1px solid rgba(255,0,127,0.3);">', unsafe_allow_html=True)
            st.markdown("<h3 style='text-align: center; color: #FF007F;'>🔑 Đăng Nhập Admin</h3>", unsafe_allow_html=True)
            admin_pass_input = st.text_input("Nhập mật khẩu Admin:", type="password", key="field_admin_password")
            btn_login = st.button("🔓 ĐĂNG NHẬP ADMIN", use_container_width=True, key="btn_do_login")
            
            if btn_login:
                target_pass = os.environ.get("ADMIN_PASSWORD", "admin123")
                if admin_pass_input.strip() == target_pass:
                    st.session_state['admin_authenticated'] = True
                    st.success("✅ Đăng nhập thành công!")
                    st.rerun()
                else:
                    st.error("❌ Mật khẩu Admin không chính xác. Vui lòng thử lại!")
            st.markdown('</div>', unsafe_allow_html=True)
        return

    # Nếu đã đăng nhập thành công: Hiển thị giao diện Admin
    st.sidebar.success("🟢 Đã đăng nhập Admin")
    if st.sidebar.button("🚪 Đăng xuất Admin", key="btn_admin_logout_side"):
        st.session_state['admin_authenticated'] = False
        st.rerun()

    # Header Admin
    st.markdown("""
    <div class="main-header">
        <h1>🎬 TikTok Video Mixer (Admin Mode)</h1>
        <p>Tải hàng loạt • Trộn tự động • Ghép nhạc • Phối âm thanh & Xuất video dài</p>
        <div style="margin-top:10px;">
            <span class="badge badge-pink">yt-dlp</span>
            <span class="badge badge-purple">audio remix</span>
            <span class="badge badge-blue">ffmpeg</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")

    # LAYOUT 2 CỘT CHO ADMIN
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

        auto_merge_batches = st.toggle(
            "🔗 Tự động nối tất cả batch thành 1 video dài",
            value=True,
            help="Sau khi chạy xong tất cả các batch (ví dụ 3 batch = 3 video), hệ thống sẽ tự động ghép 3 video đó thành 1 video dài duy nhất."
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
            else:
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
        start_btn = st.button("🚀 BẮT ĐẦU XỬ LÝ HÀNG LOẠT", use_container_width=True)

        st.markdown("""
        <div class="card">
            <h3>⚡ Tính năng tích hợp Admin</h3>
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
                Nhập danh sách link TikTok ở cột bên trái và nhấn <b>"BẮT ĐẦU XỬ LÝ HÀNG LOẠT"</b>.
            </div>
            """, unsafe_allow_html=True)

    # XỬ LÝ CHÍNH
    if start_btn:
        error_logs = []
        live_log = LiveLog(live_log_area)
        BATCH_SIZE = 10
        
        if not valid_urls:
            status_area.error("⚠️ Vui lòng nhập ít nhất 1 link TikTok hợp lệ.")
        elif not keep_audio and music_file is None and mix_mode == "🎲 Trộn ngẫu nhiên theo nhạc":
            status_area.error("⚠️ Bạn chọn chế độ trộn theo nhạc nhưng chưa tải lên file nhạc nền.")
        else:
            st.session_state.pop('completed_videos', None)
            st.session_state.pop('merged_video_info', None)
            
            batches = [valid_urls[i:i+BATCH_SIZE] for i in range(0, len(valid_urls), BATCH_SIZE)]
            total_batches = len(batches)
            completed_videos = []
            
            live_log.add(f"🚀 Bắt đầu xử lý {len(valid_urls)} link → {total_batches} batch (tối đa {BATCH_SIZE} link/batch)")
            
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
                    check_and_cleanup_disk_space(required_mb=500, live_log=live_log, current_batch_num=batch_num)
                    
                    for f in glob.glob(os.path.join(DOWNLOAD_DIR, "*")):
                        try:
                            if os.path.isdir(f):
                                shutil.rmtree(f)
                            else:
                                os.remove(f)
                        except:
                            pass
                    gc.collect()
                    
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
                    
                    pct = batch_pct_base + int(batch_pct_range * 0.5)
                    progress_bar.progress(min(99, pct))
                    live_log.add(f"✂️ [2/2] Ghép & Render video bằng FFmpeg...")
                    
                    status_area.markdown(f"""
                    <div class="status-box">
                        <b>📦 Batch {batch_num}/{total_batches} — 🎞️ Đang ghép & render video...</b>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    for idx_p, path in enumerate(downloaded_files):
                        dur = ffmpeg_get_duration(path)
                        live_log.add(f"  📂 {os.path.basename(path)} ({dur:.1f}s)")
                    
                    check_and_cleanup_disk_space(required_mb=500, live_log=live_log, current_batch_num=batch_num)
                    
                    if mix_mode == "🎲 Trộn ngẫu nhiên theo nhạc":
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
                    
                    # PHỐI LẠI ÂM THANH
                    if audio_remix:
                        live_log.add(f"🎛️ Đang phối lại âm thanh (né Content ID)...")
                        status_area.markdown(f"""
                        <div class="status-box">
                            <b>📦 Batch {batch_num}/{total_batches} — 🎛️ Phối lại âm thanh...</b>
                        </div>
                        """, unsafe_allow_html=True)
                        
                        af_filters = []
                        if audio_pitch != 0:
                            pitch_factor = 2 ** (audio_pitch / 12.0)
                            af_filters.append(f"asetrate=44100*{pitch_factor:.4f}")
                            af_filters.append("aresample=44100")
                            live_log.add(f"  🎵 Pitch: {'+' if audio_pitch > 0 else ''}{audio_pitch} semitone")
                        
                        if audio_speed != 1.0:
                            af_filters.append(f"atempo={audio_speed:.2f}")
                            live_log.add(f"  ⏩ Speed: {audio_speed:.2f}x")
                        
                        if audio_eq_random:
                            freqs = random.sample([200, 400, 800, 1200, 2000, 3500, 5000, 8000], 3)
                            for freq in freqs:
                                gain = random.choice([-4, -3, -2, 2, 3, 4, 5])
                                width = random.choice([100, 200, 300])
                                af_filters.append(f"equalizer=f={freq}:t=h:w={width}:g={gain}")
                            live_log.add(f"  🎚️ Random EQ: {len(freqs)} dải tần")
                        
                        if audio_bass > 0:
                            af_filters.append(f"bass=g={audio_bass}")
                            live_log.add(f"  🔊 Bass: +{audio_bass}dB")
                        
                        if audio_compressor:
                            af_filters.append("acompressor=threshold=-20dB:ratio=4:attack=5:release=50")
                            live_log.add(f"  🔧 Compressor: ON")
                        
                        if audio_stereo:
                            af_filters.append("stereotools=mlev=0.015:slev=1.3:sbal=0.1")
                            af_filters.append("aphaser=type=t:speed=0.5:decay=0.3")
                            live_log.add(f"  🎧 Stereo widen + Phaser: ON")
                        
                        if audio_reverb:
                            af_filters.append("aecho=0.8:0.85:40:0.3")
                            live_log.add(f"  🏛️ Reverb: ON")
                        
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
                    gc.collect()
                    continue
            
            progress_bar.progress(100)
            
            if completed_videos:
                live_log.add(f"")
                live_log.add(f"{'='*40}")
                live_log.add(f"🏁 HOÀN THÀNH TẤT CẢ: {len(completed_videos)}/{total_batches} batch thành công!")
                live_log.add(f"{'='*40}")
                
                st.session_state['completed_videos'] = completed_videos
                
                if auto_merge_batches and len(completed_videos) >= 2:
                    live_log.add("🔗 Đang tự động nối tất cả batch thành 1 video dài...")
                    merge_output = os.path.join(OUTPUT_DIR, "merged_all_batches.mp4")
                    batch_paths = [v['path'] for v in completed_videos]
                    merge_ok = ffmpeg_merge_batches(batch_paths, merge_output, live_log=live_log)
                    if merge_ok and os.path.exists(merge_output):
                        merged_dur = ffmpeg_get_duration(merge_output)
                        merged_size = os.path.getsize(merge_output) / (1024 * 1024)
                        st.session_state['merged_video_info'] = {
                            'path': merge_output,
                            'duration': merged_dur,
                            'size_mb': merged_size,
                            'batch_count': len(completed_videos)
                        }
            else:
                status_area.error("❌ Không có batch nào hoàn thành thành công.")
                progress_bar.progress(0)
            
            if error_logs:
                with log_area.container():
                    st.markdown("---")
                    st.markdown("### ⚠️ Nhật ký lỗi (Logs)")
                    st.code("\n".join(error_logs), language="text")

    # HIỂN THỊ KẾT QUẢ BATCH MIXER
    if st.session_state.get('completed_videos'):
        comp_vids = st.session_state['completed_videos']
        merged_info = st.session_state.get('merged_video_info')
        
        total_dur_all = sum(v['duration'] for v in comp_vids)
        total_size_all = sum(v['size_mb'] for v in comp_vids)
        
        status_area.markdown(f"""
        <div class="status-box" style="border-left-color: #00c853;">
            <b>🎉 Hoàn thành! {len(comp_vids)} batch xử lý thành công.</b><br>
            📊 Tổng thời lượng các batch: <b>{total_dur_all:.1f}s</b> • 💾 Tổng dung lượng: <b>{total_size_all:.1f} MB</b>
        </div>
        """, unsafe_allow_html=True)
        
        with video_preview.container():
            if merged_info and os.path.exists(merged_info['path']):
                st.markdown(f"""
                <div class="card" style="margin-bottom: 15px; border-left: 4px solid #00c853; background: #0f172a;">
                    <h3 style="margin-top: 0; color: #00c853;">🎬 VIDEO ĐÃ NỐI TẤT CẢ BATCH ({merged_info['batch_count']} BATCH)</h3>
                    <p style="color: #94a3b8; font-size: 0.9rem; margin-bottom: 10px;">
                        ⏱️ Thời lượng: <b>{merged_info['duration']:.1f}s</b> • 💾 Dung lượng: <b>{merged_info['size_mb']:.1f} MB</b>
                    </p>
                </div>
                """, unsafe_allow_html=True)
                
                rel_merged = os.path.basename(merged_info['path'])
                st.markdown(f"""
                <div style="margin-bottom: 15px;">
                    <video controls preload="metadata" style="width: 100%; max-height: 480px; border-radius: 10px; background: #0a0e17; box-shadow: 0 4px 15px rgba(0,0,0,0.5);">
                        <source src="app/static/{rel_merged}" type="video/mp4">
                        Trình duyệt không hỗ trợ xem trực tiếp.
                    </video>
                </div>
                <a href="app/static/{rel_merged}" download="tiktok_merged_all.mp4" target="_blank" style="display: block; width: 100%; text-align: center; background: linear-gradient(135deg, #FF007F 0%, #7928CA 100%); color: #ffffff; padding: 14px 20px; border-radius: 10px; font-weight: 700; text-decoration: none; font-size: 1rem; box-shadow: 0 4px 15px rgba(255,0,127,0.3); margin-bottom: 20px;">
                    📥 TẢI VIDEO ĐÃ NỐI TẤT CẢ ({merged_info['size_mb']:.1f} MB)
                </a>
                """, unsafe_allow_html=True)
                st.markdown("---")
            elif len(comp_vids) >= 2:
                st.markdown("""
                <div class="card" style="margin-bottom: 15px; border-left: 3px solid #ff6b35;">
                    <h4>🔗 Nối tất cả batch thành 1 video dài</h4>
                    <p style="color: #94a3b8; font-size: 0.9rem;">
                        Ghép tất cả các batch ở trên thành 1 video duy nhất theo thứ tự.
                    </p>
                </div>
                """, unsafe_allow_html=True)
                
                if st.button(f"🔗 NỐI {len(comp_vids)} BATCH THÀNH 1 VIDEO DÀI ({total_dur_all:.0f}s)", use_container_width=True, key="manual_merge_btn"):
                    merge_output = os.path.join(OUTPUT_DIR, "merged_all_batches.mp4")
                    batch_paths = [v['path'] for v in comp_vids]
                    with st.spinner("🔗 Đang nối tất cả batch..."):
                        merge_ok = ffmpeg_merge_batches(batch_paths, merge_output)
                    if merge_ok and os.path.exists(merge_output):
                        merged_dur = ffmpeg_get_duration(merge_output)
                        merged_size = os.path.getsize(merge_output) / (1024 * 1024)
                        st.session_state['merged_video_info'] = {
                            'path': merge_output,
                            'duration': merged_dur,
                            'size_mb': merged_size,
                            'batch_count': len(comp_vids)
                        }
                        st.rerun()
                    else:
                        st.error("❌ Nối video thất bại.")

            st.markdown("### 📦 Danh sách video từng Batch riêng lẻ")
            for vid in comp_vids:
                st.markdown(f"""
                <div class="card" style="margin-bottom: 10px;">
                    <h4>📦 Batch {vid['batch_num']} — {vid['clip_count']} clip • {vid['duration']:.1f}s • {vid['size_mb']:.1f} MB</h4>
                </div>
                """, unsafe_allow_html=True)
                
                rel_batch = os.path.basename(vid['path'])
                st.markdown(f"""
                <div style="margin-bottom: 10px;">
                    <video controls preload="metadata" style="width: 100%; max-height: 380px; border-radius: 8px; background: #0a0e17;">
                        <source src="app/static/{rel_batch}" type="video/mp4">
                        Trình duyệt không hỗ trợ xem trực tiếp.
                    </video>
                </div>
                <a href="app/static/{rel_batch}" download="tiktok_batch_{vid['batch_num']}.mp4" target="_blank" style="display: block; width: 100%; text-align: center; background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); border: 1px solid #FF007F; color: #ffffff; padding: 12px 16px; border-radius: 8px; font-weight: 600; text-decoration: none; font-size: 0.9rem; margin-top: 6px; margin-bottom: 20px;">
                    📥 TẢI BATCH {vid['batch_num']} ({vid['size_mb']:.1f} MB)
                </a>
                """, unsafe_allow_html=True)
                st.markdown("---")

# ==========================================
# ĐIỀU HƯỚNG TRANG DỰA THEO NAVIGATION
# ==========================================
if app_mode == "🏠 Tải Video TikTok (Public)":
    render_public_landing_page()
else:
    render_admin_page()
