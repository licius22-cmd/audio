import os
import uuid
import math
from io import BytesIO
from flask import Flask, request, send_file
from pydub import AudioSegment
from moviepy.editor import VideoFileClip, AudioFileClip
import imageio_ffmpeg

# Configura o Pydub para usar o ffmpeg do sistema
AudioSegment.converter = imageio_ffmpeg.get_ffmpeg_exe()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # Limite de 500MB

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
        
        unique_id = str(uuid.uuid4())
        input_path = f"temp_input_{unique_id}_{filename}"
        input_file.save(input_path)
        temp_files.append(input_path)

        # 1. PREPARAÇÃO DO ÁUDIO
        if is_video:
            video_clip = VideoFileClip(input_path)
            audio_source_path = f"temp_audio_source_{unique_id}.wav"
            # Extração rápida de áudio
            video_clip.audio.write_audiofile(audio_source_path, logger=None)
            temp_files.append(audio_source_path)
            voice_seg = AudioSegment.from_file(audio_source_path)
        else:
            voice_seg = AudioSegment.from_file(input_path)

        # 2. PREPARAÇÃO DO RUÍDO (Background)
        if 'noise' in request.files and request.files['noise'].filename != '':
            noise_file = request.files['noise']
            noise_path = f"temp_noise_{unique_id}_{noise_file.filename}"
            noise_file.save(noise_path)
            temp_files.append(noise_path)
            noise_seg = AudioSegment.from_file(noise_path)
        else:
            possible_files = ['background.mp3', 'background.wav', 'background.m4a']
            found_bg = next((f for f in possible_files if os.path.exists(f)), None)
            noise_seg = AudioSegment.from_file(found_bg) if found_bg else AudioSegment.silent(duration=1000)

        # 3. PROCESSAMENTO DE FASE
        voice_vol_pct = float(request.form.get('voice_volume', 100))
        music_vol_left_pct = float(request.form.get('music_vol_left', 100))
        music_vol_right_pct = float(request.form.get('music_vol_right', 30))

        if len(noise_seg) < len(voice_seg):
             noise_seg = noise_seg * math.ceil(len(voice_seg) / len(noise_seg))
        noise_seg = noise_seg[:len(voice_seg)]

        voice_mono = voice_seg.set_channels(1)
        noise_channels = noise_seg.split_to_mono()
        noise_l = noise_channels[0]
        noise_r = noise_channels[1] if len(noise_channels) > 1 else noise_channels[0]

        voice_final = voice_mono.apply_gain(percent_to_db(voice_vol_pct))
        noise_l_final = noise_l.apply_gain(percent_to_db(music_vol_left_pct))
        noise_r_final = noise_r.apply_gain(percent_to_db(music_vol_right_pct))

        voice_inverted = voice_final.invert_phase()
        left_channel = voice_inverted.overlay(noise_l_final)
        right_channel = voice_final.overlay(noise_r_final)

        final_audio = AudioSegment.from_mono_audiosegments(left_channel, right_channel)
        processed_audio_path = f"temp_processed_{unique_id}.wav"
        final_audio.export(processed_audio_path, format="wav")
        temp_files.append(processed_audio_path)

        # 4. GERAÇÃO DE SAÍDA (Otimizado para 32 vCPUs)
        name_base, ext = os.path.splitext(filename)
        new_filename = f"{name_base} - new{ext}"

        if is_video:
            new_audioclip = AudioFileClip(processed_audio_path)
            final_video = video_clip.set_audio(new_audioclip)
            output_video_path = f"output_video_{unique_id}.mp4"
            
            # LINHA ALTERADA: Usando 32 threads e preset rápido
            final_video.write_videofile(
                output_video_path, 
                codec="libx264", 
                audio_codec="aac", 
                logger=None,
                threads=32,       # Aproveita seu plano Railway
                preset='ultrafast' # Renderização quase instantânea
            )
            
            temp_files.append(output_video_path)
            video_clip.close()
            new_audioclip.close()

            return send_file(output_video_path, mimetype="video/mp4", as_attachment=True, download_name=new_filename)
        else:
            output_io = BytesIO()
            final_audio.export(output_io, format="wav")
            output_io.seek(0)
            return send_file(output_io, mimetype="audio/wav", as_attachment=True, download_name=new_filename)

    except Exception as e:
        return f"Erro: {str(e)}", 500
    finally:
        for f in temp_files:
            if os.path.exists(f):
                try: os.remove(f)
                except: pass

# AJUSTE OBRIGATÓRIO PARA RAILWAY
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port) # Bind em 0.0.0.0 é essencial
