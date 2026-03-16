"""
Intent type definitions for query classification.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional


class IntentType(str, Enum):
    """Types of query intents."""
    SEMANTIC = "semantic"       # Unstructured text search
    STRUCTURED = "structured"   # SQL-based query on tables
    HYBRID = "hybrid"           # Both semantic and structured
    CLARIFICATION = "clarification"  # Need more information
    GREETING = "greeting"       # Simple greeting/chitchat
    

@dataclass
class ClassificationResult:
    """Result of intent classification."""
    intent: IntentType
    confidence: float
    target_table: Optional[str] = None
    reasoning: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "intent": self.intent.value,
            "confidence": self.confidence,
            "target_table": self.target_table,
            "reasoning": self.reasoning
        }


# Keywords for rule-based pre-classification
STRUCTURED_KEYWORDS = [
    "how many", "count", "total", "sum", "average", "avg",
    "maximum", "max", "minimum", "min", "list all", "show all",
    "filter", "where", "group by", "sort by", "order by",
    "top", "bottom", "between", "greater than", "less than",
    "equal to", "distinct", "unique"
]

SEMANTIC_KEYWORDS = [
    "explain", "describe", "what is", "tell me about",
    "summarize", "summary", "overview", "meaning",
    "why", "how does", "what does", "elaborate",
    "discuss", "analyze", "insight"
]

GREETING_PATTERNS = [
    "hi", "hello", "hey", "good morning", "good afternoon",
    "good evening", "howdy", "greetings", "what's up",
    "how are you", "thanks", "thank you"
]
