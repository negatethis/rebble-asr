import gevent.monkey
gevent.monkey.patch_all()
from email.mime.multipart import MIMEMultipart
from email.message import Message
from .model_map import get_model_for_lang
import json
import os
import struct
import requests
import io
import wave
import time
import audioop
import logging
import asyncio
from speex import SpeexDecoder
from flask import Flask, request, Response, abort

# Wyoming imports
try:
    import wyoming
    from wyoming.asr import Transcribe, Transcript
    from wyoming.audio import AudioChunk, AudioStart, AudioStop
    from wyoming.client import AsyncTcpClient
    HAS_WYOMING = True
except ImportError:
    HAS_WYOMING = False
    print("[WARNING] Wyoming package not installed, wyoming-whisper provider will not be available")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('rebble-asr')

# Set up debug mode from environment variable
DEBUG = os.environ.get('DEBUG', 'false').lower() in ('true', '1', 't', 'yes')
if DEBUG:
    logger.setLevel(logging.DEBUG)
    logger.debug("Debug mode enabled")
else:
    logger.setLevel(logging.INFO)

decoder = SpeexDecoder(1)
app = Flask(__name__)

# Get API key from environment, or None if not set
API_KEY = os.environ.get('ASR_API_KEY')

# Get Wyoming connection details from environment
WYOMING_HOST = os.environ.get('WYOMING_HOST', 'localhost')
WYOMING_PORT = int(os.environ.get('WYOMING_PORT', '10300'))

# Audio settings for Wyoming
SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
SAMPLE_CHANNELS = 1

# Determine which provider to use
if not API_KEY and os.environ.get('ASR_API_PROVIDER') != 'wyoming-whisper':
    raise Exception("[ERROR] No API key set and not using wyoming-whisper. Please provide an API key.")
else:
    # Get the provider from environment and strip any quotes
    ASR_API_PROVIDER = os.environ.get('ASR_API_PROVIDER', 'groq')
    # Remove quotes if they exist
    ASR_API_PROVIDER = ASR_API_PROVIDER.strip('"\'')

logger.info(f"Using ASR API provider: {ASR_API_PROVIDER}")

# Check if Wyoming is available when selected
if ASR_API_PROVIDER == 'wyoming-whisper' and not HAS_WYOMING:
    raise Exception("Wyoming-whisper selected but Wyoming package not installed.")


# We know gunicorn does this, but it doesn't *say* it does this, so we must signal it manually.
@app.before_request
def handle_chunking():
    request.environ['wsgi.input_terminated'] = 1


def parse_chunks(stream):
    boundary = b'--' + request.headers['content-type'].split(';')[1].split('=')[1].encode('utf-8').strip()  # super lazy/brittle parsing.
    this_frame = b''
    while True:
        content = stream.read(4096)
        this_frame += content
        end = this_frame.find(boundary)
        if end > -1:
            frame = this_frame[:end]
            this_frame = this_frame[end + len(boundary):]
            if frame != b'':
                try:
                    header, content = frame.split(b'\r\n\r\n', 1)
                except ValueError:
                    continue
                yield content[:-2]
        if content == b'':
            print("End of input.")
            break

def elevenlabs_transcribe(wav_buffer):
    try:
        if DEBUG:
            logger.debug("Starting ElevenLabs transcription")
            api_start_time = time.time()

        # Create transcription via the ElevenLabs API
        TRANSCIPTION_URL = "https://api.elevenlabs.io/v1/speech-to-text"

        files = {
            "file": ("audio.wav", wav_buffer, "audio/wav")
        }
        data = {
            "model_id": "scribe_v1",
            "tag_audio_events": "false",
            "timestamps_granularity": "none"
        }
        headers = {
            "xi-api-key": API_KEY
        }

        response_api = requests.post(TRANSCIPTION_URL, files=files, data=data, headers=headers)
        response_api.raise_for_status()
        transcription = response_api.json()

        if DEBUG:
            api_time = time.time() - api_start_time
            logger.debug(f"ElevenLabs API request completed in {api_time:.3f}s")

        return transcription.get("text", "")

    except requests.exceptions.RequestException as e:
        logger.error(f"ElevenLabs transcription error: {e}")
        return None

