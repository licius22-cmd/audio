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
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB for video

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

        # 1. EXTRACT / PREPARE AUDIO SOURCE
        if is_video:
            video_clip = VideoFileClip(input_path)
            # Extract audio to temp wav
            audio_source_path = f"temp_audio_source_{unique_id}.wav"
            video_clip.audio.write_audiofile(audio_source_path, logger=None)
            temp_files.append(audio_source_path)
            voice_seg = AudioSegment.from_file(audio_source_path)
        else:
            voice_seg = AudioSegment.from_file(input_path)

        # 2. PREPARE NOISE / BACKGROUND
        if 'noise' in request.files and request.files['noise'].filename != '':
            noise_file = request.files['noise']
            noise_path = f"temp_noise_{unique_id}_{noise_file.filename}"
            noise_file.save(noise_path)
            temp_files.append(noise_path)
            noise_seg = AudioSegment.from_file(noise_path)
        else:
            # Default background check
            possible_files = ['background.mp3', 'background.wav', 'background.m4a']
            found_bg = None
            for f in possible_files:
                if os.path.exists(f):
                    found_bg = f
                    break
            
            if found_bg:
                noise_seg = AudioSegment.from_file(found_bg)
            else:
                # If no background, create silent noise (just phase cancellation)
                print("Warning: No background file found. Using silence.")
                noise_seg = AudioSegment.silent(duration=1000) # 1s silence, will be looped

        # 3. PROCESS AUDIO (The Magic)
        try:
            voice_vol_pct = float(request.form.get('voice_volume', 100))
            music_vol_left_pct = float(request.form.get('music_vol_left', 100))
            music_vol_right_pct = float(request.form.get('music_vol_right', 30))
        except ValueError:
            return "Volume inválido", 400

        # Loop noise to match voice length
        if len(noise_seg) < len(voice_seg):
             loops = math.ceil(len(voice_seg) / len(noise_seg))
             noise_seg = noise_seg * loops
        noise_seg = noise_seg[:len(voice_seg)]

        # Mono conversion for clean processing
        voice_mono = voice_seg.set_channels(1)
        if noise_seg.channels == 1:
            noise_l = noise_seg
            noise_r = noise_seg
        else:
            noise_channels = noise_seg.split_to_mono()
            noise_l = noise_channels[0]
            if len(noise_channels) > 1:
                noise_r = noise_channels[1]
            else:
                noise_r = noise_channels[0]

        # Gains
        voice_final = voice_mono.apply_gain(percent_to_db(voice_vol_pct))
        noise_l_final = noise_l.apply_gain(percent_to_db(music_vol_left_pct))
        noise_r_final = noise_r.apply_gain(percent_to_db(music_vol_right_pct))

        # Mixing
        # Left: Inverted Voice + Music High
        voice_inverted = voice_final.invert_phase()
        left_channel = voice_inverted.overlay(noise_l_final)
        
        # Right: Normal Voice + Music Low
        right_channel = voice_final.overlay(noise_r_final)

        final_audio = AudioSegment.from_mono_audiosegments(left_channel, right_channel)
        
        processed_audio_path = f"temp_processed_{unique_id}.wav"
        final_audio.export(processed_audio_path, format="wav")
        temp_files.append(processed_audio_path)

        # 4. OUTPUT GENERATION
        if is_video:
            # Remux audio into video
            new_audioclip = AudioFileClip(processed_audio_path)
            final_video = video_clip.set_audio(new_audioclip)
            
            output_video_path = f"output_video_{unique_id}.mp4"
            # Write video file (using codec 'libx264' and audio 'aac')
            # temp_audiofile needed because moviepy creates one internally usually
            final_video.write_videofile(output_video_path, codec="libx264", audio_codec="aac", logger=None)
            temp_files.append(output_video_path)
            
            # Close clips to release file handles
            video_clip.close()
            new_audioclip.close()

            return send_file(
                output_video_path, 
                mimetype="video/mp4", 
                as_attachment=True, 
                download_name=f"video_magico_{filename}"
            )
        else:
            # Just return audio
            output_io = BytesIO()
            final_audio.export(output_io, format="wav")
            output_io.seek(0)
            return send_file(
                output_io, 
                mimetype="audio/wav", 
                as_attachment=True, 
                download_name=f"audio_magico_{os.path.splitext(filename)[0]}.wav"
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
    print("Iniciando servidor Audio Criativo...")
    print("Acesse http://localhost:5001 no seu navegador.")
    port = int(os.environ.get("PORT", 5001))
    app.run(host='0.0.0.0', port=port)
