"""
Content augmentation module for MCHP.

Provides LLM-powered content generation to replace TextLorem.
"""
from augmentations.content.llm_content import (
    LLMUnavailableError,
    LLMContentGenerator,
    set_logger,
    reset_backend,
    llm_paragraph,
    llm_sentence,
    llm_word,
    llm_filename,
    llm_search_query,
    llm_select,
    llm_comment,
    llm_spreadsheet_headers,
)

__all__ = [
    'LLMUnavailableError',
    'LLMContentGenerator',
    'set_logger',
    'reset_backend',
    'llm_paragraph',
    'llm_sentence',
    'llm_word',
    'llm_filename',
    'llm_search_query',
    'llm_select',
    'llm_comment',
    'llm_spreadsheet_headers',
]
