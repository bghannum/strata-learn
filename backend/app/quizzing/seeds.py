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
    # Which subsystem this seed's file belongs to, resolved at generation time.
    # Carried onto the persisted Question so mastery can be aggregated across
    # study-guide versions (#61) — Section/Question ids are all replaced by a
    # re-index, while a subsystem key is stable by construction.
    subsystem_key: str | None = None
