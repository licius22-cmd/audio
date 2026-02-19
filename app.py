import os
import uuid
import subprocess
from flask import Flask, request, send_file
import imageio_ffmpeg

# Localiza o executável do FFmpeg garantindo compatibilidade no Railway
FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()

app = Flask(__name__)
# Limite de 500MB para uploads de vídeo
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024 

@app.route('/')
def index():
    """Renderiza a interface principal do SaaS"""
    try:
        with open('index.html', 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return "Arquivo index.html não encontrado."

@app.route('/process', methods=['POST'])
def process_media():
    """Processa áudio e vídeo com inversão de fase assimétrica"""
    unique_id = str(uuid.uuid4())
    # Usa /tmp (RAM Disk no Linux) para velocidade máxima de leitura/escrita
    base_path = "/tmp/" if os.path.exists("/tmp") else ""
    temp_files = []

    try:
        # 1. CAPTURA DE INPUTS E VOLUMES
        voice_file = request.files.get('voice')
        noise_file = request.files.get('noise')
        
        if not voice_file:
            return "Arquivo principal faltando", 400

        # Converte volumes do frontend (0-200) para escala decimal do FFmpeg (0.0-2.0)
        v_vol = float(request.form.get('voice_volume', 100)) / 100.0
        n_vol_l = float(request.form.get('music_vol_left', 100)) / 100.0
        n_vol_r = float(request.form.get('music_vol_right', 30)) / 100.0

        # Salva o arquivo de entrada na RAM
        input_path = f"{base_path}in_{unique_id}_{voice_file.filename}"
        voice_file.save(input_path)
        temp_files.append(input_path)

        # [cite_start]Configuração da trilha de fundo (Música/Ruído) [cite: 1]
        input_args = ['-i', input_path]
        if noise_file and noise_file.filename != '':
            noise_path = f"{base_path}bg_{unique_id}_{noise_file.filename}"
            noise_file.save(noise_path)
            temp_files.append(noise_path)
            input_args += ['-stream_loop', '-1', '-i', noise_path]
        else:
            # [cite_start]Busca background padrão ou gera silêncio se nada for enviado [cite: 1]
            possible = ['background.mp3', 'background.wav', 'background.m4a']
            found_bg = next((f for f in possible if os.path.exists(f)), None)
            if found_bg:
                input_args += ['-stream_loop', '-1', '-i', found_bg]
            else:
                # Correção para o erro 'Exit Status 8': define anullsrc como lavfi
                input_args += ['-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo']

        # 2. FILTRO DE ÁUDIO (A MÁGICA DO CLOAKING)
        # Processa inversão de fase e mixagem estéreo em uma única passada de CPU
        filter_complex = (
            f"[0:a]asplit=2[v1][v2];"
            f"[v1]aneval=expr=-val(0)[v_inv];"
            f"[1:a]asplit=2[n1][n2];"
            f"[v_inv][n1]amix=inputs=2:duration=first:weights='{v_vol} {n_vol_l}'[ch_l];"
            f"[v2][n2]amix=inputs=2:duration=first:weights='{v_vol} {n_vol_r}'[ch_r];"
            f"[ch_l][ch_r]join=inputs=2:channel_layout=stereo[a_out]"
        )

        output_filename = f"new_{voice_file.filename}"
        output_path = f"{base_path}out_{unique_id}_{output_filename}"

        # 3. COMANDO DE EXECUÇÃO ULTRA-RÁPIDO
        cmd = [
            FFMPEG_EXE, '-y',
            '-thread_queue_size', '1024' # Otimiza o buffer para 32 vCPUs
        ] + input_args + [
            '-filter_complex', filter_complex,
            '-map', '0:v?',           # Copia o vídeo se existir
            '-map', '[a_out]',        # Mapeia o áudio processado
            '-c:v', 'copy',           # COPIA o vídeo (ganha 95% de velocidade)
            '-c:a', 'aac',            # Converte áudio final para formato compatível
            '-shortest',              # Termina quando a voz acabar
            '-threads', '32',         # Usa todos os seus 32 núcleos
            output_path
        ]

        # Executa o FFmpeg e monitora erros
        subprocess.run(cmd, check=True, capture_output=True)
        temp_files.append(output_path)

        return send_file(output_path, as_attachment=True, download_name=output_filename)

    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode() if e.stderr else str(e)
        return f"Erro no FFmpeg: {error_msg}", 500
    except Exception as e:
        return f"Erro Crítico: {str(e)}", 500
    finally:
        # Limpeza rigorosa de arquivos temporários na RAM
        for f in temp_files:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except:
                    pass

# INICIALIZAÇÃO PARA RAILWAY
if __name__ == '__main__':
    # Obtém a porta dinâmica do Railway
    port = int(os.environ.get("PORT", 5001))
    # Host 0.0.0.0 é obrigatório para o proxy do Railway funcionar
    app.run(host="0.0.0.0", port=port)
