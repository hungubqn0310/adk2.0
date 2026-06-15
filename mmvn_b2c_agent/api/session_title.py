import asyncio
import json
import logging
import os
import traceback
from typing import Optional, Any

from fastapi import APIRouter
from fastapi import Request, Response, status
from google.adk.sessions import BaseSessionService, DatabaseSessionService, InMemorySessionService
# ADK 2.0: schema cũ chuyển sang schemas.v0, export tên *V0. DatabaseSessionService
# mặc định vẫn dùng V0 (tables "sessions"/"events") nên alias lại cho tương thích.
from google.adk.sessions.database_session_service import (
    StorageSessionV0 as StorageSession,
    StorageEventV0 as StorageEvent,
)
from google.genai import Client, types
from google.genai.types import HttpOptions, HttpRetryOptions, GenerateContentConfig, Content
from pydantic import BaseModel
from sqlalchemy import text

import mmvn_b2c_agent.api
from mmvn_b2c_agent.telemetry.otel_metrics import get_metrics

logger = logging.getLogger(__name__)
SESSION_STATE_KEY_TO_STORE_TITLE = "session_title"
GENERATE_TITLE_MODEL = "gemini-3.1-flash-lite-preview"
GENERATE_TITLE_SYSTEM_PROMPT = """### Task:
Generate a concise, 3-5 word title summarizing the chat history.

### Guidelines:
- The title should clearly represent the main theme or subject of the conversation.
- Use emojis that enhance understanding of the topic, but avoid quotation marks or special formatting.
- Write the title in the chat's primary language; default to English if multilingual.
- Prioritize accuracy over excessive creativity; keep it clear and simple.
- If the user input is a short phrase (e.g., a single word or code like "g5", "A23", "milk","adbakisbdabsnd", ....), use it **exactly as the title** without modification.
### Output:
JSON format: { "lang": "language of chat", emoji:"appropriate emoji", "title": "concise title" }

### Examples:
- { "lang": "en", emoji:"📉", "title": "Stock Market Trends" },
- { "lang": "en", emoji:"🍪", "title": "Perfect Chocolate Chip Recipe" },
- { "lang": "en", emoji:"🤖", "title": "Artificial Intelligence in Healthcare" },
- { "lang": "en", emoji:"🥩", "title": "Fresh Beef Selection" },
- { "lang": "en", emoji:"🧀", "title": "Cheese Product Inquiry" }
- { "lang": "vn", emoji:"🥬", "title": "Rau Hữu Cơ" },
- { "lang": "th", emoji:"🛒", "title": "สั่งซื้อขั้นต่ำ" },
- { "lang": "jp", emoji:"🍎", "title": "果物の鮮度" },


### Chat History:
<chat_history>
%s
</chat_history>
"""


class GenerateTitleOutputSchema(BaseModel):
    lang: str
    emoji: str
    title: str


class GetSessionTitleRequest(BaseModel):
    page: Optional[int] = 1
    limit: Optional[int] = 20
    user_id: str = "user"
    session_ids: Optional[list[str]] = None


class SearchSessionRequest(BaseModel):
    page: Optional[int] = 1
    limit: Optional[int] = 20
    query: str
    session_ids: Optional[list[str]] = None


class MergeSessionsRequest(BaseModel):
    session_ids: list[str]
    old_user_id: str
    new_user_id: str


class DeleteMultipleSessionsRequest(BaseModel):
    session_ids: Optional[list[str]] = None


