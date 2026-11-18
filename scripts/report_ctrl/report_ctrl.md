# Experiment C - downlink command delivery

156 runs, 50 cells. Latency = issue upstream to held by the Edge Agent (one-way, shared clock), percentiles over the pooled commands of all repetitions.

## Regime: busy

### Command latency p50 (ms)

| mode | loss 0 % | loss 1 % | loss 2 % | loss 5 % |
|---|---|---|---|---|
| REST polling, HTTP/1.1, 1000 ms | 536.8 | 549.0 | 545.2 | 526.2 |
| REST polling, HTTP/1.1, 100 ms | 64.4 | 71.8 | 76.2 | 96.7 |
| REST long polling, HTTP/2 | 21.1 | 33.5 | 37.9 | 55.7 |
| REST long polling, HTTP/3 | 21.1 | 21.1 | 21.0 | 21.1 |
| REST + SSE, HTTP/2 | 20.7 | 20.9 | 21.0 | 33.4 |
| REST + SSE, HTTP/3 | 20.8 | 20.8 | 20.8 | 20.8 |
| gRPC stream, HTTP/2 | 20.6 | 20.7 | 20.7 | 25.1 |
| gRPC stream, HTTP/3 | 20.7 | 20.7 | 20.7 | 20.7 |

### Command latency p99 (ms)

| mode | loss 0 % | loss 1 % | loss 2 % | loss 5 % |
|---|---|---|---|---|
| REST polling, HTTP/1.1, 1000 ms | 990.8 | 1,001.2 | 1,012.8 | 1,154.1 |
| REST polling, HTTP/1.1, 100 ms | 120.9 | 316.8 | 349.8 | 699.8 |
| REST long polling, HTTP/2 | 62.1 | 124.6 | 143.2 | 383.6 |
| REST long polling, HTTP/3 | 60.6 | 81.2 | 108.3 | 121.2 |
| REST + SSE, HTTP/2 | 21.2 | 74.6 | 91.5 | 186.3 |
| REST + SSE, HTTP/3 | 21.3 | 61.0 | 70.6 | 84.9 |
| gRPC stream, HTTP/2 | 21.0 | 75.1 | 91.6 | 315.0 |
| gRPC stream, HTTP/3 | 21.2 | 64.0 | 81.8 | 91.2 |

### Decision-to-confirmation p99 (ms, median over runs)

| mode | loss 0 % | loss 1 % | loss 2 % | loss 5 % |
|---|---|---|---|---|
| REST polling, HTTP/1.1, 1000 ms | 1,008.6 | 1,016.4 | 1,024.3 | 1,234.3 |
| REST polling, HTTP/1.1, 100 ms | 141.6 | 356.5 | 377.7 | 645.6 |
| REST long polling, HTTP/2 | 82.9 | 188.1 | 227.4 | 495.6 |
| REST long polling, HTTP/3 | 81.4 | 103.1 | 139.1 | 174.7 |
| REST + SSE, HTTP/2 | 42.4 | 121.0 | 146.7 | 290.4 |
| REST + SSE, HTTP/3 | 42.7 | 92.9 | 107.5 | 122.3 |
| gRPC stream, HTTP/2 | 41.9 | 125.5 | 194.3 | 341.2 |
| gRPC stream, HTTP/3 | 42.3 | 115.5 | 105.3 | 126.7 |

### Requests per delivered command (median over runs)

| mode | loss 0 % | loss 1 % | loss 2 % | loss 5 % |
|---|---|---|---|---|
| REST polling, HTTP/1.1, 1000 ms | 0.35 | 0.35 | 0.35 | 0.35 |
| REST polling, HTTP/1.1, 100 ms | 2.49 | 2.44 | 2.38 | 2.09 |
| REST long polling, HTTP/2 | 1.91 | 1.82 | 1.83 | 1.68 |
| REST long polling, HTTP/3 | 1.91 | 1.92 | 1.89 | 1.86 |
| REST + SSE, HTTP/2 | 0.98 | 0.96 | 0.93 | 0.89 |
| REST + SSE, HTTP/3 | 0.99 | 0.98 | 0.97 | 0.96 |
| gRPC stream, HTTP/2 | 0.00 | 0.00 | 0.00 | 0.00 |
| gRPC stream, HTTP/3 | 0.00 | 0.00 | 0.00 | 0.00 |

### Uplink telemetry delivered alongside (measurements/s, median over runs)

| mode | loss 0 % | loss 1 % | loss 2 % | loss 5 % |
|---|---|---|---|---|
| REST polling, HTTP/1.1, 1000 ms | 1,912 | 1,641 | 1,414 | 1,022 |
| REST polling, HTTP/1.1, 100 ms | 1,917 | 1,622 | 1,392 | 1,025 |
| REST long polling, HTTP/2 | 1,917 | 1,257 | 999 | 676 |
| REST long polling, HTTP/3 | 1,904 | 1,786 | 1,689 | 1,329 |
| REST + SSE, HTTP/2 | 1,917 | 1,314 | 995 | 664 |
| REST + SSE, HTTP/3 | 1,901 | 1,793 | 1,661 | 1,325 |
| gRPC stream, HTTP/2 | 1,941 | 1,621 | 1,299 | 897 |
| gRPC stream, HTTP/3 | 1,923 | 1,851 | 1,809 | 1,604 |

### Missing commands (issued in window, never delivered; summed over runs)

