# Langfuse S3 idle connection reliability investigation

## Problem

Storage endpoints and intermediaries may close idle HTTP keep-alive connections without advertising a timeout. Reusing a socket after the remote endpoint closes it can cause ECONNRESET during bursts of S3 traffic.

## Reproduction and evidence

The original proposed implementation and test report are preserved in [langfuse/langfuse #19114](https://github.com/langfuse/langfuse/pull/19114). The reported standalone test used @smithy/node-http-handler 4.5.1 against a server configured to silently close idle connections.

| Configuration | Requests | Connection resets |
|---|---:|---:|
| Original behavior | 1,600 | 383 |
| Configurable idle socket expiration | 1,600 | 0 |

These are historical controlled-experiment results from the PR, not production telemetry, not a fresh rerun, and not a universal reliability guarantee. The PR also reports a check that in-flight downloads with stalls between chunks still completed, and 54 passing targeted tests. Exact original reproduction timing and fixture code should be recovered from the original experiment before claiming independent reproducibility here.

## Proposed fix and upstream outcome

The proposed patch added a configurable socket idle timeout, passed it to both ordinary and secure outbound agents, included it in the agent cache key, and tested the configuration on HTTP/HTTPS agents. The proposed code PR was closed without merging. The separate [documentation contribution #4256](https://github.com/langfuse/langfuse-docs/pull/4256) was merged on 2026-11-20. This case study does not attribute the final upstream implementation to the author of the closed PR.

## Lessons

- Distinguish a connection-pool idle timeout from an application request timeout.
- Test below, equal to and above the provider idle-close window.
- Check long-running responses independently from idle connection reuse.
- Preserve exact dependency versions, failure counts, and experimental scope.

See the linked PR and upstream issue [#18834](https://github.com/langfuse/langfuse/issues/18834) for primary evidence.
