"""Phase 8 voice layer (ADR-010): transcription (speech -> text) and speech
(text -> speech) behind two small provider protocols, each with a hosted and
a self-hosted backend.

Deliberately its own package rather than living under app/semantics/, whose
LLMProvider is the Layer B abstraction (ADR-003). Audio is neither Layer A
nor Layer B — it never participates in indexing, and nothing here is a
source of truth. The persisted study-guide text, the learner-confirmed
transcript, and the existing grading endpoints remain canonical; audio is
an input/output convenience on top of them.
"""
