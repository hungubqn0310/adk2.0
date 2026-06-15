"""
Output format tool for Question Answer Agent - simplified version.
"""
from typing import Optional, Dict, Any


def set_qa_response(
    message: str,
    language: str,
    metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Set the final response for QA agent.
    
    MUST be used for ALL QA agent responses.
    
    Args:
        message: Complete response to customer (must be polite and clear)
        language: 'vi' or 'en'
        metadata: Optional additional info (store details, links, etc.)
        
    Returns:
        Formatted response dictionary
    """
    return {
        "message": message,
        "language": language,
        "metadata": metadata or {},
        "agent": "question_answer_agent"
    }