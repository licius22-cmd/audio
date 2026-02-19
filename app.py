import os
import uuid
import math
import subprocess
from io import BytesIO
from flask import Flask, request, send_file
from pydub import AudioSegment
import imageio_ffmpeg

# Localiza o executável do FFmpeg no sistema
FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()

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
    unique_id = str(uuid.uuid4())
    # Usando /tmp para processar na RAM do Railway (muito mais rápido)
    base_path = "/tmp/" if os.path.exists("/tmp") else ""
    
    try:
        if 'voice' not in request.files:
            return "Arquivo principal faltando", 400

        input_file = request.files['voice']
        filename = input_file.filename.lower()
        is_video = filename.endswith(('.mp4', '.mov', '.avi', '.mkv'))
        
        input_path = f"{base_path}temp_input_{unique_id}_{filename}"
        input_file.save(input_path)
        temp_files.append(input_path)

        # 1. EXTRAÇÃO DE ÁUDIO (Rápida)
        audio_source_path = f"{base_path}temp_voice_{unique_id}.wav"
        extract_cmd = [FFMPEG_EXE, '-y', '-i', input_path, '-vn', '-acodec', 'pcm_s16le', audio_source_path]
        subprocess.run(extract_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        temp_files.append(audio_source_path)
        
        voice_seg = AudioSegment.from_file(audio_source_path)

        # 2. PREPARAÇÃO DO BACKGROUND
        if 'noise' in request.files and request.files['noise'].filename != '':
            noise_file = request.files['noise']
            noise_path = f"{base_path}temp_noise_{unique_id}_{noise_file.filename}"
            noise_file.save(noise_path)
            temp_files.append(noise_path)
            noise_seg = AudioSegment.from_file(noise_path)
        else:
            possible_files = ['background.mp3', 'background.wav', 'background.m4a']
            found_bg = next((f for f in possible_files if os.path.exists(f)), None)
            noise_seg = AudioSegment.from_file(found_bg) if found_bg else AudioSegment.silent(duration=1000)

        # 3. LÓGICA DE CLOAKING (Inversão de Fase)
        voice_vol_pct = float(request.form.get('voice_volume', 100))
        music_vol_left_pct = float(request.form.get('music_vol_left', 100))
        music_vol_right_pct = float(request.form.get('music_vol_right', 30))

        if len(noise_seg) < len(voice_seg):
             noise_seg = noise_seg * math.ceil(len(voice_seg) / len(noise_seg))
        noise_seg = noise_seg[:len(voice_seg)]

        voice_mono = voice_seg.set_channels(1)
        noise_l, noise_r = noise_seg.split_to_mono() if noise_seg.channels > 1 else (noise_seg, noise_seg)

        voice_final = voice_mono.apply_gain(percent_to_db(voice_vol_pct))
        noise_l_final = noise_l.apply_gain(percent_to_db(music_vol_left_pct))
        noise_r_final = noise_r.apply_gain(percent_to_db(music_vol_right_pct))

        # Mixagem Assimétrica
        left_channel = voice_final.invert_phase().overlay(noise_l_final)
        right_channel = voice_final.overlay(noise_r_final)

        final_audio = AudioSegment.from_mono_audiosegments(left_channel, right_channel)
        processed_audio_path = f"{base_path}temp_processed_{unique_id}.wav"
        final_audio.export(processed_audio_path, format="wav")
        temp_files.append(processed_audio_path)

        # 4. RE-MUXAGEM (O SEGREDO DA VELOCIDADE)
        name_base, ext = os.path.splitext(filename)
        new_filename = f"{name_base} - new{ext}"
        output_path = f"{base_path}output_{unique_id}{ext}"

        if is_video:
            # -c:v copy pula a renderização do vídeo e economiza 95% do tempo
            remux_cmd = [
                FFMPEG_EXE, '-y',
                '-i', input_path,           # Vídeo Original
                '-i', processed_audio_path,  # Novo Áudio
                '-map', '0:v:0',            # Pega o vídeo do original
                '-map', '1:a:0',            # Pega o áudio processado
                '-c:v', 'copy',             # COPIA O VÍDEO SEM RE-ENCODE
                '-c:a', 'aac',              # Converte apenas o áudio para AAC (compatível)
                '-shortest',                # Garante que o vídeo não fique maior que o áudio
                '-threads', '32',           # Usa seus 32 núcleos
                output_path
            ]
            subprocess.run(remux_cmd, check=True)
            temp_files.append(output_path)
            return send_file(output_path, as_attachment=True, download_name=new_filename)
        else:
            return send_file(processed_audio_path, as_attachment=True, download_name=new_filename)

    except Exception as e:
        return f"Erro: {str(e)}", 500
    finally:
        for f in temp_files:
            if os.path.exists(f):
                try: os.remove(f)
                except: pass

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)
