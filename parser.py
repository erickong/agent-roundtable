"""JSON parser for agent outputs with fallback to markdown extraction."""

import json
import re
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def parse_json_response(raw_text: str) -> Optional[Dict[str, Any]]:
    """Try to parse JSON from an LLM response.

    Attempts:
    1. Direct JSON parse
    2. Extract JSON from ```json ... ``` blocks
    3. Extract JSON from first { to last }
    """
    # 1. Direct parse
    try:
        return json.loads(raw_text.strip())
    except json.JSONDecodeError:
        pass

    # 2. Extract from code block
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", raw_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 3. Extract from first { to last }
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw_text[start : end + 1])
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse JSON from response, returning None")
    return None


def extract_field(text: str, field_name: str) -> Optional[str]:
    """Fallback: extract a field value from markdown-style output.

    Looks for patterns like:
      Core Position: some value
      **Core Position**: some value
    """
    pattern = rf"(?:\*\*)?{re.escape(field_name)}(?:\*\*)?[：:]\s*(.+)"
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def safe_parse_agent_output(raw_text: str, expected_keys: list[str]) -> Dict[str, Any]:
    """Parse agent output, falling back to markdown extraction if JSON fails."""
    result = parse_json_response(raw_text)
    if result is not None:
        return result

    # Fallback: try to extract individual fields from text
    logger.info("Using fallback markdown extraction")
    fallback = {}
    for key in expected_keys:
        readable_key = key.replace("_", " ").title()
        value = extract_field(raw_text, readable_key) or extract_field(raw_text, key)
        if value:
            fallback[key] = value
    return fallback
