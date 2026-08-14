"""A `QuestionSeed` is one already-persisted `Citation`, promoted to the unit
mcq_generator.py/fill_blank_generator.py each turn into a question. Shared
here (not defined in generation.py) so both generators can import it without
importing the orchestrator that calls them.
"""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class QuestionSeed:
    citation_id: UUID
    claim_excerpt: str
    snippet_text: str
    file_path: str
    line_start: int
    line_end: int
