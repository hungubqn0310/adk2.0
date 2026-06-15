"""
Voice STT API - Speech-to-Text Streaming
User speaks → Real-time STT → Return text immediately
"""
import base64
import logging
import os
import traceback
from datetime import timedelta

import dotenv
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from google.cloud import speech_v2 as speech
from google.api_core.client_options import ClientOptions

dotenv.load_dotenv(override=True)
logger = logging.getLogger(__name__)

# Google Cloud Speech-to-Text V2 Configuration
GOOGLE_CLOUD_PROJECT = os.getenv('GOOGLE_CLOUD_PROJECT')
GOOGLE_CLOUD_LOCATION = os.getenv('GOOGLE_CLOUD_LOCATION', 'us')
SPEECH_RECOGNIZER_ID = os.getenv('SPEECH_RECOGNIZER_ID', '_')

# Supported languages for Chirp 3 model
SUPPORTED_LANGUAGES = {
    'vi-VN': 'Tiếng Việt',
    'en-US': 'English',
    'fr-FR': 'Français',
    'cmn-Hans-CN': '中文 (Simplified)',
    'cmn-Hant-TW': '中文 (Traditional)',
    'ko-KR': '한국어',
    'th-TH': 'ไทย'
}

# Map legacy codes to Chirp 3 compatible codes
LANGUAGE_CODE_MAPPING = {
    'zh-CN': 'cmn-Hans-CN',
    'zh-TW': 'cmn-Hant-TW',
}

voice_stt_router = APIRouter(prefix="/voice-stt", tags=["voice-stt"])


