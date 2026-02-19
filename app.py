# 2. O COMANDO MESTRE (Corrigido para evitar Status 8)
        output_filename = f"new_{voice_file.filename}"
        output_path = f"{base_path}out_{unique_id}_{output_filename}"
        
        # Preparação dos inputs de áudio para o comando
        input_args = ['-i', input_path]
        
        if noise_path and os.path.exists(noise_path):
            # Se houver música de fundo, faz o loop dela
            input_args += ['-stream_loop', '-1', '-i', noise_path]
        else:
            # CORREÇÃO DO STATUS 8: Usa o formato 'lavfi' para gerar silêncio
            input_args += ['-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo']

        # Filtro de Áudio Ultra-Otimizado:
        # 1. Separa a voz em 2, inverte uma delas.
        # 2. Separa a música em L e R.
        # 3. Mixa (Voz Invertida + Música L) e (Voz Normal + Música R).
        filter_complex = (
            f"[0:a]asplit=2[v1][v2];"
            f"[v1]aneval=expr=-val(0)[v_inv];"
            f"[1:a]channelsplit=channel_layout=stereo[nL][nR];"
            f"[v_inv][nL]amix=inputs=2:duration=first:weights='{v_vol} {n_vol_l}'[ch_l];"
            f"[v2][nR]amix=inputs=2:duration=first:weights='{v_vol} {n_vol_r}'[ch_r];"
            f"[ch_l][ch_r]join=inputs=2:channel_layout=stereo[a_out]"
        )

        cmd = [FFMPEG_EXE, '-y'] + input_args + [
            '-filter_complex', filter_complex,
            '-map', '0:v?',           # Copia o vídeo original (se existir)
            '-map', '[a_out]',        # Usa o áudio processado
            '-c:v', 'copy',           # COPIA o vídeo (veloz)
            '-c:a', 'aac',            # Converte áudio final para AAC
            '-shortest',              # Termina quando o vídeo original acabar
            '-threads', '32',         # Usa todos os seus 32 núcleos
            output_path
        ]

        subprocess.run(cmd, check=True)
