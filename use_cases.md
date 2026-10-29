# Vehicle Location Data Product Use Cases

This document follows the interview questions: propose at least three use cases, choose one, and explain its source, ETL, warehouse and BI. Technology choices and alternatives are summarized from [tech_stack.md](tech_stack.md); the [README architecture](README.md#architecture) is the navigation map for the implementation.

## 1. Three candidate use cases

### A. Motion-based Clip Discovery (Chosen ⭐)

**User and problem:** Algorithm engineers and data scientists need manageable subsets of recorded drives for training and validation. Motion criteria can help them find candidate clips before reviewing footagee.

**Example question:** "Which clips contain at least 5 seconds at or above 10 m/s, and also contain a stopped interval?

**Inputs and processing:** VL point clouds, drive/frame identities, timestamps and clip frame ranges. Reduce each cloud to its center and bounds, calculate displacement and speed between adjacent frames, and summarize the observations within each clip.

**Useful output:** A ranked clip catalog with qualifying duration, speed metrics, stopped time, trajectory drill-down and quality information. The intended benefit is less manual searching; it has not been measured.

**Main limitation:** Center movement represents vehicle movement under our stable, uniformly sampled synthetic geometry. Changing visibility or sampling in real clouds could move the center without vehicle movement. Real use needs a calibrated motion source or validated estimation method.

### B. Spatial Coverage Catalog

**User and problem:** Dataset curators and collection planners want to identify repeated routes and underrepresented areas.

**Example questions:** "Which spatial cells contain many drives?" and "Which target areas have no observations?" The second question also requires a definition of the target area.

**Inputs and processing:** VL positions in a coordinate reference shared across drives. Group observations into spatial cells and compare occupancy and route overlap.

**Useful output:** A coverage grid, counts per region and links to overlapping clips. This could guide additional collection and dataset diversity reviews.

**Main limitation:** Shared synthetic coordinates don't establish real geographic coverage. A future version could add geographic and spatial transformations.

### C. VL Signal Quality Monitoring

**User and problem:** Data infrastructure and localization engineers need to find defective or suspicious inputs before researchers rely on them.

**Example questions:** "Which drives contain missing frames, conflicting duplicates or invalid coordinates?" and "Which observations show unusually large position jumps?"

**Inputs and processing:** VL points, identities and timing metadata. Validate records, identify gaps and duplicate conflicts, and summarize diagnostic events by drive.

**Useful output:** Quality scorecards, defect timelines and records to investigate. Trusted reference trajectories could later support accuracy evaluation.

**Main limitation:** Structural checks can establish that a record follows its contract. They cannot establish whether its reported physical location is accurate. Large jumps also need interpretation: a diagnostic threshold alone does not prove sensor failure.

## 2. Why I chose Motion-based Clip Discovery (Option A)

* Goal was to make VL data useful for researchers working with recorded drives.
Motion-based Clip Discovery connects the cloud points input to a direct researcher decision: which clips should I inspect or select? It also exercises all four required layers and leaves reusable frame data for other queries.

- Research value: Motion and stopping criteria provide a concrete training/validation subset-selection workflow.
- Feasibility: Deterministic rigid-cloud translations allow exact motion examples without external labels or maps.
- Engineering depth: Dense processing, time semantics, validation, duplicate handling, SQL and recovery are meaningful parts of the solution.
- Reuse: Frame positions, bounds, counts and lineage also support spatial and quality queries.
- Demonstration: A researcher can filter clips, inspect the supporting observations and export a selection.
- ML benefit: Researchers can select clips by motion or stopping behavior for training-data curation or targeted validation. With real footage and appropriate labels, they could evaluate a model on those clips to investigate performance under those conditions. The current synthetic demo selects candidate clips; it does not train or evaluate a model.

* Coverage would become the preferred use case if the main question was "Which geographic areas are missing in our dataset?" This requires coordinates aligned to a shared geographic reference, so the same location means the same place across different drives. For example, an engineer could use coverage results to prioritize areas with few recordings to decide where vehicles should go to record more data.

* Quality monitoring would become the preferred use case if the main question was "Can researchers trust the VL signal, and which drives need investigation?" Missing frames, conflicting records and suspicious jumps would then be the primary results. 