| mode | loss 0 % | loss 1 % | loss 2 % | loss 5 % |
|---|---|---|---|---|
| REST polling, HTTP/1.1, 1000 ms | 0 | 0 | 0 | 0 |
| REST polling, HTTP/1.1, 100 ms | 0 | 0 | 0 | 0 |
| REST long polling, HTTP/2 | 0 | 0 | 0 | 0 |
| REST long polling, HTTP/3 | 0 | 0 | 0 | 0 |
| REST + SSE, HTTP/2 | 0 | 0 | 0 | 0 |
| REST + SSE, HTTP/3 | 0 | 0 | 0 | 0 |
| gRPC stream, HTTP/2 | 0 | 0 | 0 | 0 |
| gRPC stream, HTTP/3 | 0 | 0 | 0 | 0 |

## Regime: idle

### Command latency p50 (ms)

| mode | loss 0 % | loss 5 % |
|---|---|---|
| REST polling, HTTP/1.1, 1000 ms | 542.3 | 570.9 |
| REST polling, HTTP/2, 1000 ms | 539.8 | - |
| REST polling, HTTP/3, 1000 ms | 566.2 | - |
| REST polling, HTTP/1.1, 100 ms | 65.1 | 81.5 |
| REST long polling, HTTP/2 | 21.2 | 30.9 |
| REST long polling, HTTP/3 | 21.2 | 21.3 |
| REST + SSE, HTTP/2 | 20.8 | 20.9 |
| REST + SSE, HTTP/3 | 20.9 | 20.9 |
| gRPC stream, HTTP/2 | 20.7 | 20.8 |
| gRPC stream, HTTP/3 | 20.8 | 20.8 |

### Command latency p99 (ms)

| mode | loss 0 % | loss 5 % |
|---|---|---|
| REST polling, HTTP/1.1, 1000 ms | 992.9 | 2,007.7 |
| REST polling, HTTP/2, 1000 ms | 990.1 | - |
| REST polling, HTTP/3, 1000 ms | 1,015.7 | - |
| REST polling, HTTP/1.1, 100 ms | 120.7 | 540.7 |
| REST long polling, HTTP/2 | 61.9 | 436.4 |
| REST long polling, HTTP/3 | 61.6 | 133.9 |
| REST + SSE, HTTP/2 | 21.2 | 323.6 |
| REST + SSE, HTTP/3 | 21.3 | 90.5 |
| gRPC stream, HTTP/2 | 21.0 | 274.4 |
| gRPC stream, HTTP/3 | 21.2 | 112.1 |

### Decision-to-confirmation p99 (ms, median over runs)

| mode | loss 0 % | loss 5 % |
|---|---|---|
| REST polling, HTTP/1.1, 1000 ms | 1,013.0 | 1,257.3 |
| REST polling, HTTP/2, 1000 ms | 1,011.3 | - |
| REST polling, HTTP/3, 1000 ms | 1,036.6 | - |
| REST polling, HTTP/1.1, 100 ms | 140.9 | 602.2 |
| REST long polling, HTTP/2 | 82.7 | 545.8 |
| REST long polling, HTTP/3 | 81.7 | 158.8 |
| REST + SSE, HTTP/2 | 42.4 | 371.4 |
| REST + SSE, HTTP/3 | 42.8 | 117.5 |
| gRPC stream, HTTP/2 | 42.0 | 374.9 |
| gRPC stream, HTTP/3 | 42.2 | 164.2 |

### Requests per delivered command (median over runs)

| mode | loss 0 % | loss 5 % |
|---|---|---|
| REST polling, HTTP/1.1, 1000 ms | 0.35 | 0.37 |
| REST polling, HTTP/2, 1000 ms | 0.35 | - |
| REST polling, HTTP/3, 1000 ms | 0.35 | - |
| REST polling, HTTP/1.1, 100 ms | 2.49 | 2.15 |
| REST long polling, HTTP/2 | 1.91 | 1.75 |
| REST long polling, HTTP/3 | 1.91 | 1.88 |
| REST + SSE, HTTP/2 | 0.98 | 0.94 |
| REST + SSE, HTTP/3 | 0.98 | 0.97 |
| gRPC stream, HTTP/2 | 0.00 | 0.00 |
| gRPC stream, HTTP/3 | 0.00 | 0.00 |

### Bytes on the link per delivered command (median over runs)

| mode | loss 0 % | loss 5 % |
|---|---|---|
| REST polling, HTTP/1.1, 1000 ms | 529 | 565 |
| REST polling, HTTP/2, 1000 ms | 515 | - |
| REST polling, HTTP/3, 1000 ms | 506 | - |
| REST polling, HTTP/1.1, 100 ms | 1,794 | 1,685 |
| REST long polling, HTTP/2 | 1,249 | 1,203 |
| REST long polling, HTTP/3 | 1,233 | 1,287 |
| REST + SSE, HTTP/2 | 929 | 915 |
| REST + SSE, HTTP/3 | 924 | 983 |
| gRPC stream, HTTP/2 | 523 | 565 |
| gRPC stream, HTTP/3 | 471 | 529 |

### Missing commands (issued in window, never delivered; summed over runs)

| mode | loss 0 % | loss 5 % |
|---|---|---|
| REST polling, HTTP/1.1, 1000 ms | 0 | 0 |
| REST polling, HTTP/2, 1000 ms | 0 | - |
| REST polling, HTTP/3, 1000 ms | 0 | - |
| REST polling, HTTP/1.1, 100 ms | 0 | 0 |
| REST long polling, HTTP/2 | 0 | 0 |
| REST long polling, HTTP/3 | 0 | 0 |
| REST + SSE, HTTP/2 | 0 | 0 |
| REST + SSE, HTTP/3 | 0 | 0 |
| gRPC stream, HTTP/2 | 0 | 0 |
| gRPC stream, HTTP/3 | 0 | 0 |

## Figures

- fig_c1_latency_busy.svg
- fig_c1_latency_idle.svg
- fig_c2_cost_idle.svg
