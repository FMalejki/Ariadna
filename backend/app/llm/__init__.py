"""LLM layer (spec 11). Optional, assistive, never authoritative.

* Output is parsed and validated against a strict schema; anything else is rejected, not "repaired".
* Every claim must quote text that really occurs in the cited observation and is stored as
  review=pending / verification=unverified together with model + prompt version.
* Nothing here creates entities, relationships or merges.
"""
