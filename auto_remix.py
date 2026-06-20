import os
import sys
import random
import glob
from yt_dlp import YoutubeDL
from moviepy.editor import VideoFileClip, concatenate_videoclips, AudioFileClip
from moviepy.audio.fx.all import audio_loop

# ==========================================
# CẤU HÌNH TOOL (BẠN CÓ THỂ THAY ĐỔI TẠI ĐÂY)
# ==========================================
MIX_MODE = "sequential"         # "sequential": Ghép nối tiếp nguyên bản toàn bộ video theo thứ tự
                                # "random_segments": Cắt ghép ngẫu nhiên các đoạn ngắn khớp với thời lượng nhạc

KEEP_ORIGINAL_AUDIO = True      # True: Giữ lại âm thanh gốc của video TikTok
                                # False: Tắt âm thanh gốc và chèn nhạc nền mới (music.mp3)

DOWNLOAD_DIR = "./downloaded_videos"
OUTPUT_FILE = "final_music_video.mp4"
BACKGROUND_MUSIC = "music.mp3"
URLS_FILE = "urls.txt"
SEGMENT_DURATION = 3.0          # Chỉ dùng khi chọn MIX_MODE = "random_segments"

NUM_CHANNEL_VIDEOS = 10         # Số lượng video mới nhất cần tải từ mỗi link kênh TikTok
# ==========================================

