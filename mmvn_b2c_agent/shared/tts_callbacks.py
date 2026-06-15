import time
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.genai.types import Part
from gtts import gTTS
from io import BytesIO
from markdown import Markdown
from io import StringIO


def unmark_element(element, stream=None):
    if stream is None:
        stream = StringIO()
    if element.text:
        stream.write(element.text)
    for sub in element:
        unmark_element(sub, stream)
    if element.tail:
        stream.write(element.tail)
    return stream.getvalue()


# patching Markdown
Markdown.output_formats["plain"] = unmark_element
__md = Markdown(output_format="plain")
__md.stripTopLevelTags = False


def unmark(text):
    return __md.convert(text)


def text_to_speech(text: str) -> bytes:
    """Converts text to speech using gTTS and returns the audio as bytes."""
    s_time = time.perf_counter()
    text = unmark(text)  # Remove any formatting or special characters
    tts = gTTS(text, lang='vi', tld='com.vn')
    fp = BytesIO()
    tts.write_to_fp(fp)
    fp.seek(0)
    content = fp.read()
    print(f"Text to speech conversion took {time.perf_counter() - s_time:.2f} seconds")
    return content


def tts_after_model_callback(
        callback_context: CallbackContext, llm_response: LlmResponse
) -> LlmResponse:
    """Converts the model's text output to speech and modifies the response."""
    print("Running TTS after model callback...")
    # if not llm_response.content or not llm_response.content.parts:
    #     # If no content, return the original llm_response or an empty one
    #     return llm_response
    #
    # text_parts = [part for part in llm_response.content.parts
    #               if part.text
    #               and not part.function_response
    #               and not part.function_call]
    #
    # text_response = "".join(part.text for part in text_parts)
    # if not text_response:
    #     return llm_response
    #
    # audio_blob = text_to_speech(text_response)
    #
    # # Create a new Content object with both the original text and the new audio
    # new_part = Part.from_bytes(data=audio_blob, mime_type="audio/mpeg")
    # llm_response.content.parts.append(new_part)
    #
    # return llm_response