@voice_stt_router.websocket("/stream")
async def voice_stt_stream(websocket: WebSocket):
    """
    WebSocket endpoint for real-time Speech-to-Text
    User speaks → Sends audio chunks → Returns transcript immediately

    Protocol:
    1. Client sends config: {"language_code": "vi-VN", "appName": "...", "userId": "...", "sessionId": "..."}
    2. Client sends audio chunks: {"audio": "base64_audio_chunk"}
    3. Server returns interim results: {"type": "interim", "transcript": "..."}
    4. Server returns final results: {"type": "final", "transcript": "...", "confidence": 0.95}
    5. Client sends: {"type": "end"} to stop
    """
    await websocket.accept()

    try:
        # Receive config
        config_msg = await websocket.receive_json()
        language_code = config_msg.get("language_code", "vi-VN")
        app_name = config_msg.get("appName", "unknown")
        user_id = config_msg.get("userId", "unknown")
        session_id = config_msg.get("sessionId", "unknown")

        # Map legacy language codes to Chirp 3 compatible codes
        original_language_code = language_code
        if language_code in LANGUAGE_CODE_MAPPING:
            language_code = LANGUAGE_CODE_MAPPING[language_code]
            logger.info(f"Mapped language code: {original_language_code} → {language_code}")

        # Validate language code
        if language_code not in SUPPORTED_LANGUAGES:
            await websocket.send_json({
                "type": "error",
                "message": f"Unsupported language: {original_language_code}. Supported: {list(SUPPORTED_LANGUAGES.keys())}"
            })
            await websocket.close()
            return

        logger.info(f"STT Stream started: app={app_name}, user={user_id}, session={session_id}, lang={language_code} ({SUPPORTED_LANGUAGES[language_code]})")

        # Create STT client
        if GOOGLE_CLOUD_LOCATION == "global":
            client_options = ClientOptions(api_endpoint="speech.googleapis.com")
        else:
            client_options = ClientOptions(api_endpoint=f"{GOOGLE_CLOUD_LOCATION}-speech.googleapis.com")

        client = speech.SpeechClient(client_options=client_options)

        # Streaming recognition config
        recognizer_path = f"projects/{GOOGLE_CLOUD_PROJECT}/locations/{GOOGLE_CLOUD_LOCATION}/recognizers/{SPEECH_RECOGNIZER_ID}"

        # Use Chirp 3 for best multilingual support
        model_name = "chirp_3"

        logger.info(f"Using model: {model_name} for language: {language_code} ({SUPPORTED_LANGUAGES[language_code]}) in region: {GOOGLE_CLOUD_LOCATION}")

        streaming_config = speech.StreamingRecognitionConfig(
            config=speech.RecognitionConfig(
                explicit_decoding_config=speech.ExplicitDecodingConfig(
                    encoding=speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                    sample_rate_hertz=16000,
                    audio_channel_count=1,
                ),
                language_codes=[language_code],
                model=model_name,
                features=speech.RecognitionFeatures(
                    enable_automatic_punctuation=True,
                    enable_word_time_offsets=False,
                ),
            ),
            streaming_features=speech.StreamingRecognitionFeatures(
                interim_results=True,  # Enable interim results
                enable_voice_activity_events=True,  # Enable VAD for better silence detection
                voice_activity_timeout=speech.StreamingRecognitionFeatures.VoiceActivityTimeout(
                    speech_end_timeout=timedelta(seconds=13)  # Finalize after 13 seconds of silence
                ),
            ),
        )

        # Generator to yield audio chunks from WebSocket (sync generator)
        def audio_generator():
            # First request with config
            yield speech.StreamingRecognizeRequest(
                recognizer=recognizer_path,
                streaming_config=streaming_config,
            )

            # Note: This generator will be consumed synchronously by streaming_recognize
            # We'll handle WebSocket receiving separately

        # Create a queue for audio chunks
        import asyncio
        from queue import Queue
        import threading

        audio_queue = Queue()
        stop_event = threading.Event()

        # Accumulate all final transcripts for continuous conversation
        accumulated_transcript = []

        # Generator that pulls from queue
        def request_generator():
            try:
                # First request with config
                yield speech.StreamingRecognizeRequest(
                    recognizer=recognizer_path,
                    streaming_config=streaming_config,
                )

                # Stream audio chunks from queue
                while not stop_event.is_set():
                    try:
                        chunk = audio_queue.get(timeout=0.5)
                        if chunk is None:  # Sentinel value
                            break
                        yield speech.StreamingRecognizeRequest(audio=chunk)
                    except GeneratorExit:
                        # Generator is being closed, cleanup and exit gracefully
                        logger.debug("Generator closed by client")
                        break
                    except Exception:
                        continue
            except GeneratorExit:
                # Handle generator close during initialization
                logger.debug("Generator closed during initialization")
                pass

        # Task to receive audio from WebSocket
        async def receive_audio():
            try:
                while True:
                    message = await websocket.receive_json()

                    if message.get("type") == "end":
                        logger.info("Client requested end of stream")
                        audio_queue.put(None)  # Sentinel
                        stop_event.set()
                        break

                    if "audio" in message:
                        audio_chunk = base64.b64decode(message["audio"])
                        audio_queue.put(audio_chunk)

            except WebSocketDisconnect:
                logger.info("WebSocket disconnected during receive")
                audio_queue.put(None)
                stop_event.set()
            except Exception as e:
                logger.error(f"Error receiving audio: {e}")
                audio_queue.put(None)
                stop_event.set()

        # Task to process STT responses
        async def process_responses():
            try:
                # Run streaming_recognize in thread pool (it's blocking)
                loop = asyncio.get_event_loop()

                # Response queue to communicate between threads
                from queue import Queue
                response_queue = Queue()

                def run_streaming():
                    try:
                        # Stream responses one by one
                        for response in client.streaming_recognize(requests=request_generator()):
                            response_queue.put(("response", response))
                        # Stream ended naturally (silence timeout or max duration)
                        # Don't send "done" yet - check if we should restart
                        response_queue.put(("stream_ended", None))
                    except Exception as e:
                        logger.error(f"Streaming recognize error: {e}\n{traceback.format_exc()}")
                        response_queue.put(("error", str(e)))

                # Start streaming in background thread
                import concurrent.futures
                executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                stream_executor = executor.submit(run_streaming)  # Keep reference to prevent GC

                # Process responses as they come
                while True:
                    # Check queue non-blocking
                    try:
                        msg_type, data = response_queue.get(timeout=0.1)

                        if msg_type == "stream_ended":
                            # Stream ended - send final notification and close
                            logger.info("STT stream ended. Sending session_ended and closing.")

                            try:
                                full_transcript = " ".join(accumulated_transcript)
                                await websocket.send_json({
                                    "type": "session_ended",
                                    "message": "Session ended after 13 seconds of silence",
                                    "full_transcript": full_transcript
                                })
                            except Exception as e:
                                logger.warning(f"Failed to send session_ended: {e}")

                            stop_event.set()
                            break
                        elif msg_type == "error":
                            logger.error(f"STT error: {data}")
                            break
                        elif msg_type == "response":
                            response = data

                            # Check if WebSocket is still open
                            if websocket.client_state.value != 1:  # 1 = CONNECTED
                                logger.info("WebSocket closed, stopping response processing")
                                stop_event.set()
                                break

                            # Handle voice activity events
                            if response.speech_event_type:
                                event_type = response.speech_event_type
                                event_type_name = speech.StreamingRecognizeResponse.SpeechEventType(event_type).name
                                logger.info(f"Voice activity event: {event_type_name} ({event_type})")

                                try:
                                    # SPEECH_EVENT_SPEECH_BEGIN = 2
                                    if event_type == 2:
                                        await websocket.send_json({
                                            "type": "speech_start",
                                            "message": "Speech detected"
                                        })
                                    # SPEECH_EVENT_SPEECH_END = 3
                                    elif event_type == 3:
                                        # Speech segment ended - but don't close session yet
                                        # Let the stream timeout handle actual session close after 13s silence
                                        logger.info("Speech segment ended. Waiting for 13s silence timeout...")
                                        await websocket.send_json({
                                            "type": "speech_end",
                                            "message": "Speech segment ended"
                                        })
                                except Exception as send_error:
                                    logger.warning(f"Failed to send VAD event: {send_error}")
                                continue

                            if not response.results:
                                continue

                            result = response.results[0]

                            if not result.alternatives:
                                continue

                            alternative = result.alternatives[0]
                            transcript = alternative.transcript

                            # Send interim or final result
                            try:
                                if result.is_final:
                                    confidence = alternative.confidence
                                    detected_language = result.language_code if hasattr(result, 'language_code') else language_code

                                    # Add to accumulated transcript
                                    accumulated_transcript.append(transcript)
                                    full_transcript = " ".join(accumulated_transcript)

                                    logger.info(f"Final segment: {transcript} ({confidence:.2%})")
                                    logger.info(f"Accumulated: {full_transcript}")

                                    await websocket.send_json({
                                        "type": "final",
                                        "transcript": transcript,  # Current segment
                                        "full_transcript": full_transcript,  # All segments combined
                                        "confidence": confidence,
                                        "detected_language": detected_language
                                    })
                                else:
                                    # For interim results, show current + accumulated
                                    if accumulated_transcript:
                                        combined = " ".join(accumulated_transcript) + " " + transcript
                                    else:
                                        combined = transcript

                                    logger.debug(f"Interim: {transcript}")

                                    await websocket.send_json({
                                        "type": "interim",
                                        "transcript": transcript,  # Current interim
                                        "full_transcript": combined  # Accumulated + current interim
                                    })
                            except Exception as send_error:
                                logger.warning(f"Failed to send response: {send_error}")
                                stop_event.set()
                                break

                    except:
                        # Queue empty, continue waiting
                        await asyncio.sleep(0.01)

                        # Check if we should stop
                        if stop_event.is_set():
                            break

            except Exception as e:
                logger.error(f"Error processing responses: {e}\n{traceback.format_exc()}")

        # Run both tasks concurrently
        await asyncio.gather(
            receive_audio(),
            process_responses()
        )

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"STT Stream error: {e}\n{traceback.format_exc()}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except:
            pass
    finally:
        try:
            await websocket.close()
        except:
            pass


@voice_stt_router.get("/health")
async def health_check():
    """Health check"""
    return {
        "status": "healthy",
        "service": "voice-stt",
        "google_cloud_project": GOOGLE_CLOUD_PROJECT,
        "location": GOOGLE_CLOUD_LOCATION
    }
