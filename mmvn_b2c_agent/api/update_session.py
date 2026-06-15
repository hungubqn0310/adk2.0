import time
import traceback
import logging
import uuid
from google.adk.events import EventActions, Event
from google.adk.sessions import BaseSessionService

logger = logging.getLogger(__name__)


async def update_session(session_service: BaseSessionService, app_name: str,
                         user_id: str, session_id: str, state_delta: dict, auto_create: bool = True):
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
        logger.info(f"Setting state for app {app_name} user {user_id} session {session_id}: {state_delta}")
        if session:
            # If session exists, append an event to update the state
            actions_update_state = EventActions(state_delta=state_delta)
            system_event = Event(
                invocation_id=uuid.uuid4().hex,
                author="system",
                actions=actions_update_state,
                timestamp=time.time(),
            )
            await session_service.append_event(session, system_event)
        elif auto_create:
            # If session does not exist, create a new session with the initial state
            session = await session_service.create_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
                state=state_delta,
            )
        return session
    except Exception as e:
        logger.error(f"Error updating session: {e}\n{traceback.format_exc()}")
        raise e
