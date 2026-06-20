import os
import glob
import random
import tempfile
import streamlit as st
from yt_dlp import YoutubeDL
from moviepy.editor import VideoFileClip, concatenate_videoclips, AudioFileClip
from moviepy.audio.fx.all import audio_loop
import time

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
DOWNLOAD_DIR = tempfile.mkdtemp(prefix="tiktok_dl_")
OUTPUT_DIR = tempfile.mkdtemp(prefix="tiktok_out_")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "final_music_video.mp4")

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
    border-radius: 16px;
    padding: 24px;
    margin-bottom: 16px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.3);
}
.card h3 {
    margin-top: 0;
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
    <div class="card">
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
            if "?" in u:
                u = u.split("?")[0]
            valid_urls.append(u)
            
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
        if cleaned_channel_url:
            if "?" in cleaned_channel_url:
                cleaned_channel_url = cleaned_channel_url.split("?")[0]
            valid_urls = [cleaned_channel_url]
            st.caption(f"✅ Đã nhận kênh: **{cleaned_channel_url}** (Sẽ lấy **{num_channel_videos}** bài mới nhất)")
        else:
            st.caption("⏳ Chưa nhập link kênh")

    st.markdown("")

    # Cấu hình
    st.markdown("""
    <div class="card">
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

    # Hiệu ứng chuyển cảnh
    st.markdown("---")
    st.caption("🎬 **Hiệu ứng chuyển cảnh (Transitions)**")
    transition_mode = st.selectbox(
        "Chọn hiệu ứng chuyển cảnh:",
        ["❌ Không có (None)", "🌸 Hòa tan (Crossfade)", "⚫ Mờ dần qua đen (Fade to Black)"],
        index=0
    )
    transition_duration = st.slider(
        "Thời lượng chuyển cảnh (giây):",
        min_value=0.1, max_value=2.0, value=0.5, step=0.1
    )

    st.markdown("")
    start_btn = st.button("🚀 BẮT ĐẦU XỬ LÝ", use_container_width=True)

    # Tính năng
    st.markdown("""
    <div class="card">
        <h3>⚡ Tính năng tích hợp</h3>
        <div class="feature-grid">
            <div class="feature-item"><div class="icon">🪞</div>Lật hình ngẫu nhiên (Mirror)</div>
            <div class="feature-item"><div class="icon">🔇</div>Tách/Giữ âm thanh gốc</div>
            <div class="feature-item"><div class="icon">🔀</div>Trộn thứ tự ngẫu nhiên</div>
            <div class="feature-item"><div class="icon">🔁</div>Tự động loop nhạc nền</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

with col_right:
    st.markdown("""
    <div class="card">
        <h3>🖥️ Trạng thái & Kết quả</h3>
    </div>
    """, unsafe_allow_html=True)

    status_area = st.empty()
    progress_bar = st.progress(0)
    video_preview = st.empty()
    download_area = st.empty()
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
    if not valid_urls:
        status_area.error("⚠️ Vui lòng nhập ít nhất 1 link TikTok hợp lệ.")
    elif not keep_audio and music_file is None and mix_mode == "🎲 Trộn ngẫu nhiên theo nhạc":
        status_area.error("⚠️ Bạn chọn chế độ trộn theo nhạc nhưng chưa tải lên file nhạc nền.")
    else:
        try:
            # Dọn dẹp thư mục tạm cũ
            for f in glob.glob(os.path.join(DOWNLOAD_DIR, "*")):
                os.remove(f)
            if os.path.exists(OUTPUT_FILE):
                os.remove(OUTPUT_FILE)

            # Lưu nhạc nền tạm nếu có upload
            music_path = None
            if not keep_audio and music_file:
                music_path = os.path.join(OUTPUT_DIR, "temp_music.mp3")
                with open(music_path, "wb") as f:
                    f.write(music_file.getbuffer())

            # ========== BƯỚC 1: TẢI VIDEO ==========
            status_area.markdown(f"""
            <div class="status-box">
                <b>⬇️ [Bước 1/3] Đang tải {len(valid_urls)} video từ TikTok...</b>
            </div>
            """, unsafe_allow_html=True)
            progress_bar.progress(5)

            ydl_opts = {
                'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
                'format': 'bestvideo+bestaudio/best',
                'referer': 'https://www.tiktok.com/',
                'merge_output_format': 'mp4',
                'headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                },
                'quiet': True,
                'no_warnings': True,
            }

            success_count = 0
            fail_count = 0
            with YoutubeDL(ydl_opts) as ydl:
                for i, url in enumerate(valid_urls):
                    pct = 5 + int((i / len(valid_urls)) * 40)
                    progress_bar.progress(pct)
                    
                    download_success = False
                    last_error = ""
                    try_count = 3  # 1 lần đầu + 2 lần retry
                    
                    for attempt in range(1, try_count + 1):
                        attempt_str = f" (Thử lại lần {attempt-1})" if attempt > 1 else ""
                        status_area.markdown(f"""
                        <div class="status-box">
                            <b>⬇️ [Bước 1/3] Đang tải video {i+1}/{len(valid_urls)}{attempt_str}...</b><br>
                            <span class="url">{url}</span>
                        </div>
                        """, unsafe_allow_html=True)
                        try:
                            # Kiem tra neu la link kenh (chua @ va khong co /video/)
                            if "@" in url and "/video/" not in url:
                                channel_opts = ydl_opts.copy()
                                channel_opts['playlist_items'] = f'1-{num_channel_videos}'
                                with YoutubeDL(channel_opts) as ydl_channel:
                                    ydl_channel.download([url])
                            else:
                                ydl.download([url])
                            download_success = True
                            success_count += 1
                            break
                        except Exception as e:
                            last_error = str(e)
                            if attempt < try_count:
                                time.sleep(1.5)
                                
                    if not download_success:
                        fail_count += 1
                        error_logs.append(f"❌ Video {i+1} ({url}): {last_error}")
                        st.toast(f"⚠️ Lỗi tải video {i+1} (Thử {try_count} lần đều thất bại)")

            downloaded_files = sorted(glob.glob(os.path.join(DOWNLOAD_DIR, "*.mp4")))

            if not downloaded_files:
                status_area.error(f"❌ Không tải thành công video nào ({fail_count} lỗi). Kiểm tra lại link TikTok.")
                progress_bar.progress(0)
                st.stop()

            progress_bar.progress(45)
            status_area.markdown(f"""
            <div class="status-box">
                <b>✅ Tải xong! {success_count} thành công, {fail_count} lỗi.</b>
            </div>
            """, unsafe_allow_html=True)

            # ========== BƯỚC 2: XỬ LÝ & GHÉP VIDEO ==========
            status_area.markdown("""
            <div class="status-box">
                <b>✂️ [Bước 2/3] Đang xử lý hình ảnh & ghép video...</b>
            </div>
            """, unsafe_allow_html=True)
            progress_bar.progress(50)

            video_clips_opened = []

            if mix_mode == "🔗 Ghép nối tiếp nguyên bản":
                clips = []
                for path in downloaded_files:
                    try:
                        clip = VideoFileClip(path)
                        video_clips_opened.append(clip)

                        # Lật hình ngẫu nhiên 50% để chống quét bản quyền
                        if random.choice([True, False]):
                            clip_mod = clip.fx(lambda c: c.image_transform(lambda im: im[:, ::-1]))
                        else:
                            clip_mod = clip

                        if not keep_audio:
                            clip_mod = clip_mod.without_audio()

                        clips.append(clip_mod)
                    except Exception as e:
                        err_msg = f"⚠️ Bỏ qua file lỗi {os.path.basename(path)}: {str(e)}"
                        error_logs.append(err_msg)
                        st.toast(err_msg[:80])

                if not clips:
                    status_area.error("❌ Không mở được video nào để ghép.")
                    st.stop()

                # Tính toán thời lượng transition thực tế để tránh crash
                actual_trans_duration = 0.0
                if transition_mode != "❌ Không có (None)" and transition_duration > 0:
                    min_clip_dur = min([c.duration for c in clips]) if clips else 0
                    actual_trans_duration = min(transition_duration, min_clip_dur / 2)
                    st.toast(f"ℹ️ Thời lượng chuyển cảnh thực tế: {actual_trans_duration:.2f}s")

                # Áp dụng chuyển cảnh
                if transition_mode == "🌸 Hòa tan (Crossfade)" and actual_trans_duration > 0:
                    processed_clips = []
                    for i, clip in enumerate(clips):
                        if i == 0:
                            processed_clips.append(clip)
                        else:
                            processed_clips.append(clip.crossfadein(actual_trans_duration))
                    final_video = concatenate_videoclips(processed_clips, padding=-actual_trans_duration, method="compose")
                elif transition_mode == "⚫ Mờ dần qua đen (Fade to Black)" and actual_trans_duration > 0:
                    processed_clips = []
                    for clip in clips:
                        faded = clip.fadein(actual_trans_duration).fadeout(actual_trans_duration)
                        processed_clips.append(faded)
                    final_video = concatenate_videoclips(processed_clips, method="compose")
                else:
                    final_video = concatenate_videoclips(clips, method="compose")

                total_dur = final_video.duration

                # Ghép nhạc nền nếu tắt âm thanh gốc
                if not keep_audio and music_path and os.path.exists(music_path):
                    mc = AudioFileClip(music_path)
                    if mc.duration < total_dur:
                        looped = audio_loop(mc, duration=total_dur)
                        final_video = final_video.set_audio(looped)
                    else:
                        final_video = final_video.set_audio(mc.set_duration(total_dur))

            else:
                # Chế độ trộn ngẫu nhiên
                if not music_path or not os.path.exists(music_path):
                    status_area.error("❌ Chế độ trộn ngẫu nhiên yêu cầu phải có file nhạc nền.")
                    st.stop()

                mc = AudioFileClip(music_path)
                total_dur = mc.duration

                loaded_videos = []
                for path in downloaded_files:
                    try:
                        clip = VideoFileClip(path)
                        video_clips_opened.append(clip)
                        if clip.duration > segment_duration:
                            loaded_videos.append(clip)
                    except:
                        pass

                if not loaded_videos:
                    status_area.error("❌ Không có video nào đủ dài để cắt phân đoạn.")
                    st.stop()

                # Tính toán thời lượng transition thực tế để tránh crash
                actual_trans_duration = 0.0
                if transition_mode != "❌ Không có (None)" and transition_duration > 0:
                    actual_trans_duration = min(transition_duration, segment_duration / 2)
                    st.toast(f"ℹ️ Thời lượng chuyển cảnh thực tế: {actual_trans_duration:.2f}s")

                clips = []
                current_dur = 0.0
                while current_dur < total_dur:
                    video = random.choice(loaded_videos)
                    start_t = random.uniform(0, video.duration - segment_duration)
                    sub = video.subclip(start_t, start_t + segment_duration).without_audio()
                    if random.choice([True, False]):
                        sub = sub.fx(lambda c: c.image_transform(lambda im: im[:, ::-1]))
                    clips.append(sub)
                    
                    if transition_mode == "🌸 Hòa tan (Crossfade)" and len(clips) > 1:
                        current_dur += (segment_duration - actual_trans_duration)
                    else:
                        current_dur += segment_duration

                if transition_mode == "🌸 Hòa tan (Crossfade)" and actual_trans_duration > 0:
                    processed_clips = []
                    for i, clip in enumerate(clips):
                        if i == 0:
                            processed_clips.append(clip)
                        else:
                            processed_clips.append(clip.crossfadein(actual_trans_duration))
                    final_video = concatenate_videoclips(processed_clips, padding=-actual_trans_duration, method="compose")
                elif transition_mode == "⚫ Mờ dần qua đen (Fade to Black)" and actual_trans_duration > 0:
                    processed_clips = []
                    for clip in clips:
                        faded = clip.fadein(actual_trans_duration).fadeout(actual_trans_duration)
                        processed_clips.append(faded)
                    final_video = concatenate_videoclips(processed_clips, method="compose")
                else:
                    final_video = concatenate_videoclips(clips, method="compose")

                final_video = final_video.set_duration(total_dur)
                final_video = final_video.set_audio(mc)

            # ========== BƯỚC 3: RENDER ==========
            status_area.markdown(f"""
            <div class="status-box">
                <b>🎞️ [Bước 3/3] Đang render video ({total_dur:.1f}s). Vui lòng chờ...</b>
            </div>
            """, unsafe_allow_html=True)
            progress_bar.progress(70)

            final_video.write_videofile(
                OUTPUT_FILE,
                fps=24,
                codec="libx264",
                audio_codec="aac",
                threads=4,
                logger=None
            )

            # Giải phóng bộ nhớ
            final_video.close()
            for c in video_clips_opened:
                try:
                    c.close()
                except:
                    pass

            progress_bar.progress(100)

            # ========== HIỂN THỊ KẾT QUẢ ==========
            status_area.markdown(f"""
            <div class="status-box" style="border-left-color: #00c853;">
                <b>🎉 Hoàn thành! Video đã sẵn sàng.</b><br>
                📊 Đã ghép <b>{len(clips)}</b> clip • Tổng thời lượng: <b>{total_dur:.1f}s</b>
            </div>
            """, unsafe_allow_html=True)

            # Preview video
            with open(OUTPUT_FILE, 'rb') as vf:
                video_bytes = vf.read()
            video_preview.video(video_bytes)

            # Nút download
            download_area.download_button(
                label="📥 TẢI VIDEO VỀ MÁY",
                data=video_bytes,
                file_name="tiktok_compilation.mp4",
                mime="video/mp4",
                use_container_width=True
            )

        except Exception as e:
            status_area.error(f"❌ Lỗi: {e}")
            progress_bar.progress(0)
            error_logs.append(f"❌ Lỗi hệ thống: {str(e)}")
        finally:
            if 'error_logs' in locals() and error_logs:
                with log_area.container():
                    st.markdown("---")
                    st.markdown("### ⚠️ Nhật ký lỗi (Logs)")
                    st.code("\n".join(error_logs), language="text")