def setup_directories():
    """Tạo các thư mục cần thiết."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def download_tiktok_videos(urls_file):
    """Tải video TikTok từ danh sách URL trong urls_file sử dụng yt-dlp."""
    if not os.path.exists(urls_file):
        with open(urls_file, "w", encoding="utf-8") as f:
            f.write("# Dan link TikTok vao day (moi dong 1 link)\n")
            f.write("# Vi du:\n")
            f.write("# https://www.tiktok.com/@username/video/1234567890\n")
        print(f"[*] Da tao file danh sach '{urls_file}'. Vui long them link TikTok vao file nay.")
        return []

    urls = []
    with open(urls_file, "r", encoding="utf-8") as f:
        for line in f.readlines():
            line_str = line.strip()
            if line_str and not line_str.startswith("#"):
                if "?" in line_str:
                    line_str = line_str.split("?")[0]
                urls.append(line_str)

    if not urls:
        print("[!] File urls.txt trong. Vui long them link TikTok de tai.")
        return []

    print(f"[*] Tim thay {len(urls)} link video trong file. Bat dau tai...")
    
    ydl_opts = {
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
        'format': 'bestvideo+bestaudio/best',
        'referer': 'https://www.tiktok.com/',
        'headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
        },
        'quiet': False,
        'no_warnings': True,
    }

    with YoutubeDL(ydl_opts) as ydl:
        for idx, url in enumerate(urls, 1):
            try:
                if "@" in url and "/video/" not in url:
                    print(f"\n[*] Dang tai {NUM_CHANNEL_VIDEOS} video moi nhat tu kenh {idx}/{len(urls)}: {url}")
                    channel_opts = ydl_opts.copy()
                    channel_opts['playlist_items'] = f'1-{NUM_CHANNEL_VIDEOS}'
                    with YoutubeDL(channel_opts) as ydl_channel:
                        ydl_channel.download([url])
                else:
                    print(f"\n[*] Dang tai video {idx}/{len(urls)}: {url}")
                    ydl.download([url])
            except Exception as e:
                print(f"[!] Tai video that bai {url}: {e}")

    downloaded_files = glob.glob(os.path.join(DOWNLOAD_DIR, "*.mp4"))
    print(f"\n[*] Tai hoan tat. Co {len(downloaded_files)} video san sang.")
    return downloaded_files

def mix_videos_sequential(video_paths, music_path, output_path, keep_audio):
    """Ghép nối tiếp nguyên bản toàn bộ video theo thứ tự tải về."""
    if not video_paths:
        print("[!] Khong co video de ghep.")
        return

    # Sắp xếp video theo thứ tự bảng chữ cái/thời gian tải về để đảm bảo đúng thứ tự
    video_paths.sort()

    print("[*] Dang chuan bi ghep video noi tiep nguyen ban...")
    clips = []
    video_clips_opened = []

    for path in video_paths:
        try:
            clip = VideoFileClip(path)
            # Lật hình ngang chống quét bản quyền
            clip_modified = clip.fl_image(lambda frame: frame[:, ::-1])
            
            # Xử lý âm thanh
            if not keep_audio:
                clip_modified = clip_modified.without_audio()
                
            clips.append(clip_modified)
            video_clips_opened.append(clip)
        except Exception as e:
            print(f"[!] Khong the mo file {path}: {e}")

    if not clips:
        print("[!] Khong co video nao mo thanh cong.")
        return

    try:
        print("[*] Dang ghep lien mach cac video...")
        final_video = concatenate_videoclips(clips, method="chain")
            
        total_duration = final_video.duration
        print(f"[*] Tong thoi luong video sau khi ghep: {total_duration:.2f} giay.")

        # Xử lý lồng nhạc nền nếu tắt âm thanh gốc
        if not keep_audio:
            if os.path.exists(music_path):
                print(f"[*] Dang long nhac nen '{music_path}'...")
                music_clip = AudioFileClip(music_path)
                
                if music_clip.duration < total_duration:
                    print("[*] Nhac nen ngan hon video, tien hanh lap lai nhac cho khop...")
                    looped_music = audio_loop(music_clip, duration=total_duration)
                    final_video = final_video.set_audio(looped_music)
                else:
                    final_video = final_video.set_audio(music_clip.set_duration(total_duration))
            else:
                print(f"[!] Canh bao: Khong tim thay file nhac nen '{music_path}'. Video xuat ra se khong co am thanh.")
        
        render_threads = 2  # Giới hạn 2 threads cho server 2GB RAM
        print(f"[*] Dang render video dau ra: {output_path} (threads={render_threads}, preset=ultrafast)...")
        final_video.write_videofile(
            output_path,
            fps=24,
            codec="libx264",
            audio_codec="aac" if not keep_audio or os.path.exists(music_path) else None,
            threads=render_threads,
            preset='ultrafast'
        )
        print(f"[+] Render video hoan tat: {output_path}")
        final_video.close()
        
    except Exception as e:
        print(f"[!] Xay ra loi trong qua trinh render: {e}")
    finally:
        for clip in video_clips_opened:
            try:
                clip.close()
            except:
                pass

def mix_videos_random_segments(video_paths, music_path, output_path, segment_duration):
    """Cắt ghép trộn ngẫu nhiên các phân đoạn ngắn để khớp với thời lượng nhạc."""
    if not video_paths:
        print("[!] Khong co video de tron.")
        return

    if not os.path.exists(music_path):
        print(f"[!] File nhac nen '{music_path}' khong ton tai. Vui long chuan bi file nhac nay.")
        return

    print(f"[*] Dang tai nhac nen '{music_path}'...")
    try:
        music_clip = AudioFileClip(music_path)
        total_duration = music_clip.duration
        print(f"[*] Nhac nen co thoi luong: {total_duration:.2f} giay.")
    except Exception as e:
        print(f"[!] Khong the tai nhac nen: {e}")
        return

    clips = []
    current_duration = 0.0
    video_clips_opened = []

    print("[*] Bat dau cat ghep va tron video...")
    try:
        loaded_videos = []
        for path in video_paths:
            try:
                clip = VideoFileClip(path)
                if clip.duration > segment_duration:
                    loaded_videos.append(clip)
                    video_clips_opened.append(clip)
            except Exception as e:
                print(f"[!] Khong the mo file {path}: {e}")

        if not loaded_videos:
            print("[!] Khong co video nao du thoi luong >= segment_duration.")
            music_clip.close()
            return

        while current_duration < total_duration:
            video = random.choice(loaded_videos)
            start_time = random.uniform(0, video.duration - segment_duration)
            end_time = start_time + segment_duration
            
            sub_clip = video.subclip(start_time, end_time)
            sub_clip = sub_clip.without_audio()
            
            # Lật ngang hình ảnh ngẫu nhiên 50%
            if random.choice([True, False]):
                sub_clip = sub_clip.fl_image(lambda frame: frame[:, ::-1])
            
            clips.append(sub_clip)
            current_duration += segment_duration

        print(f"[*] Da cat ghep xong {len(clips)} phan doan. Dang ghep video lien mach...")
        final_video = concatenate_videoclips(clips, method="chain")
            
        final_video = final_video.set_duration(total_duration)
        final_video = final_video.set_audio(music_clip)
        
        render_threads = 2  # Giới hạn 2 threads cho server 2GB RAM
        print(f"[*] Dang render video dau ra: {output_path} (threads={render_threads}, preset=ultrafast)...")
        final_video.write_videofile(
            output_path,
            fps=24,
            codec="libx264",
            audio_codec="aac",
            threads=render_threads,
            preset='ultrafast'
        )
        print(f"[+] Render video hoan tat: {output_path}")
        final_video.close()
        
    except Exception as e:
        print(f"[!] Xay ra loi trong qua trinh render: {e}")
    finally:
        music_clip.close()
        for clip in clips:
            try:
                clip.close()
            except:
                pass
        for clip in video_clips_opened:
            try:
                clip.close()
            except:
                pass

if __name__ == "__main__":
    setup_directories()
    
    # 1. Tải video
    video_files = download_tiktok_videos(URLS_FILE)
    
    if not video_files:
        print("[!] Vui long them url vao urls.txt de bat dau.")
        sys.exit(0)
        
    # 2. Xử lý ghép video theo chế độ được chọn
    if MIX_MODE == "sequential":
        mix_videos_sequential(video_files, BACKGROUND_MUSIC, OUTPUT_FILE, KEEP_ORIGINAL_AUDIO)
    elif MIX_MODE == "random_segments":
        mix_videos_random_segments(video_files, BACKGROUND_MUSIC, OUTPUT_FILE, SEGMENT_DURATION)
    else:
        print("[!] MIX_MODE khong hop le. Vui long chon 'sequential' hoac 'random_segments'.")
