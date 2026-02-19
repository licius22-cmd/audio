import os
import uuid
import subprocess
from flask import Flask, request, send_file
import imageio_ffmpeg

# Localiza o executável do FFmpeg
FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024 

@app.route('/')
def index():
    try:
        with open('index.html', 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return "Arquivo index.html não encontrado."

@app.route('/process', methods=['POST'])
def process_media():
    unique_id = str(uuid.uuid4())
    # RAM Disk (/tmp) para processamento ultra-rápido
    base_path = "/tmp/" if os.path.exists("/tmp") else ""
    temp_files = []

    try:
        voice_file = request.files.get('voice')
        noise_file = request.files.get('noise')
        
        if not voice_file:
            return "Arquivo principal faltando", 400

        # Volumes (0.0 a 2.0)
        v_vol = float(request.form.get('voice_volume', 100)) / 100.0
        n_vol_l = float(request.form.get('music_vol_left', 100)) / 100.0
        n_vol_r = float(request.form.get('music_vol_right', 30)) / 100.0

        input_path = f"{base_path}in_{unique_id}_{voice_file.filename}"
        voice_file.save(input_path)
        temp_files.append(input_path)

        input_args = ['-i', input_path]
        if noise_file and noise_file.filename != '':
            noise_path = f"{base_path}bg_{unique_id}_{noise_file.filename}"
            noise_file.save(noise_path)
            temp_files.append(noise_path)
            input_args += ['-stream_loop', '-1', '-i', noise_path]
        else:
            possible = ['background.mp3', 'background.wav', 'background.m4a']
            found_bg = next((f for f in possible if os.path.exists(f)), None)
            if found_bg:
                input_args += ['-stream_loop', '-1', '-i', found_bg]
            else:
                input_args += ['-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo']

        # CORREÇÃO: 'aeval' em vez de 'aneval'. Inversão de fase: -val(0)
        # Processamento: Split Voz -> Inverter 1 -> Split Música -> Mixar L/R -> Join
        filter_complex = (
            f"[0:a]asplit=2[v1][v2];"
            f"[v1]aeval=-val(0)[v_inv];"
            f"[1:a]asplit=2[n1][n2];"
            f"[v_inv][n1]amix=inputs=2:duration=first:weights='{v_vol} {n_vol_l}'[ch_l];"
            f"[v2][n2]amix=inputs=2:duration=first:weights='{v_vol} {n_vol_r}'[ch_r];"
            f"[ch_l][ch_r]join=inputs=2:channel_layout=stereo[a_out]"
        )

        output_filename = f"new_{voice_file.filename}"
        output_path = f"{base_path}out_{unique_id}_{output_filename}"

        cmd = [
            FFMPEG_EXE, '-y',
            '-thread_queue_size', '1024'
        ] + input_args + [
            '-filter_complex', filter_complex,
            '-map', '0:v?', 
            '-map', '[a_out]',
            '-c:v', 'copy',       # Mantém a velocidade
            '-c:a', 'aac', 
            '-shortest', 
            '-threads', '32',     # Usa seus 32 núcleos
            output_path
        ]

        # Captura o log de erro para diagnóstico preciso
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return f"Erro no FFmpeg: {result.stderr}", 500

        temp_files.append(output_path)
        return send_file(output_path, as_attachment=True, download_name=output_filename)

    except Exception as e:
        return f"Erro Crítico: {str(e)}", 500
    finally:
        for f in temp_files:
            if os.path.exists(f):
                try: os.remove(f)
                except: pass

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)
