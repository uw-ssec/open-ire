# Architectural Decision Records

An Architecture Decision Record (ADR) serves as a concise, immutable log that
documents the context, rationale, and consequences of a specific technical
choice. Beyond providing a historical narrative for future developers, the
process of drafting these records facilitates collaborative clarity by forcing
teams to align on trade-offs and alternative solutions.

We record architectural decisions using
[Markdown Architectural Decision Records (MADR)](https://adr.github.io/madr/)
format. Each record explains a decision and the reasoning behind it.

## Index

| ADR                                                         | Title                                       | Date       |
| ----------------------------------------------------------- | ------------------------------------------- | ---------- |
| [0000](0000-use-markdown-architectural-decision-records.md) | Use Markdown Architectural Decision Records | 2026-09-24 |
| [0001](0001-store-timestamps-as-timezone-aware-utc.md)      | Store Timestamps as Timezone-Aware UTC      | 2026-09-24 |

## Writing guidelines

An ADR explains why a decision was reasonable at the time, giving future readers
enough context to understand or re-evaluate it later.

1. **One Decision Per Record**: Focus each ADR on a single architecturally
   significant choice. State the decision clearly in the title and lead with the
   problem statement.
2. **Focus on Rationale over Implementation**: Document the "why" and key
   constraints in the ADR; link out to pull requests, code, or tests for the
   "how" and verification details.
3. **Make Trade-offs Explicit**: Summarize considered options fairly and list
   both positive and negative consequences ("Good, because..." / "Bad,
   because...").
4. **Write for the Future (Human) Reader**: Keep records short (1–2 pages) using
   an inverted pyramid style—putting vital conclusions first and details later.
5. **Note Re-evaluation Triggers**: If relevant, mention specific changes in
   context or assumptions that should prompt the team to revisit the decision.
6. **Use Direct, Ordinary Language.** Prefer short sentences and familiar terms.
   Keep technical terms when they add precision, and remove throat-clearing.

## Maintaining ADRs

Preserve the historical decision and rationale in accepted ADRs. Editorial
improvements are fine; make factual corrections explicit. If the decision or its
substantive rationale changes, record it in a superseding ADR, mark the original
as superseded, and link the two records in both directions.

## Creating an ADR

1. Copy [adr-template.md](adr-template.md) into this directory as
   `NNNN-title-with-dashes.md`, using the next sequential number and a lowercase
   title that states the decision.
2. Fill in the template, removing optional metadata and sections that do not add
   useful information.
3. Add the record to the index above.

The template is adapted from
[MADR 4.0.0](https://github.com/adr/madr/releases/tag/4.0.0), which also
supplied [ADR 0000](0000-use-markdown-architectural-decision-records.md). See
the [upstream templates](https://github.com/adr/madr/tree/4.0.0/template) and
[examples](https://adr.github.io/madr/examples.html) for fuller guidance.
