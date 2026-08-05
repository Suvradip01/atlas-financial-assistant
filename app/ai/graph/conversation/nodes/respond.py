"""
Atlas — Conversation Graph: respond node.

The terminal node: sends the final response to the user via Telegram.
Also persists both the user message and the assistant response to the database.

Two code paths:
1. Normal response — send final_response.
2. Clarification — send clarification_question (turn ends here, state persisted).
3. Error fallback — if no final_response, send a graceful error message.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.graph.conversation.state import ConversationState
from app.core.logging import get_logger
from app.integrations_clients.telegram_client import get_telegram_client
from app.modules.conversation.models import MessageModality, MessageRole
from app.modules.conversation.service import ConversationService

logger = get_logger(__name__)

_FALLBACK_MESSAGE = (
    "I'm having a moment — could you try rephrasing that? "
    "I want to make sure I give you the right answer."
)


async def respond(
    state: ConversationState,
    session: AsyncSession,
) -> ConversationState:
    """Send the response to the user and persist the conversation turn.

    Returns the state unchanged (this is the terminal node).
    """
    chat_id = state.get("chat_id")
    conversation_id = state.get("conversation_id")
    user_id = state.get("user_id")
    raw_input = state.get("raw_input", "")
    input_modality = state.get("input_modality", "text")
    needs_clarification = state.get("needs_clarification", False)

    tg = get_telegram_client()
    convo_service = ConversationService(session)

    # Determine what to send.
    if needs_clarification:
        response_text = state.get("clarification_question", "Could you clarify that?")
    elif state.get("final_response"):
        response_text = state["final_response"]
    else:
        response_text = _FALLBACK_MESSAGE

    # Send the typing indicator and then the message.
    if chat_id:
        await tg.send_typing_action(chat_id)
        await tg.send_message(chat_id, response_text)

    # Persist both turns to the database.
    if conversation_id:
        # Save user message.
        modality = MessageModality(input_modality) if input_modality in MessageModality.__members__.values() else MessageModality.TEXT
        if raw_input:
            await convo_service.save_user_message(
                conversation_id=conversation_id,
                content=raw_input,
                modality=modality,
            )

        # Save assistant response.
        await convo_service.save_assistant_message(
            conversation_id=conversation_id,
            content=response_text,
        )

    logger.info(
        "response_sent",
        user_id=user_id,
        chat_id=chat_id,
        is_clarification=needs_clarification,
        response_length=len(response_text),
    )

    return state
