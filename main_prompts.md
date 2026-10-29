# Main Prompts

These are my main prompt around the product, design/architecture and implementation decisions.

## 1. AI workflow

> /goal Act as a Senior Data Engineer and discuss with me about the Vehicle Location Data Product until we reach to a shared understanding.
>
> For every question, provide your recommended answer.
>
> After you follow my design decision making implement the engineering workflow:
> plan, implement, test, review, simplify, document, and ship using @Agent Skills which
> serves as a Production-grade engineering skills for AI coding agents.
>
> Follow the installed skills defined in `AGENTS.md`.
>
> Definition of DONE: The complete source -> ETL -> Warehouse -> BI product works, tests and end-to-end verification pass, documentation is complete, and app is deployed successfully.

## 2. Evaluate Use Cases

> /plan Propose three distinct use cases for the VL signal. For each, discuss with me about the researcher problem, required inputs, processing, useful queries, BI output, feasibility and limitations. Afterwards we'll compare them using metrics such as user value, role alignment, engineering depth, feasibility, reuse and demonstration quality.

## 3. Tech Stack

> /plan Propose technologies to implement Vehicle Location Data Product. For each, discuss with me about the technology, its advantages and disadvantages.

## 4. Storage Efficiency

> I want four synthetic drives produce 1,000 default frames, each containing one million XYZ points. Timestamps represent 16 FPS while generation runs as accelerated replay. The pipeline retains centroid, bounds, count and content identity rather than all points.

## 5. Define warehouse row storage

> Define exactly what one warehouse row represents and how drives, frames and clips relate. Validate inputs before accepting them. Derive motion from ordered observations within each drive, with explicit units and time intervals. Specify first-frame, missing-frame and clip-boundary behavior. Keep metric definitions reusable by SQL users and the dashboard.

## 6. Protect the published catalog during failure

> Define how ingestion handles invalid records, identical duplicates, conflicting content and a source failure halfway through a replay. Preserve the previous good catalog when publication fails. Make quality outcomes inspectable and distinguish stable analytical replay from exactly-once delivery. During hardening, add justified publication gates, versioned rules, retained runs, failure audit and export protections with regression tests.

## 7. Build and verify in modular approach

> Implement thin, testable slices: source, reduction, warehouse and SQL, CLI, then BI. Use TDD for meaningful behavior and regressions. Verify dense processing separately from small fixtures. Measure ingestion time, peak memory and query latency against the recorded local budgets. Report failed checks and limitations accurately; retain an optimization only when its measured benefit justifies the complexity.

## 8. Code Review

> Use the code-review-and-quality skill review-agent pattern. Read-only review of current implementation in active directory. Focus correctness, data loss, data semantics, mandatory requirements. Do not edit files or run changes. Report concrete required findings with paths and suggested regression tests, plus limitations.

## 10. BI UI/UX

> Keep the Streamlit and warehouse architecture. Provide motion filters, trajectory and speed inspection, exact-record drill-down, quality information and exports. Use real warehouse results for every metric and make each display's scope clear. Preserve filters across theme changes, support keyboard access and narrow screens, and handle empty or unreadable catalogs explicitly. Follow the supplied Bento visual reference for UI design.