async def _generate_session_title(
        session_service: BaseSessionService,
        app_name: str, user_id: str, session_id: str
) -> dict | None:
    """
    Update or create a session with the given state delta.
    If the session exists, append an event to update the state.
    If the session does not exist, create a new session with the initial state.
    :return: The updated or created session.
    """
    try:
        session = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id
        )
        if not session:
            logger.warning(f"Session not found for app {app_name} user {user_id} session {session_id}")
            return None
        if session.state.get(SESSION_STATE_KEY_TO_STORE_TITLE):
            return {
                "session_id": session_id,
                "title": session.state[SESSION_STATE_KEY_TO_STORE_TITLE],
            }

        # If session exists, summarize the session
        logger.info(f"Generating title for session of app {app_name} user {user_id} session {session_id}:")
        # get chat history and construct the parts
        if not session.events or len(session.events) == 0:
            logger.warning(f"No chat history found in session {session_id}")
            return None
        first_user_message = None
        has_user_images = False
        for event in session.events:
            if event.content and event.content.parts and event.content.role == "user":
                # check if this event is truly the first user message
                if any(part for part in event.content.parts if part.function_response):
                    continue
                # get text parts
                text_parts = [part.text for part in event.content.parts if part.text]
                # check if there are any image parts (inline_data or file_data)
                has_user_images = any(
                    part.inline_data or part.file_data
                    for part in event.content.parts
                )

                if text_parts:
                    first_user_message = "\n".join(text_parts)

                break

        message_for_title = None

        if has_user_images and not first_user_message:
            # User sent only images without any text - use bot's response
            for event in session.events:
                if event.content and event.content.parts and event.content.role == "model":
                    text_parts = [part.text for part in event.content.parts if part.text]
                    if text_parts:
                        message_for_title = "\n".join(text_parts)
                        break
        else:
            # Use user's message (if exists)
            message_for_title = first_user_message

        if not message_for_title:
            logger.warning(f"No message found for title generation in session {session_id}")
            return None

        # construct the full prompt
        parts = [types.Part(text=GENERATE_TITLE_SYSTEM_PROMPT % message_for_title)]
        pass

        # Build config
        config = GenerateContentConfig(
            temperature=0.4,
            response_mime_type="application/json",
            # response_json_schema=output_schema,
            response_json_schema=GenerateTitleOutputSchema.model_json_schema(),
        )
        # make the request
        client = Client(
            vertexai=False,
            http_options=HttpOptions(
                api_version='v1alpha',
                base_url=os.getenv("GOOGLE_GEMINI_BASE_URL"),
                retry_options=HttpRetryOptions(initial_delay=0.25, attempts=3),
            )
        )
        async with client.aio as async_client:
            llm_response = await async_client.models.generate_content(
                model=GENERATE_TITLE_MODEL,
                contents=Content(role="user", parts=parts),
                config=config,
            )
            logger.info(f"\nGenerated title AI response:\n{'-' * 50}\n{llm_response.text}")
            try:
                usage = llm_response.usage_metadata
                if usage:
                    get_metrics().record_tokens(
                        input_tokens=getattr(usage, 'prompt_token_count', 0) or 0,
                        output_tokens=getattr(usage, 'candidates_token_count', 0) or 0,
                        model=GENERATE_TITLE_MODEL,
                        cached_tokens=getattr(usage, 'cached_content_token_count', 0) or 0,
                        agent_name="session_title",
                        session_id=session_id,
                        user_id=user_id,
                    )
            except Exception as _metrics_err:
                logger.debug(f"Failed to record title generation metrics: {_metrics_err}")
            output = GenerateTitleOutputSchema.model_validate(json.loads(llm_response.text))
        result = {
            "session_id": session_id,
            "title": f"{output.title}",
        }
        await mmvn_b2c_agent.api.update_session(session_service, app_name, user_id, session_id, auto_create=False,
                                                state_delta={SESSION_STATE_KEY_TO_STORE_TITLE: result["title"]})
        return result
    except Exception as e:
        logger.error(f"Error generating session title: {e}\n{traceback.format_exc()}")
        raise e


def sort_session(raw_sessions):
    """
    session.last_update_time return the last event time, including state delta events.
    We need to sort by the last event with content instead.
    :param raw_sessions:
    :return:
    """

    def get_last_content_time(sess):
        try:
            if sess.events and len(sess.events) >= 1:
                for event in reversed(sess.events):
                    if event.content and event.content.parts:
                        return event.timestamp
            return sess.last_update_time
        except Exception as e:
            logger.error(f"sort_session: sess {sess.id}: {str(e)}\n{traceback.format_exc()}")
            return sess.last_update_time

    return sorted(raw_sessions, reverse=True, key=lambda sess: get_last_content_time(sess))


