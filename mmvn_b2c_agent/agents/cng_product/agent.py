import asyncio
import json
import logging
import time
import traceback
import mmvn_b2c_agent.agents.cng_product.schema as cng_product_schemas
from typing import AsyncGenerator
from google.adk.agents import BaseAgent, LlmAgent, InvocationContext
from google.adk.agents.context import Context as ToolContext
from google.adk.events import Event, EventActions
from google.adk.tools.set_model_response_tool import SetModelResponseTool
from google.genai import types
from typing_extensions import override
from mmvn_b2c_agent.agents.cng.schema import CngProductSearchAiResponse, ProductSkuNotFoundError, \
    CngProductSearchAiResponseFinal
from mmvn_b2c_agent.agents.cng_product.prompts import (
    CNG_PRODUCT_SUPPORT_AGENT_DESCRIPTION,
    PRODUCT_FILTER_ONLY_PROMPT,
    PRODUCT_SEARCH_SEMANTIC_PROMPT,
)
from mmvn_b2c_agent.shared.constants import MODEL_GEMINI_3_1_FLASH_LITE, DEFAULT_RETRY_OPTION, MODEL_GEMINI_3_FLASH, GEMINI_BASE_URL, get_thinking_config
from mmvn_b2c_agent.shared.safety import SAFETY_FILTER_CONFIG
from mmvn_b2c_agent.shared.callbacks import debug_log_llm_request, handle_malformed_response, handle_raw_audio_input, strip_old_image_data, inject_file_upload_context
from mmvn_b2c_agent.shared.schema import MagentoMainCategories
from mmvn_b2c_agent.tools.cng.product import UnifiedProductSearchTool
from mmvn_b2c_agent.tools.cng.product.product_discount import GetProductsDiscountTool
from mmvn_b2c_agent.tools.cng.product.product_bestselling import BestSellingProductsTool
logger = logging.getLogger(__name__)




"""
How it should work now:
product_search_response_generator generates function calls, the content of that event will be emptied so that it will not show up in chat history. To push the search param to Front-end, we edit the event and put the function call into event.action.state_delta['last_search_queries'].
The search agent will end after that, no functionResponse is generated. Instead we call the tools directly in Python and get the results, save them into ctx.session.state['last_search_result'].
The filter agent will have a dynamic prompt to read ctx.session.state['last_search_result'] and generate the final response.
This will ensure that the api response will not be in the chat history, but the filter result will be.
"""