def groq_transcribe(wav_buffer):
    try:
        if DEBUG:
            logger.debug("Starting Groq transcription")
            api_start_time = time.time()

        # Create transcription via the Groq API
        TRANSCIPTION_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

        files = {
            "file": ("audio.wav", wav_buffer, "audio/wav")
        }
        data = {
            "model": "whisper-large-v3",
            "response_format": "json"
        }
        headers = {
            "Authorization": f"Bearer {API_KEY}"
        }

        response_api = requests.post(TRANSCIPTION_URL, files=files, data=data, headers=headers)
        response_api.raise_for_status()
        transcription = response_api.json()

        if DEBUG:
            api_time = time.time() - api_start_time
            logger.debug(f"Groq API request completed in {api_time:.3f}s")

        return transcription.get("text", "")

    except requests.exceptions.RequestException as e:
        logger.error(f"Groq transcription error: {e}")
        return None

def wyoming_whisper_transcribe(wav_buffer):
    try:
        if not HAS_WYOMING:
            logger.error("Wyoming package not installed, cannot use wyoming-whisper")
            return None

        if DEBUG:
            logger.debug(f"Starting Wyoming-whisper transcription")
            logger.debug(f"Wyoming host: {WYOMING_HOST}, port: {WYOMING_PORT}")
            wyoming_start_time = time.time()

        # Reset buffer position and read the audio data
        wav_buffer.seek(0)

        # Parse the WAV file to get just the PCM data
        with wave.open(wav_buffer, 'rb') as wav_file:
            audio_data = wav_file.readframes(wav_file.getnframes())
            if DEBUG:
                logger.debug(f"Extracted {len(audio_data)} bytes of PCM data from WAV")

        # Since we need to use asyncio, we need to create and run an async function
        async def process_with_wyoming():
            connection_start_time = time.time() if DEBUG else 0
            try:
                # Connect to Wyoming service
                async with AsyncTcpClient(WYOMING_HOST, WYOMING_PORT) as client:
                    if DEBUG:
                        connection_time = time.time() - connection_start_time
                        logger.debug(f"Connected to Wyoming service in {connection_time:.3f}s")

                    # Set transcription language (using default as we don't have language info)
                    await client.write_event(Transcribe(language=None).event())

                    # Begin audio stream
                    await client.write_event(
                        AudioStart(
                            rate=SAMPLE_RATE,
                            width=SAMPLE_WIDTH,
                            channels=SAMPLE_CHANNELS,
                        ).event()
                    )

                    if DEBUG:
                        logger.debug(f"Sending {len(audio_data)} bytes to Wyoming service")

                    # Send audio data
                    chunk = AudioChunk(
                        rate=SAMPLE_RATE,
                        width=SAMPLE_WIDTH,
                        channels=SAMPLE_CHANNELS,
                        audio=audio_data,
                    )
                    await client.write_event(chunk.event())

                    # End audio stream
                    await client.write_event(AudioStop().event())

                    if DEBUG:
                        logger.debug("Waiting for transcription result")

                    # Wait for transcription result
                    while True:
                        event = await client.read_event()
                        if event is None:
                            logger.error("Wyoming connection lost")
                            return None

                        if Transcript.is_type(event.type):
                            transcript = Transcript.from_event(event)
                            if DEBUG:
                                logger.debug(f"Received transcript from Wyoming service: '{transcript.text}'")
                            return transcript.text
            except Exception as e:
                logger.error(f"Wyoming transcription error: {e}")
                if DEBUG:
                    import traceback
                    logger.debug(traceback.format_exc())
                return None

        # Run the async function in an event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(process_with_wyoming())
            if DEBUG:
                wyoming_time = time.time() - wyoming_start_time
                logger.debug(f"Wyoming-whisper transcription completed in {wyoming_time:.3f}s")
            return result
        finally:
            loop.close()

    except Exception as e:
        logger.error(f"Wyoming-whisper transcription error: {e}")
        if DEBUG:
            import traceback
            logger.debug(traceback.format_exc())
        return None

@app.route('/heartbeat')
def heartbeat():
    return 'asr'

