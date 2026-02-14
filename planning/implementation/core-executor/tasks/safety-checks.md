# Task: Safety Checks -- Call Depth, Circular Detection, Frequency Throttling

## Goal

Implement the three safety mechanisms evaluated at step 2 of the execution pipeline: call depth limiting, circular call detection, and per-module frequency throttling. These prevent unbounded recursion, circular invocation chains, and tight-loop abuse.

## Files Involved

- `src/apcore/executor.py` -- `_check_safety()` method (lines 342-375)
- `src/apcore/errors.py` -- `CallDepthExceededError`, `CircularCallError`, `CallFrequencyExceededError`
- `tests/test_executor.py` -- Safety check unit tests

## Steps

1. **Implement error types** (TDD: write tests for each error's code, message, and details)
   - `CallDepthExceededError(depth, max_depth, call_chain)` with code `CALL_DEPTH_EXCEEDED`
   - `CircularCallError(module_id, call_chain)` with code `CIRCULAR_CALL`
   - `CallFrequencyExceededError(module_id, count, max_repeat, call_chain)` with code `CALL_FREQUENCY_EXCEEDED`

2. **Implement call depth check** (TDD: test depth at limit, below limit, above limit)
   - Compare `len(call_chain)` against `_max_call_depth` (default 32)
   - Raise `CallDepthExceededError` when exceeded

3. **Implement circular call detection** (TDD: test A->B->A cycle, A->B->C->B cycle, non-cycle repetition)
   - Examine `call_chain[:-1]` (prior chain, since child() already appended module_id)
   - If `module_id` is found in prior chain, extract subsequence between last occurrence and end
   - Only raise `CircularCallError` if subsequence length > 0 (true cycle of length >= 2)

4. **Implement frequency throttling** (TDD: test count at limit, below limit, above limit)
   - Count occurrences of `module_id` in `call_chain`
   - Raise `CallFrequencyExceededError` when count exceeds `_max_module_repeat` (default 3)

## Acceptance Criteria

- Call depth check rejects chains exceeding max_call_depth
- Circular detection identifies A->B->A patterns but allows simple repetition (A->A)
- Frequency throttle fires when a module appears more than max_module_repeat times in the chain
- All errors carry full call_chain in details for debugging
- Configurable limits via Config (executor.max_call_depth, executor.max_module_repeat)

## Dependencies

- Task: setup (Context with call_chain, Config with dot-path access)

## Estimated Time

2 hours
