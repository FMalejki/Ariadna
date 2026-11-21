# Product vision

STATUS: PROPOSED (vision statement; individual capabilities carry their own status in IMPLEMENTATION_STATUS.md)

## The problem

OSINT tools are stateless. Each run forgets the last one, results are not tied to provenance, and similar-looking names get conflated. Analysts redo work and cannot say why they believe something.

## The product

A persistent, evidence-first knowledge layer:

1. **Reuse.** A second investigation that overlaps the first reuses stored observations and recent provider runs, and records only what is new.
2. **Provenance.** Every statement links to an observation, a source, a tool run, a parser version and (where available) a raw artifact.
3. **Conservative identity.** Entities are identified by exact canonical identifiers. Similarity yields a reviewable candidate, never a merge.
4. **Honest status.** A relationship is proposed, supported, corroborated, verified, contradicted, historical or rejected, depending on evidence, and the UI shows which.
5. **Human in the loop.** Sensitive discovery steps need approval. LLM output is a claim until reviewed, and stays a claim afterwards.

## Users

Analysts investigating public crypto-ecosystem activity (promotion campaigns, project presence, linked infrastructure) who need a defensible chain of evidence.

## Non-goals

Real-time monitoring at scale, social-media scraping, identity attribution, private-data collection.