# --- Custom Agent ---
class ProductSearchAgent(BaseAgent):
# class ProductSearchAgent(LlmAgent):
    """
    Gemini don't support structured output with tools, this is an experimental agent to work around that.
    It tries to solve the problem by having the `product_search` agent to call the tools and get the raw data,
    then the `response_generator` agent will filter and output the result in structured format.
    """

    # planner: LlmAgent
    product_search: LlmAgent
    response_generator: LlmAgent
    # sequential_agent: SequentialAgent
    # model_config allows setting Pydantic configurations if needed, e.g., arbitrary_types_allowed
    model_config = {"arbitrary_types_allowed": True}

    def __init__(
            self,
            name: str,
            product_search: LlmAgent,
            response_generator: LlmAgent,
            **kwargs
    ):
        sub_agents_list = [
            product_search,
            response_generator,
        ]

        # Pydantic will validate and assign them based on the class annotations.
        super().__init__(
            name=name,
            product_search=product_search,
            response_generator=response_generator,
            sub_agents=sub_agents_list,  # Pass the sub_agents list directly
            **kwargs
        )

    @override
    async def _run_live_impl(
            self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        raise NotImplementedError("Live mode is not implemented for ProductSearchAgent.")

    @override
    async def _run_async_impl(
            self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        s_time = time.perf_counter()
        logger.info(f"[{self.name}] Starting product search workflow.")

        # clear old search history
        if not ctx.session.state:
            ctx.session.state = {}
        ctx.session.state['searched_products'] = []

        # begin calling tools
        logger.info(f"[{self.name}] Running product_search...")

        # DEBUG: Log context size trước khi gọi LLM
        # Note: ctx.session doesn't have 'history' attribute in ADK
        # History is managed internally by the LLM flow
        if ctx.session.state:
            state_size = len(json.dumps(ctx.session.state))
            logger.info(f"[DEBUG] Session state size: {state_size} chars, Est. tokens: ~{state_size // 4}")
            # Log state keys để biết có gì trong state
            logger.debug(f"[DEBUG] Session state keys: {list(ctx.session.state.keys())}")

            # Warning nếu context quá lớn
            if state_size > 12000:  # ~3000 tokens
                logger.warning(f"[WARNING] Session state is very large ({state_size} chars, ~{state_size // 4} tokens). "
                             f"This may cause MALFORMED_FUNCTION_CALL errors.")

        search_queries = []
        tool_events = []
        search_start_time = time.perf_counter()
        async for event in self.product_search.run_async(ctx):
            if not event.content or not event.content.parts:
                logger.error(f"[{self.name}] Empty event from LLM: "
                             f"{event.model_dump_json(indent=2, exclude_none=True)}")

                # DEBUG: Log thêm thông tin chi tiết khi MALFORMED_FUNCTION_CALL
                if hasattr(event, 'candidates') and event.candidates:
                    for idx, candidate in enumerate(event.candidates):
                        logger.error(f"[DEBUG] Candidate {idx}: finish_reason={candidate.finish_reason}")
                        if hasattr(candidate, 'content') and candidate.content:
                            logger.error(f"[DEBUG] Candidate {idx} content: {candidate.content}")
                        if hasattr(candidate, 'grounding_metadata'):
                            logger.error(f"[DEBUG] Candidate {idx} grounding: {candidate.grounding_metadata}")

                # DEBUG: Log raw response nếu có
                if hasattr(event, 'sdk_http_response'):
                    logger.error(f"[DEBUG] Raw HTTP response headers: {event.sdk_http_response.get('headers', {})}")

                # DEBUG: Log usage metadata
                if event.usage_metadata:
                    logger.error(f"[DEBUG] Token usage - Prompt: {event.usage_metadata.prompt_token_count}, "
                               f"Candidates: {event.usage_metadata.candidates_token_count if hasattr(event.usage_metadata, 'candidates_token_count') else 0}, "
                               f"Total: {event.usage_metadata.total_token_count}")

                continue
            logger.info(
                f"[{self.name}] Event from product_search: {event.model_dump_json(indent=2, exclude_none=True)}")
            tool_events.append(event)
            for part in event.content.parts:
                if part.text:
                    part.thought = True
            if not event.actions.state_delta:
                event.actions.state_delta = {}
            print(f"[DEBUG] is func response: {bool(event.get_function_responses())}")
            # Edit the functionCall event to skip saving it into chat history, while still allowing front end to see it.
            # NOTE: gemini-3 thường kèm 1 text/thought part rỗng cạnh function calls, nên KHÔNG
            # dùng all() (sẽ False) — dùng any() và lọc riêng các part có function_call.
            # CHỈ bắt event ĐÃ HOÀN CHỈNH (not partial): khi streaming, function calls có thể
            # đến qua nhiều partial event; bắt+break trên partial sẽ cắt thiếu query (chỉ lấy 1).
            if not event.partial and event.get_function_calls():
                search_queries = event.get_function_calls()
                try:
                    queries = [query.args for query in search_queries]
                except json.JSONDecodeError:
                    # todo: what to do if the AI generate invalid JSON?
                    logger.error(f"[DEBUG] Invalid JSON generated by LLM for search queries.")
                    queries = []
                for query in queries:
                    if query.get('category'):
                        query['category_name'] = query['category']
                        # Typo corrections
                        typo_corrections = {
                            "dấu ấn - gia vị": "dầu ăn - gia vị - nước chấm",
                            "dầu ăn - gia vị": "dầu ăn - gia vị - nước chấm",
                            "dau an - gia vi": "dầu ăn - gia vị - nước chấm",
                            "dầu ăn": "dầu ăn - gia vị - nước chấm",
                            "gia vị": "dầu ăn - gia vị - nước chấm",
                        }
                        normalized_cats = []
                        for cate in query['category']:
                            cate_lower = cate.lower()
                            # Apply corrections if needed
                            if cate_lower in typo_corrections:
                                cate_lower = typo_corrections[cate_lower]
                            try:
                                normalized_cats.append(MagentoMainCategories(cate_lower).name)
                            except ValueError:
                                logger.warning(f"[agent] Category '{cate}' not found in MagentoMainCategories")
                        query['category'] = normalized_cats
                # Attach original user query to each search query for dashboard tracking
                try:
                    original_query = None
                    if ctx.user_content and ctx.user_content.parts:
                        original_query = next(
                            (p.text for p in ctx.user_content.parts if p.text), None
                        )
                    if original_query:
                        for query in queries:
                            query['original_query'] = original_query
                except Exception:
                    pass
                last_search_queries_json = json.dumps(queries, ensure_ascii=False)
                event.actions.state_delta['last_search_queries'] = last_search_queries_json
                # Ghi thẳng vào session.state để response_generator (instruction template
                # dùng {last_search_queries}) luôn đọc được — tránh KeyError khi ta break
                # sớm và runner chưa kịp apply state_delta.
                ctx.session.state['last_search_queries'] = last_search_queries_json
                # This will make is_final_response() return True and stop the agent
                event.content = None
                # IMPORTANT: dừng tiêu thụ inner agent ngay tại đây. Nếu tiếp tục vòng for,
                # ADK sẽ TỰ execute UnifiedProductSearchTool và vì mode=ANY ép gọi function
                # liên tục → lặp vô hạn (đã thấy 35+ vòng / 1 request). Ta tự chạy search
                # bằng Python (asyncio.gather) ở dưới nên không cần ADK execute tool.
                yield event
                break

            # elif all([p.function_response is not None for p in event.content.parts]):
            #     # A hack to make is_final_response() return True and skip this agent from reading the tool output.
            #     # This will stop the agent from reading the api output, saving some input token cost but
            #     #  can potentially cause error if the agent call one api at a time.
            #     event.actions.skip_summarization = True
            #     all_result = [part.function_response.response
            #                   for part in event.content.parts
            #                   if part.function_response]
            #     search_result_str = '\n'.join(json.dumps(res, ensure_ascii=False) for res in all_result)
            #     print("SETTING search result:")
            #     ctx.session.state['last_search_result'] = search_result_str
            #     event.actions.state_delta['last_search_result'] = search_result_str
            #     event.content = None
            yield event

        # Perform product search from the queries generated by LLM, then save the result into session state.
        if search_queries:
            # Emit status: searching products
            keyword_previews = []
            for q in search_queries:
                kw = (q.args or {}).get('keyword_in_vietnamese') or (q.args or {}).get('keyword', '')
                if kw:
                    keyword_previews.append(kw)
            search_status = f"Đang tìm kiếm: {', '.join(keyword_previews[:3])}" if keyword_previews else "Đang tìm kiếm sản phẩm..."
            yield Event(
                author=self.name,
                actions=EventActions(state_delta={'ai_thinking_status': search_status}),
                invocation_id=ctx.invocation_id,
            )
            # ADK 2.x: BaseTool.run_async requires ToolContext (=Context), not InvocationContext
            tool_ctx = ToolContext(invocation_context=ctx)
            tasks = [
                # todo assuming the search agent have only ProductSearchTool. Once all other tools are removed, delete this comment.
                asyncio.create_task(UnifiedProductSearchTool().run_async(args=query.args, tool_context=tool_ctx))
                for query in search_queries
            ]
            await asyncio.gather(*tasks)
            search_result = [res['result'] for res in ctx.session.state.get('search_result_history', {}).values()
                             if res.get('invocation_id') == ctx.invocation_id]
            # filter duplicated products
            unique_products = {
                prod['sku']: prod for res in search_result for prod in res
            }
            # ensure_ascii=False is IMPORTANT to keep Vietnamese characters
            search_result_str = '\n'.join(json.dumps(prod, ensure_ascii=False) for prod in unique_products.values())
        else:
            # todo: What to do when the AI did not generate any search query?
            logger.error(f"[DEBUG] No search queries generated by LLM.\n")
            search_result_str = "No products found."
        ctx.session.state['last_search_result'] = search_result_str
        ctx.session.state['last_search_product_count'] = len(unique_products)
        # Per-keyword product count for accurate metrics: keyword → product_count
        per_kw_counts = {}
        for args_key, res in ctx.session.state.get('search_result_history', {}).items():
            if res.get('invocation_id') != ctx.invocation_id:
                continue
            try:
                args_dict = json.loads(args_key)
                kw = args_dict.get('keyword_in_vietnamese') or args_dict.get('keyword')
                if kw:
                    per_kw_counts[kw] = res.get('product_count', 0)
            except Exception:
                pass
        ctx.session.state['last_search_per_kw_counts'] = per_kw_counts
        actions = EventActions(state_delta=ctx.session.state)
        search_result_event = Event(
            author=self.product_search.name,
            actions=actions,
            invocation_id=ctx.invocation_id,
        )
        # IMPORTANT: yield the event so ADK will save the session state changes.
        yield search_result_event
        search_end_time = time.perf_counter()
        logger.info(f"[PERF LOG] Product search took {search_end_time - search_start_time:.3f} seconds.")

        filter_start_time = time.perf_counter()
        logger.info(f"[{self.name}] Running response_generator...")
        async for event in self.response_generator.run_async(ctx):
            logger.info(
                f"[{self.name}] Event from response_generator: {event.model_dump_json(indent=2, exclude_none=True)}")
            if event.usage_metadata:
                last_token_count = event.usage_metadata.prompt_token_count
                event.actions.state_delta.update({
                    "input_token_count": last_token_count
                })
            for func_response in event.get_function_responses():
                if func_response.name == 'set_model_response':
                        try:
                            raw_output = func_response.response

                            # DETERMINISTIC LANGUAGE GUARD (do NOT trust the LLM here).
                            # When the user only attached an image/file and typed NO text,
                            # the model reads the English words printed on the product
                            # packaging (e.g. "Ensure", "Vanilla") and wrongly replies in
                            # English. The prompt/schema directives lose to the model's prior,
                            # so force Vietnamese here for both the language field and the
                            # user-facing message.
                            from mmvn_b2c_agent.tools.output_formater.cng_output_format import detect_file_upload
                            _uc = getattr(ctx, 'user_content', None)
                            _user_typed_text = False
                            if _uc is not None and getattr(_uc, 'parts', None):
                                for _p in _uc.parts:
                                    _t = (getattr(_p, 'text', None) or '').strip()
                                    if _t and not _t.startswith('[SYSTEM CONTEXT') and not _t.startswith('[Nội dung file'):
                                        _user_typed_text = True
                                        break
                            if isinstance(raw_output, dict) and detect_file_upload(_uc) and not _user_typed_text:
                                raw_output['user_language'] = 'vi'
                                _msg = (raw_output.get('message') or '')
                                _vi_chars = set('ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ')
                                _looks_vi = ('dạ' in _msg.lower()) or any(c in _vi_chars for c in _msg.lower())
                                if not _looks_vi:
                                    _has_results = bool(raw_output.get('product_skus'))
                                    raw_output['message'] = (
                                        'Dạ, em xin giới thiệu các sản phẩm sau đây ạ:' if _has_results
                                        else 'Dạ, hiện em chưa tìm thấy sản phẩm phù hợp ạ. Anh/chị vui lòng cho em biết thêm thông tin để em hỗ trợ nhé!'
                                    )
                                func_response.response = raw_output
                                logger.info(f"[LANG_GUARD] Forced Vietnamese (file/image-only, no typed text); looks_vi={_looks_vi}")

                            final_response = CngProductSearchAiResponseFinal.from_search_output(
                                raw_output, ctx.session.state
                            )
                            response_dict = final_response.model_dump()

                            # Code-level file upload detection
                            from mmvn_b2c_agent.tools.output_formater.cng_output_format import detect_file_upload
                            user_content = getattr(ctx, 'user_content', None)
                            if detect_file_upload(user_content):
                                response_dict['is_file_upload_response'] = True

                            func_response.response = response_dict
                            # Determine actual product count for dashboard metrics.
                            # A response is "no results" if:
                            # 1. product_skus is empty (AI found nothing), OR
                            # 2. Message signals unavailability + AI is showing substitutes
                            #    (e.g. "chưa có thịt chó" but shows dog food)
                            raw_skus = (raw_output.get('product_skus') or []) if isinstance(raw_output, dict) else []
                            raw_message = (raw_output.get('message') or '').lower() if isinstance(raw_output, dict) else ''
                            _no_result_phrases = ['chưa có', 'không có', 'không tìm thấy', 'hiện chưa', 'hiện không']
                            _is_substitute = bool(raw_skus) and any(p in raw_message for p in _no_result_phrases)
                            actual_product_count = 0 if (not raw_skus or _is_substitute) else len(response_dict.get('product_data') or [])
                            event.actions.state_delta['last_response_product_count'] = actual_product_count
                        except ProductSkuNotFoundError as e:
                            func_response.response = {
                                "success": False,
                                "message": str(e),
                                "instruction_for_agent": "The agent provided an invalid SKU that does not exist in the "
                                                         "search history. Please ensure the SKU is correct and try again.",
                            }
                        except Exception as e:
                            # todo: what to do in this case?
                            logger.error(f"Failed to convert to final schema: {str(e)}")
                            logger.error(traceback.format_exc())
            yield event
        workflow_end_time = time.perf_counter()
        logger.info(f"[PERF LOG] Response generation tooks {workflow_end_time - filter_start_time:.3f} seconds.")
        logger.info(f"[{self.name}] Workflow finished. Total time: {workflow_end_time - s_time:.3f} seconds.")


product_search_tool_caller = LlmAgent(
    name="product_search_tool_caller",
    model=MODEL_GEMINI_3_FLASH,
    # instruction=PRODUCT_SEARCH_SEMANTIC_PROMPT,
    static_instruction=types.Content(parts=[types.Part(text=PRODUCT_SEARCH_SEMANTIC_PROMPT)]),
    generate_content_config=types.GenerateContentConfig(
        temperature=0.1,
        safety_settings=SAFETY_FILTER_CONFIG,
        # gemini-3 mặc định thinking_level='high' (chậm ~4.4s). Hạ xuống 'low' để giảm
        # latency (~2-2.5s) mà vẫn đủ reasoning bung đủ & ổn định keyword. 'minimal' thử
        # rồi nhưng bung thiếu (3 thay vì 4) và bỏ sót cả từ cốt lõi → không dùng.
        thinking_config=get_thinking_config(MODEL_GEMINI_3_FLASH, level="low"),
        http_options=types.HttpOptions(
            api_version='v1alpha',
            base_url=GEMINI_BASE_URL,
            retry_options=DEFAULT_RETRY_OPTION
        ),
        tool_config=types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(
                mode=types.FunctionCallingConfigMode.ANY  # Force the model to only generate function calls
            )
        ),
    ),

    before_model_callback=[handle_raw_audio_input, strip_old_image_data, inject_file_upload_context, debug_log_llm_request],
    after_model_callback=[handle_malformed_response],
    tools=[
        UnifiedProductSearchTool(),
    ],
)

product_search_response_generator = LlmAgent(
    name="product_search_response_generator",
    model=MODEL_GEMINI_3_1_FLASH_LITE,
    instruction="Search queries used:\n{last_search_queries}\n\nSearch result:\n{last_search_result}\n\n",
    static_instruction=types.Content(parts=[types.Part(text=PRODUCT_FILTER_ONLY_PROMPT)]),
    generate_content_config=types.GenerateContentConfig(
        temperature=0.1,
        safety_settings=SAFETY_FILTER_CONFIG,
        http_options=types.HttpOptions(
            api_version='v1alpha',
            base_url=GEMINI_BASE_URL,
            retry_options=DEFAULT_RETRY_OPTION
        ),
        tool_config=types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(
                mode=types.FunctionCallingConfigMode.ANY  # Force the model to only generate function calls
            )
        ),
    ),

    before_model_callback=[handle_raw_audio_input, strip_old_image_data, inject_file_upload_context, debug_log_llm_request],
    after_model_callback=[handle_malformed_response],
    tools=[
        SetModelResponseTool(cng_product_schemas.ProductSearchOutputSchema),
    ],
)

# Custom workflow agent for product search
cng_product_search_workflow_agent = ProductSearchAgent(
    name="cng_product_search_tool",
    description=CNG_PRODUCT_SUPPORT_AGENT_DESCRIPTION,
    product_search=product_search_tool_caller,
    response_generator=product_search_response_generator,
    # input_schema=cng_product_schemas.ProductSearchInputSchema,
)
