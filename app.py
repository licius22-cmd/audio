import os
import uuid
import subprocess
from flask import Flask, request, send_file
import imageio_ffmpeg

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB

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
    base_path = "/tmp/" if os.path.exists("/tmp") else ""
    temp_files = []

    try:
        # 1. PEGAR INPUTS
        voice_file = request.files.get('voice')
        noise_file = request.files.get('noise')
        
        if not voice_file:
            return "Arquivo principal faltando", 400

        # Volumes vindos do frontend
        v_vol = float(request.form.get('voice_volume', 100)) / 100.0
        n_vol_l = float(request.form.get('music_vol_left', 100)) / 100.0
        n_vol_r = float(request.form.get('music_vol_right', 30)) / 100.0

        # Salvar entrada principal
        input_path = f"{base_path}in_{unique_id}_{voice_file.filename}"
        voice_file.save(input_path)
        temp_files.append(input_path)

        # Tratar o Background (Música)
        if noise_file and noise_file.filename != '':
            noise_path = f"{base_path}bg_{unique_id}_{noise_file.filename}"
            noise_file.save(noise_path)
            temp_files.append(noise_path)
        else:
            # Busca background padrão se não enviado
            possible = ['background.mp3', 'background.wav', 'background.m4a']
            noise_path = next((f for f in possible if os.path.exists(f)), None)

        # 2. O COMANDO MESTRE (Processamento em Fluxo Único)
        # Este filtro FFmpeg faz a inversão de fase e mixagem em uma única passada de CPU
        output_filename = f"new_{voice_file.filename}"
        output_path = f"{base_path}out_{unique_id}_{output_filename}"
        
        # Lógica do filtro: 
        # [0:a] é a voz. [1:a] é o fundo. 
        # Invertemos [0:a] para o canal L e somamos com [1:a] no volume definido.
        # Mantemos [0:a] normal para o canal R e somamos com [1:a] no volume definido.
        filter_complex = (
            f"[0:a]asplit=2[v1][v2];"
            f"[v1]aneval=expr=-val(0):c=mono[v_inv];"
            f"[v_inv][1:a]amix=inputs=2:duration=first:weights='{v_vol} {n_vol_l}'[ch_l];"
            f"[v2][1:a]amix=inputs=2:duration=first:weights='{v_vol} {n_vol_r}'[ch_r];"
            f"[ch_l][ch_r]join=inputs=2:channel_layout=stereo[a_out]"
        )

        cmd = [
            FFMPEG_EXE, '-y',
            '-i', input_path,
            '-stream_loop', '-1', '-i', noise_path if noise_path else 'anullsrc=r=44100:cl=stereo',
            '-filter_complex', filter_complex,
            '-map', '0:v?',           # Copia o vídeo se existir
            '-map', '[a_out]',        # Mapeia o áudio processado
            '-c:v', 'copy',           # VELOCIDADE: Não renderiza vídeo, apenas copia
            '-c:a', 'aac',            # Converte o áudio final para AAC
            '-shortest',              # Termina quando a voz acabar
            '-threads', '32',         # Usa todos os seus núcleos
            output_path
        ]

        subprocess.run(cmd, check=True)
        temp_files.append(output_path)

        return send_file(output_path, as_attachment=True, download_name=output_filename)

    except Exception as e:
        return f"Erro Crítico: {str(e)}", 500
    finally:
        # Limpeza rápida de arquivos na RAM (/tmp)
        for f in temp_files:
            if os.path.exists(f):
                try: os.remove(f)
                except: pass

if __name__ == '__main__':
    import os
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)
