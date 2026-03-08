import os
import uuid
import math
from io import BytesIO
from flask import Flask, request, send_file
from pydub import AudioSegment
from moviepy.editor import VideoFileClip, AudioFileClip
import imageio_ffmpeg

# Configure Pydub to use the ffmpeg installed by moviepy/imageio
AudioSegment.converter = imageio_ffmpeg.get_ffmpeg_exe()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 2000 * 1024 * 1024  # 2GB for video

def percent_to_db(percent):
    if percent <= 0: return -100.0
    if percent == 100: return 0.0
    return 20 * math.log10(percent / 100.0)

@app.route('/')
def index():
    try:
        with open('index.html', 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return "Arquivo index.html não encontrado."

@app.route('/process', methods=['POST'])
def process_media():
    temp_files = []
    try:
        if 'voice' not in request.files:
            return "Arquivo principal faltando", 400

        input_file = request.files['voice']
        filename = input_file.filename.lower()
        is_video = filename.endswith(('.mp4', '.mov', '.avi', '.mkv'))
        
        # Save input to temp file (needed for moviepy)
        unique_id = str(uuid.uuid4())
        input_path = f"temp_input_{unique_id}_{filename}"
        input_file.save(input_path)
        temp_files.append(input_path)

        # 1. PROCESS MEDIA WITH FFMPEG
        import subprocess

        # GET NOISE FILE
        if 'noise' in request.files and request.files['noise'].filename != '':
            noise_file = request.files['noise']
            noise_path = f"temp_noise_{unique_id}_{noise_file.filename}"
            noise_file.save(noise_path)
            temp_files.append(noise_path)
        else:
            # Default background check
            possible_files = ['background.mp3', 'background.wav', 'background.m4a']
            noise_path = None
            for f in possible_files:
                if os.path.exists(f):
                    noise_path = f
                    break
            
            if not noise_path:
                print("Warning: No background file found. Using 1s silence.")
                noise_path = f"temp_silence_{unique_id}.wav"
                ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
                subprocess.run([ffmpeg_exe, "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "1", noise_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                temp_files.append(noise_path)

        # GET GAINS
        try:
            voice_vol_pct = float(request.form.get('voice_volume', 100))
            music_vol_left_pct = float(request.form.get('music_vol_left', 100))
            music_vol_right_pct = float(request.form.get('music_vol_right', 30))
        except ValueError:
            return "Volume inválido", 400

        v_vol = voice_vol_pct / 100.0
        m_l_vol = music_vol_left_pct / 100.0
        m_r_vol = music_vol_right_pct / 100.0

        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        name_base, ext = os.path.splitext(filename)
        out_ext = ext if is_video else ".wav"
        output_path = f"output_media_{unique_id}{out_ext}"
        temp_files.append(output_path)

        filter_complex = f"""
[0:a]aformat=sample_rates=44100:channel_layouts=mono,volume={v_vol},asplit[v_l][v_r];
[v_l]volume=-1[v_l_inv];
[1:a]aformat=sample_rates=44100:channel_layouts=mono,asplit[m_l][m_r];
[m_l]volume={m_l_vol}[m_l_vol];
[m_r]volume={m_r_vol}[m_r_vol];
[v_l_inv][m_l_vol]amix=inputs=2:duration=first:dropout_transition=0,aformat=channel_layouts=mono[out_l];
[v_r][m_r_vol]amix=inputs=2:duration=first:dropout_transition=0,aformat=channel_layouts=mono[out_r];
[out_l][out_r]join=inputs=2:channel_layout=stereo:map=0.0-FL|1.0-FR[a_out]
"""

        cmd = [
            ffmpeg_exe, "-y", 
            "-i", input_path, 
            "-stream_loop", "-1", 
            "-i", noise_path,
            "-filter_complex", filter_complex.strip().replace('\n', ''),
            "-map", "0:v?", "-map", "[a_out]"
        ]

        if is_video:
            cmd.extend(["-c:v", "copy", "-c:a", "aac"])
        else:
            cmd.extend(["-c:a", "pcm_s16le"])
            
        cmd.extend(["-shortest", output_path])

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print("FFmpeg Error:", res.stderr)
            return f"Erro processando media: {res.stderr[-200:]}", 500

        new_filename = f"{name_base} - new{out_ext}"
        mimetype = "video/mp4" if is_video else "audio/wav"

        return send_file(
            output_path, 
            mimetype=mimetype, 
            as_attachment=True, 
            download_name=new_filename
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Erro interno no servidor: {str(e)}", 500
    finally:
        # Cleanup temp files
        for f in temp_files:
            # Try to remove, but sometimes windows holds lock (less likely on mac)
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception as cleanup_error:
                    print(f"Warning: Could not remove temp file {f}: {cleanup_error}")

if __name__ == '__main__':
    print("Iniciando servidor Audio Criativo com suporte a arquivos grandes...")
    print("Acesse http://localhost:5001 no seu navegador.")
    # Use Waitress for production-grade reliability with large file streams
    from waitress import serve
    serve(app, host='0.0.0.0', port=5001, max_request_body_size=2000 * 1024 * 1024, clear_untrusted_proxy_headers=False, threads=16)