def setup_session_title_api(session_service: BaseSessionService):
    router = APIRouter()

    @router.post("/apps/{app_name}/session_title")
    async def generate_session_title(app_name: str, body: GetSessionTitleRequest, request: Request, response: Response):
        """
        Update the session state for a given app, user, and session ID.
        """
        try:
            user_id = body.user_id
            session_ids = body.session_ids
            page = body.page
            limit = body.limit
            if page < 1:
                response.status_code = status.HTTP_400_BAD_REQUEST
                return {"success": False, "error_message": "page must be >= 1"}
            if limit < 1 or limit > 100:
                response.status_code = status.HTTP_400_BAD_REQUEST
                return {"success": False, "error_message": "limit must be between 1 and 100"}
            if user_id is None and session_ids is None:
                response.status_code = status.HTTP_400_BAD_REQUEST
                return {"success": False, "error_message": "Either user_id or session_ids must be provided"}
            # get all sessions to return
            sessions_to_returns = []
            if session_ids:
                # get sessions from ids
                for session_id in session_ids:
                    ses = await session_service.get_session(
                        app_name=app_name,
                        user_id=user_id or 'user',
                        session_id=session_id
                    )
                    if ses:
                        sessions_to_returns.append(ses)
            else:
                if not user_id:
                    response.status_code = status.HTTP_400_BAD_REQUEST
                    return {"success": False, "error_message": "Either user_id or session_ids must be provided"}
                if user_id == 'user':
                    response.status_code = status.HTTP_400_BAD_REQUEST
                    return {"success": False, "error_message": "Session ids are required for guest user."}
                # get all sessions for the user
                all_sessions = await session_service.list_sessions(
                    app_name=app_name,
                    user_id=user_id,
                )
                if not all_sessions.sessions:
                    sessions_to_returns = []
                else:
                    sessions_to_returns = [sess for sess in all_sessions.sessions]

            sessions_to_returns = sort_session(sessions_to_returns)
            sessions_to_returns = sessions_to_returns[(page - 1) * limit: page * limit]
            # noinspection PyShadowingNames
            logger.debug(
                f"[DEBUG]: client request app name: {app_name}, user_id: {user_id}, session_ids: {session_ids}\n"
                f"found {len(sessions_to_returns)} sessions to returns: {[sess.id for sess in sessions_to_returns]}"
            )
            result = [
                {
                    "session_id": sess.id,
                    "title": sess.state.get(SESSION_STATE_KEY_TO_STORE_TITLE),
                    "token_count": sess.state.get("input_token_count"),
                }
                for sess in sessions_to_returns
            ]
            # get all sessions without title and generate titles for them
            sessions_to_process = [sess for sess in sessions_to_returns
                                   if SESSION_STATE_KEY_TO_STORE_TITLE not in sess.state]
            if sessions_to_process:
                logger.info(f"{len(sessions_to_process)} sessions need title generation.")
                tasks = [
                    asyncio.create_task(
                        _generate_session_title(
                            session_service,
                            app_name,
                            sess.user_id,
                            sess.id
                        )
                    )
                    for sess in sessions_to_process
                ]
                process_result = await asyncio.gather(*tasks)
                process_result_map = {
                    res["session_id"]: res["title"]
                    for res in process_result
                    if res is not None
                }
                for sess_data in result:
                    if sess_data["session_id"] in process_result_map:
                        sess_data["title"] = process_result_map[sess_data["session_id"]]
            logger.info(f"result titles: {result}")
            return result
        except Exception as e:
            logger.error(f"Error generating session title for app {app_name} "
                         f"user {body.user_id} sessions {body.session_ids}: "
                         f"{e}\n{traceback.format_exc()}")
            response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return {"success": False, "error_message": str(e)}

    @router.post("/apps/{app_name}/users/{user_id}/session_search")
    async def search_session(app_name: str, user_id: str,
                             request: Request, response: Response,
                             body: Optional[SearchSessionRequest | None] = None,
                             ):
        try:
            pass
            keyword = body.query
            limit = body.limit or 20
            page = body.page or 1
            session_ids = body.session_ids
            # verify inputs
            if not keyword or len(keyword.strip()) == 0:
                response.status_code = status.HTTP_400_BAD_REQUEST
                return {"success": False, "error_message": "Query keyword must not empty."}
            if user_id == 'user' and (not body.session_ids or len(body.session_ids) < 1):
                response.status_code = status.HTTP_400_BAD_REQUEST
                return {"success": False, "error_message": "Session ids are required for guest user."}
            if page < 1:
                response.status_code = status.HTTP_400_BAD_REQUEST
                return {"success": False, "error_message": "page must be >= 1"}
            if limit < 1 or limit > 100:
                response.status_code = status.HTTP_400_BAD_REQUEST
                return {"success": False, "error_message": "limit must be between 1 and 100"}

            offset = ((page or 1) - 1) * limit
            # get matching first, then fuzzy later
            # search by title
            if isinstance(session_service, InMemorySessionService):
                logger.info(f"Searching sessions with in-memory session service")
                all_sessions = await session_service.list_sessions(
                    app_name=app_name,
                    user_id=user_id,
                )
                matching_sessions = [
                    sess for sess in all_sessions.sessions
                    if keyword.lower() in sess.state.get(SESSION_STATE_KEY_TO_STORE_TITLE, '').lower()
                       and (not session_ids or sess.id in session_ids)
                ]
            elif isinstance(session_service, DatabaseSessionService):
                pass
                logger.info(f"Searching sessions with database session service")
                with session_service.database_session_factory() as sql_session:
                    with sql_session.begin():
                        sql = f"""
                        WITH latest_event AS (
                            SELECT
                                e.app_name,
                                e.user_id,
                                e.session_id,
                                MAX(e.timestamp) AS latest_event_time
                            FROM {StorageEvent.__tablename__} e
                            WHERE e.content IS NOT NULL
                            GROUP BY e.app_name, e.user_id, e.session_id
                        )
                        SELECT
                            s.*,
                            COALESCE(le.latest_event_time, s.create_time) AS sort_time
                        FROM {StorageSession.__tablename__} s
                        LEFT JOIN latest_event le
                            ON le.app_name = s.app_name
                           AND le.user_id = s.user_id
                           AND le.session_id = s.id
                        WHERE
                            (s.state->>'session_title' ILIKE '%' || :keyword || '%'
                            OR EXISTS (
                                SELECT 1
                                FROM {StorageEvent.__tablename__} e
                                WHERE e.app_name = s.app_name
                                  AND e.user_id = s.user_id
                                  AND e.session_id = s.id
                                  AND e.content IS NOT NULL
                                  AND EXISTS (
                                      SELECT 1
                                      FROM jsonb_array_elements(e.content->'parts') AS part
                                      WHERE part->>'text' ILIKE '%' || :keyword || '%'
                                  )
                            ))
                            AND s.app_name = :app_name
                            AND s.user_id = :user_id
                            {f"AND s.id IN :session_ids" if session_ids and len(session_ids) > 0 else ""}
                        ORDER BY sort_time DESC
                        LIMIT :limit OFFSET :offset;
                        """
                        params: dict[str, Any] = {
                            'app_name': app_name,
                            'user_id': user_id,
                            'keyword': keyword,
                            'limit': limit,
                            'offset': offset,
                        }
                        if session_ids and len(session_ids) > 0:
                            params['session_ids'] = tuple(session_ids)
                        stmt = text(sql).bindparams(**params)
                        # Execute
                        results = sql_session.execute(stmt).all()
                        res = [
                            {
                                "session_id": sess.id,
                                "title": sess.state.get(SESSION_STATE_KEY_TO_STORE_TITLE),
                                "token_count": sess.state.get("input_token_count"),
                            }
                            for sess in results
                        ]
                        return res
            else:
                logger.error(f"Unsupported session service type: {type(session_service)}")
                raise NotImplementedError(f"Unsupported session service type: {type(session_service)}")

            return matching_sessions
        except Exception as e:
            logger.error(f"Error searching sessions for app {app_name} user {user_id}, query {body.query}: {e}"
                         f"\n{traceback.format_exc()}")
            response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return {"success": False, "error_message": str(e)}

    @router.post("/apps/{app_name}/session_merge")
    async def merge_sessions(app_name: str, body: MergeSessionsRequest, request: Request, response: Response):
        """
        Turn guest session into user session by changing their user_id to the given user_id.
        """
        try:
            session_ids = body.session_ids
            new_user_id = body.new_user_id
            if not session_ids or len(session_ids) == 0:
                response.status_code = status.HTTP_400_BAD_REQUEST
                return {"success": False, "error_message": "session_ids must be provided"}
            if not new_user_id:
                response.status_code = status.HTTP_400_BAD_REQUEST
                return {"success": False, "error_message": "new_user_id must be provided"}
            if new_user_id == 'user':
                response.status_code = status.HTTP_400_BAD_REQUEST
                return {"success": False, "error_message": "Cannot merge to guest user."}

            for sess_id in session_ids:
                if isinstance(session_service, InMemorySessionService):
                    logger.info(f"Merging session with in-memory session service")
                    sess = session_service.sessions.get(app_name, {}).get(new_user_id, {}).get(sess_id, {})
                    if sess:
                        sess.user_id = new_user_id
                elif isinstance(session_service, DatabaseSessionService):
                    logger.info(f"Merging session with database session service")
                    with session_service.database_session_factory() as sql_session:
                        with sql_session.begin():
                            # # DANGER: this line DISABLE foreign key checks,
                            # # making sure the following updates are correct!
                            # sql_session.execute(text("SET CONSTRAINTS ALL DEFERRED;"))
                            # update_event_stmt = update(StorageEvent).where(
                            #     StorageEvent.app_name == app_name,
                            #     StorageEvent.user_id == body.old_user_id,
                            #     StorageEvent.session_id == sess_id,
                            # ).values(user_id=new_user_id)
                            # update_session_stmt = update(StorageSession).where(
                            #     StorageSession.app_name == app_name,
                            #     StorageSession.user_id == body.old_user_id,
                            #     StorageSession.id == sess_id,
                            # ).values(user_id=new_user_id)
                            #
                            # sql_session.execute(update_event_stmt)
                            # sql_session.execute(update_session_stmt)
                            # # commit is not needed, sqlalchemy's session.begin() will commit automatically
                            # # sql_session.commit()

                            sql_session.execute(text(f"""
                                 WITH updated_sessions AS (
                                     UPDATE {StorageSession.__tablename__}
                                         SET user_id = :new_user_id
                                         WHERE app_name = :app_name
                                             AND user_id = :old_user_id
                                             AND id = :sess_id
                                         RETURNING app_name, id)
                                 UPDATE {StorageEvent.__tablename__}
                                 SET user_id = :new_user_id
                                 WHERE app_name = :app_name
                                   AND user_id = :old_user_id
                                   AND session_id = :sess_id
                                 """), {
                                'new_user_id': new_user_id,
                                'old_user_id': body.old_user_id,
                                'app_name': app_name,
                                'sess_id': sess_id
                            })
                else:
                    logger.error(f"Unsupported session service type: {type(session_service)}")
                    response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
                    return {"success": False, "error_message": "Unsupported session service type."}
            # todo: indicate how many sessions were merged or how many sessions were not found/cannot merged
            return {"success": True}
        except Exception as e:
            logger.error(f"Error merging sessions for app {app_name} "
                         f"new_user_id {body.new_user_id} sessions {body.session_ids}: "
                         f"{e}\n{traceback.format_exc()}")
            response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return {"success": False, "error_message": str(e)}

    @router.delete("/apps/{app_name}/users/{user_id}/sessions_delete_all")
    async def delete_all_sessions(app_name: str, user_id: str,
                                  request: Request, response: Response,
                                  body: Optional[DeleteMultipleSessionsRequest | None] = None,
                                  ):
        """
        Delete all sessions for a given app and user ID.
        """
        try:
            all_sessions = await session_service.list_sessions(
                app_name=app_name,
                user_id=user_id,
            )
            if body and body.session_ids:
                sessions_to_delete = [sess for sess in all_sessions.sessions if sess.id in body.session_ids]
            else:
                sessions_to_delete = all_sessions.sessions

            for sess in sessions_to_delete:
                await session_service.delete_session(
                    app_name=app_name,
                    user_id=user_id,
                    session_id=sess.id
                )
            return {"success": True}
        except Exception as e:
            logger.error(f"Error deleting all sessions for app {app_name} user {user_id}: "
                         f"{e}\n{traceback.format_exc()}")
            response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            return {"success": False, "error_message": str(e)}

    return router