@app.route('/NmspServlet/', methods=["POST"])
def recognise():
    # Track total processing time
    start_time = time.time()

    if DEBUG:
        logger.debug(f"Received request from: {request.remote_addr}")
        logger.debug(f"Request headers: {dict(request.headers)}")

    stream = request.stream

    chunks = list(parse_chunks(stream))
    chunks = chunks[3:]
    pcm_data = bytearray()

    if len(chunks) > 15:
        chunks = chunks[12:-3]

    if DEBUG:
        logger.debug(f"Received {len(chunks)} audio chunks")

    chunk_process_start = time.time()
    for i, chunk in enumerate(chunks):
        decoded = decoder.decode(chunk)
        # Boosting the audio volume
        decoded = audioop.mul(decoded, 2, 7)
        # Directly append decoded audio bytes
        pcm_data.extend(decoded)

    if DEBUG:
        chunk_process_time = time.time() - chunk_process_start
        logger.debug(f"Processed {len(chunks)} chunks in {chunk_process_time:.3f}s")
        logger.debug(f"PCM data size: {len(pcm_data)} bytes")

    # Create WAV file in memory
    wav_start_time = time.time()
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(pcm_data)

    wav_buffer.seek(0)
    wav_size = wav_buffer.getbuffer().nbytes

    if DEBUG:
        wav_process_time = time.time() - wav_start_time
        logger.debug(f"Created WAV file in {wav_process_time:.3f}s")
        logger.debug(f"WAV file size: {wav_size} bytes")
        logger.debug(f"Audio duration: ~{len(pcm_data)/16000/2:.2f}s at 16kHz")

    # Initialize transcript variable
    transcript = None

    logger.info(f"Using ASR API provider: {ASR_API_PROVIDER}")

    # Track transcription time
    transcription_start = time.time()

    if ASR_API_PROVIDER == 'elevenlabs':
        if not API_KEY:
            raise Exception("ElevenLabs requires an API key. Please provide one.")
        else:
            transcript = elevenlabs_transcribe(wav_buffer)
    elif ASR_API_PROVIDER == 'groq':
        if not API_KEY:
            raise Exception("Groq requires an API key. Please provide one.")
        else:
            transcript = groq_transcribe(wav_buffer)
    elif ASR_API_PROVIDER == 'wyoming-whisper':
        transcript = wyoming_whisper_transcribe(wav_buffer)
        if transcript is None:
            logger.error("Wyoming-whisper transcription failed.")
    else:
        logger.error(f"Invalid ASR API provider: {ASR_API_PROVIDER}.")

    transcription_time = time.time() - transcription_start

    # Check if transcript is valid
    if transcript is None:
        logger.error("All transcription methods failed")
        abort(500)

    logger.info(f"Transcript: '{transcript}' (took {transcription_time:.3f}s)")
    words = []
    for word in transcript.split():
        words.append({
            'word': word,
            'confidence': 1.0
        })

    # Now create a MIME multipart response
    parts = MIMEMultipart()
    response_part = Message()
    response_part.add_header('Content-Type', 'application/JSON; charset=utf-8')

    if len(words) > 0:
        response_part.add_header('Content-Disposition', 'form-data; name="QueryResult"')
        # Append the no-space marker and uppercase the first character
        words[0]['word'] += '\\*no-space-before'
        words[0]['word'] = words[0]['word'][0].upper() + words[0]['word'][1:]
        payload = json.dumps({'words': [words]})
        #print(f"[DEBUG] Payload for QueryResult: {payload}")
    else:
        response_part.add_header('Content-Disposition', 'form-data; name="QueryRetry"')
        payload = json.dumps({
            "Cause": 1,
            "Name": "AUDIO_INFO",
            "Prompt": "Sorry, speech not recognized. Please try again."
        })
        #print(f"[DEBUG] Payload for QueryRetry: {payload}")

    response_part.set_payload(payload)
    parts.attach(response_part)

    parts.set_boundary('--Nuance_NMSP_vutc5w1XobDdefsYG3wq')
    response_text = '\r\n' + parts.as_string().split("\n", 3)[3].replace('\n', '\r\n')
    if DEBUG:
        logger.debug(f"Final response text prepared with boundary: {parts.get_boundary()}")

    response = Response(response_text)
    response.headers['Content-Type'] = f'multipart/form-data; boundary={parts.get_boundary()}'

    # Log total processing time
    total_time = time.time() - start_time
    logger.info(f"Total processing time: {total_time:.3f}s")

    if DEBUG:
        logger.debug("Sending response")

    return response
