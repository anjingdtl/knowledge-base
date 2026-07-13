# Vector Coverage Maintenance Design

## Goal

Let a desktop user safely restore vector coverage from the Maintenance Center by
embedding only blocks that have no vector, using the project's configured
Embedding provider.

## Scope

The maintenance action is a targeted repair. It does not invoke an LLM, delete
or recreate blocks, rebuild FTS indexes, or replace existing vectors. Full
reindexing remains available through the existing operational interfaces for
cases that require a complete rebuild.

## Architecture

Add a focused service function that:

1. Counts all blocks and blocks without a corresponding `vec_blocks` entry.
2. Reads missing blocks in bounded batches.
3. Builds each embedding input using `EmbeddingService.build_embedding_text`,
   so the repair respects the configured embedding-context behavior.
4. Calls `EmbeddingService.embed_batch_with_cache` and persists returned vectors
   through `BlockStore.add_block_embeddings_batch`.
5. Reports progress, successes, failures, and before/after coverage statistics.

The GUI runs this function in a `QThread`, matching the Maintenance Center's
existing long-running scan and judgment operations. The worker communicates
progress and completion through Qt signals; the UI thread alone updates widgets.

## GUI Behavior

The Maintenance Center header will show the current vector coverage, including
the number of covered and total blocks. It will expose a “修复向量覆盖率” button.

Clicking the button presents a confirmation dialog stating that the configured
Embedding API will be called and may incur provider usage. Once confirmed, the
button is disabled and the coverage summary displays live repair progress. On
completion, the UI refreshes the summary and shows the initial and final
coverage, repaired count, and any failed batches. If nothing is missing, the
user receives a clear no-op result.

## Validation and Error Handling

Before scheduling work, the repair validates an Embedding model and usable API
credentials (an Embedding key or the existing LLM-key fallback). A missing
configuration is reported to the user without writing data. Batch failures are
captured so later batches can still run, and the result reports failed block
counts instead of claiming success. Unexpected worker errors restore the button
and present the error message.

## Testing

Service tests will prove that the repair selects only missing blocks, uses the
configured embedding service, writes only successfully generated vectors,
continues after a batch failure, and reports coverage correctly. GUI tests will
cover that the maintenance view presents the coverage action and delegates the
long-running work to its worker rather than blocking the UI.
