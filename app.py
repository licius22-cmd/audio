import os
import uuid
import subprocess
from flask import Flask, request, send_file
import imageio_ffmpeg

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
    # RAM Disk (/tmp) é essencial para bater a meta de 10 segundos
    base_path = "/tmp/" if os.path.exists("/tmp") else ""
    temp_files = []

    try:
        voice_file = request.files.get('voice')
        noise_file = request.files.get('noise')
        
        if not voice_file:
            return "Arquivo principal faltando", 400

        # Converte volumes para escala decimal
        v_vol = float(request.form.get('voice_volume', 100)) / 100.0
        n_l = float(request.form.get('music_vol_left', 100)) / 100.0
        n_r = float(request.form.get('music_vol_right', 30)) / 100.0

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
            # Gera silêncio virtual se não houver fundo
            input_args += ['-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo']

        # O SEGREDO DA VELOCIDADE: Matriz de Pan
        # C0 = Canal Esquerdo: -Voz + Música_L
        # C1 = Canal Direito: Voz + Música_R
        # amerge junta as fontes, pan faz o cálculo matemático instantâneo
        filter_pan = (
            f"[0:a]pan=mono|c0=c0[v];"
            f"[1:a]pan=mono|c0=c0[n];"
            f"[v][n]amerge=inputs=2[mix];"
            f"[mix]pan=stereo|c0=-{v_vol}*c0+{n_l}*c1|c1={v_vol}*c0+{n_r}*c1[a_out]"
        )

        output_filename = f"new_{voice_file.filename}"
        output_path = f"{base_path}out_{unique_id}_{output_filename}"

        cmd = [
            FFMPEG_EXE, '-y',
            '-thread_queue_size', '4096', # Buffer agressivo para 32 vCPUs
        ] + input_args + [
            '-filter_complex', filter_pan,
            '-map', '0:v?',           # Copia vídeo sem processar (instantâneo)
            '-map', '[a_out]',        # Mapeia áudio da matriz pan
            '-c:v', 'copy',           # NÃO RENDERIZA VÍDEO (Pulo do gato)
            '-c:a', 'aac',            # Converte apenas o áudio
            '-b:a', '192k',           # Bitrate fixo para velocidade
            '-shortest', 
            '-threads', '32',         # Usa todos os núcleos
            output_path
        ]

        # Execução direta e veloz
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
    import os
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)
