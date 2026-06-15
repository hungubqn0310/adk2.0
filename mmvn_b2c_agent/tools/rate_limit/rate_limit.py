# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Callback functions for rate limit."""
import datetime
import json
import logging
from typing import Optional
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse

from mmvn_b2c_agent.shared.config_service import config_service

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Adjust these values to limit the rate at which the agent
# queries the LLM API.


class RateLimitError(Exception):
    """Custom exception for rate limit violations."""

    def __init__(self, message: str):
        self.message = message

    def __str__(self):
        return self.formatted_message

    def __repr__(self):
        return self.formatted_message

    @property
    def formatted_message(self):
        return json.dumps(json.dumps({
            "code": "MINUTELY_RATE_LIMIT_EXCEEDED",
            "message": self.message
        })).strip('\"')


async def rate_limit_callback(
        callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
    """Callback function that implements a query rate limit.

    Args:
      callback_context: A CallbackContext object representing the active
              callback context.
      llm_request: A LlmRequest object representing the active LLM request.
    """
    # now = time.time()
    # if "timer_start" not in callback_context.state:
    #     callback_context.state["timer_start"] = now
    #     callback_context.state["request_count"] = 1
    #     logger.debug(
    #         "rate_limit_callback [timestamp: %i, req_count: 1, "
    #         "elapsed_secs: 0]",
    #         now,
    #     )
    #     return None
    #
    # request_count = callback_context.state["request_count"] + 1
    # elapsed_secs = now - callback_context.state["timer_start"]
    # logger.debug(
    #     "rate_limit_callback [timestamp: %i, request_count: %i,"
    #     " elapsed_secs: %i]",
    #     now,
    #     request_count,
    #     elapsed_secs,
    # )
    # print(f"Rate limit check: {request_count} requests in {elapsed_secs:.2f} seconds")
    #
    # if request_count > RPM_QUOTA:
    #     delay = RATE_LIMIT_SECS - elapsed_secs + 1
    #     if delay > 0:
    #         # logger.debug("Sleeping for %i seconds", delay)
    #         # time.sleep(delay)
    #         # Return a response to block the request
    #         return LlmResponse(
    #             content=types.Content(
    #                 role="model",
    #                 parts=[types.Part(text="The bot is currently rate-limited. Please try again later.")],
    #             )
    #         )
    #     callback_context.state["timer_start"] = now
    #     callback_context.state["request_count"] = 1
    # else:
    #     callback_context.state["request_count"] = request_count
    last_user_message_text = ""
    if llm_request.contents:
        for content in reversed(llm_request.contents):
            if content.role == 'user' and content.parts:
                if any(part.function_response for part in content.parts):
                    # Skip if there is a function response in any part
                    continue
                last_user_message_text = '\n'.join(part.text for part in content.parts if part.text)
                break
    # limit input message length to avoid extreme cases
    if len(last_user_message_text) > config_service.max_user_message_length:
        raise Exception("Tin nhắn của anh/chị quá dài. Vui lòng rút ngắn tin nhắn và thử lại.")
    if not callback_context.session.state.get("input_token_count"):
        return None

    # limit new message's input token count
    token_count = callback_context.session.state["input_token_count"]
    if token_count > config_service.token_warning_limit:
        pass
    if token_count > config_service.token_hard_limit:
        raise Exception(f"Cuộc hội thoại đã vượt quá giới hạn cho phép. Anh/chị vui lòng mở cuộc trò chuyện mới để tiếp tục.")

    # todo: we can intercept the 429 error instead
    # Token per minute limit
    last_minute_events = [
        event for event in callback_context.session.events
        if event.timestamp >= (datetime.datetime.now() - datetime.timedelta(minutes=1)).timestamp()
    ]
    past_minute_usage = [event.usage_metadata for event in last_minute_events]
    past_minute_input_tokens = sum(usage.prompt_token_count for usage in past_minute_usage if usage)
    if past_minute_input_tokens > config_service.token_per_minutes_limit:
        raise RateLimitError(f"Anh/chị đang gửi tin nhắn quá nhanh. Vui lòng đợi một chút rồi thử lại.")

    return None
