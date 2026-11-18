# test - REST vs gRPC vs gRPC over QUIC

## Emulated path

- Base RTT: 40.0 ms (access 1.0 ms + WAN 18.0 ms one-way, netem on both directions of every link)
- WAN bottleneck: 50.0 Mbit/s per direction, buffer 362500 B (~242 pkt = link BDP 150 pkt + 22 ms of queueing)
- Topology: c0 (Edge GW) - r0 - r1 - s0 (Cloud GW); loss injected on the r0-r1 link, in both directions

- Runs analysed: 1760

## Throughput regime (600 measurements per batch, 1 batch in flight per stream)

| loss % | transport | goodput Mbps | msg/s | bytes/msg | RTT p50 ms | RTT p99 ms | blocked frac. | stall wins |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 0 | REST / HTTP1.1 - 8 conn x 1 | 47.49 | 44400 | 133.6 | 92 | 289 | 0.21 | 250 |
| 0 | gRPC / HTTP2 - 1 conn x 8 | 46.55 | 104400 | 55.7 | 45 | 47 | 0.00 | 0 |
| 0 | gRPC / HTTP2 - 8 conn x 1 | 46.58 | 104460 | 55.7 | 45 | 47 | 0.00 | 0 |
| 0 | gRPC / HTTP3 - 1 conn x 8 | 44.53 | 99880 | 55.7 | 46 | 69 | 0.00 | 0 |
| 0 | gRPC / HTTP3 - 8 conn x 1 | 34.23 | 76870 | 55.6 | 61 | 84 | 0.50 | 1 |
| 0 | gRPC / raw QUIC - 1 conn x 8 | 43.14 | 96770 | 55.7 | 47 | 71 | 0.19 | 1 |
| 0.05 | REST / HTTP1.1 - 8 conn x 1 | 45.37 | 42480 | 133.6 | 120 | 199 | 0.26 | 222 |
| 0.05 | gRPC / HTTP2 - 1 conn x 8 | 16.13 | 36170 | 55.7 | 124 | 242 | 0.34 | 250 |
| 0.05 | gRPC / HTTP2 - 8 conn x 1 | 39.32 | 88350 | 55.6 | 46 | 100 | 0.12 | 13 |
| 0.05 | gRPC / HTTP3 - 1 conn x 8 | 31.97 | 71840 | 55.6 | 62 | 124 | 0.27 | 18 |
| 0.05 | gRPC / HTTP3 - 8 conn x 1 | 31.66 | 71190 | 55.5 | 66 | 94 | 0.23 | 4 |
| 0.05 | gRPC / raw QUIC - 1 conn x 8 | 35.74 | 80260 | 55.7 | 55 | 102 | 0.24 | 3 |
| 0.1 | REST / HTTP1.1 - 8 conn x 1 | 42.59 | 39880 | 133.6 | 116 | 212 | 0.27 | 226 |
| 0.1 | gRPC / HTTP2 - 1 conn x 8 | 10.72 | 24040 | 55.7 | 193 | 405 | 0.51 | 298 |
| 0.1 | gRPC / HTTP2 - 8 conn x 1 | 34.60 | 77900 | 55.5 | 47 | 108 | 0.14 | 31 |
| 0.1 | gRPC / HTTP3 - 1 conn x 8 | 23.10 | 52010 | 55.5 | 86 | 178 | 0.30 | 96 |
| 0.1 | gRPC / HTTP3 - 8 conn x 1 | 30.87 | 69540 | 55.5 | 66 | 98 | 0.18 | 6 |
| 0.1 | gRPC / raw QUIC - 1 conn x 8 | 27.37 | 61570 | 55.6 | 71 | 159 | 0.27 | 60 |
| 0.2 | REST / HTTP1.1 - 8 conn x 1 | 39.77 | 37210 | 133.5 | 129 | 251 | 0.27 | 267 |
| 0.2 | gRPC / HTTP2 - 1 conn x 8 | 7.42 | 16660 | 55.7 | 270 | 545 | 0.66 | 300 |
| 0.2 | gRPC / HTTP2 - 8 conn x 1 | 28.74 | 64830 | 55.4 | 82 | 138 | 0.14 | 64 |
| 0.2 | gRPC / HTTP3 - 1 conn x 8 | 16.57 | 37360 | 55.4 | 124 | 239 | 0.36 | 214 |
| 0.2 | gRPC / HTTP3 - 8 conn x 1 | 30.06 | 67880 | 55.4 | 64 | 104 | 0.16 | 10 |
| 0.2 | gRPC / raw QUIC - 1 conn x 8 | 16.80 | 37890 | 55.4 | 122 | 251 | 0.37 | 201 |
| 0.5 | REST / HTTP1.1 - 8 conn x 1 | 28.57 | 26770 | 133.2 | 171 | 379 | 0.45 | 298 |
| 0.5 | gRPC / HTTP2 - 1 conn x 8 | 4.67 | 10520 | 55.5 | 449 | 799 | 0.78 | 300 |
| 0.5 | gRPC / HTTP2 - 8 conn x 1 | 22.03 | 49660 | 55.4 | 83 | 224 | 0.18 | 181 |
| 0.5 | gRPC / HTTP3 - 1 conn x 8 | 9.86 | 22290 | 55.3 | 208 | 410 | 0.55 | 289 |
| 0.5 | gRPC / HTTP3 - 8 conn x 1 | 26.96 | 60810 | 55.4 | 83 | 129 | 0.14 | 34 |
| 0.5 | gRPC / raw QUIC - 1 conn x 8 | 9.99 | 22570 | 55.3 | 201 | 413 | 0.55 | 292 |
| 1 | REST / HTTP1.1 - 8 conn x 1 | 20.84 | 19620 | 132.9 | 221 | 511 | 0.59 | 300 |
| 1 | gRPC / HTTP2 - 1 conn x 8 | 3.12 | 7050 | 55.3 | 657 | 1194 | 0.86 | 300 |
| 1 | gRPC / HTTP2 - 8 conn x 1 | 16.69 | 37590 | 55.4 | 123 | 411 | 0.29 | 274 |
| 1 | gRPC / HTTP3 - 1 conn x 8 | 7.36 | 16650 | 55.3 | 283 | 523 | 0.66 | 299 |
| 1 | gRPC / HTTP3 - 8 conn x 1 | 24.03 | 54200 | 55.5 | 83 | 156 | 0.15 | 88 |
| 1 | gRPC / raw QUIC - 1 conn x 8 | 7.55 | 17090 | 55.2 | 271 | 500 | 0.65 | 298 |
| 2 | REST / HTTP1.1 - 8 conn x 1 | 14.47 | 13620 | 132.9 | 331 | 751 | 0.72 | 300 |
| 2 | gRPC / HTTP2 - 1 conn x 8 | 2.19 | 4970 | 55.0 | 951 | 1698 | 0.90 | 300 |
| 2 | gRPC / HTTP2 - 8 conn x 1 | 11.88 | 26830 | 55.3 | 164 | 483 | 0.45 | 298 |
| 2 | gRPC / HTTP3 - 1 conn x 8 | 5.36 | 12170 | 55.1 | 378 | 718 | 0.75 | 300 |
| 2 | gRPC / HTTP3 - 8 conn x 1 | 20.89 | 47130 | 55.4 | 87 | 194 | 0.19 | 184 |
| 2 | gRPC / raw QUIC - 1 conn x 8 | 5.59 | 12680 | 55.1 | 368 | 669 | 0.74 | 300 |
| 5 | REST / HTTP1.1 - 8 conn x 1 | 7.89 | 7410 | 132.8 | 590 | 1427 | 0.85 | 300 |
| 5 | gRPC / HTTP2 - 1 conn x 8 | 1.05 | 2400 | 54.6 | 1919 | 3428 | 0.95 | 300 |
| 5 | gRPC / HTTP2 - 8 conn x 1 | 6.64 | 14960 | 55.2 | 285 | 848 | 0.69 | 300 |
| 5 | gRPC / HTTP3 - 1 conn x 8 | 3.47 | 7910 | 54.9 | 581 | 1085 | 0.84 | 300 |
| 5 | gRPC / HTTP3 - 8 conn x 1 | 15.25 | 34480 | 55.3 | 124 | 314 | 0.33 | 284 |
| 5 | gRPC / raw QUIC - 1 conn x 8 | 3.62 | 8250 | 54.9 | 571 | 1020 | 0.83 | 300 |

### Degradation from 0% to 5% loss

- REST / HTTP1.1 - 8 conn x 1: retains 17% of goodput (47.5 -> 7.9 Mbps, 44400 -> 7410 msg/s), p99 latency x5 (289 -> 1427 ms)
- gRPC / HTTP2 - 1 conn x 8: retains 2% of goodput (46.5 -> 1.0 Mbps, 104400 -> 2400 msg/s), p99 latency x72 (47 -> 3428 ms)
- gRPC / HTTP2 - 8 conn x 1: retains 14% of goodput (46.6 -> 6.6 Mbps, 104460 -> 14960 msg/s), p99 latency x18 (47 -> 848 ms)
- gRPC / HTTP3 - 1 conn x 8: retains 8% of goodput (44.5 -> 3.5 Mbps, 99880 -> 7910 msg/s), p99 latency x16 (69 -> 1085 ms)
- gRPC / HTTP3 - 8 conn x 1: retains 45% of goodput (34.2 -> 15.2 Mbps, 76870 -> 34480 msg/s), p99 latency x4 (84 -> 314 ms)
- gRPC / raw QUIC - 1 conn x 8: retains 8% of goodput (43.1 -> 3.6 Mbps, 96770 -> 8250 msg/s), p99 latency x14 (71 -> 1020 ms)

## Head-of-line regime (20 measurements per batch, 1 batch in flight per stream)

| loss % | transport | goodput Mbps | msg/s | bytes/msg | RTT p50 ms | RTT p99 ms | blocked frac. | stall wins |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 0 | REST / HTTP1.1 - 8 conn x 1 | 4.18 | 3824 | 136.6 | 42 | 44 | 0.00 | 0 |
| 0 | gRPC / HTTP2 - 1 conn x 8 | 1.75 | 3888 | 56.1 | 41 | 42 | 0.00 | 0 |
| 0 | gRPC / HTTP2 - 8 conn x 1 | 1.73 | 3845 | 56.1 | 42 | 43 | 0.00 | 0 |
| 0 | gRPC / HTTP3 - 1 conn x 8 | 1.72 | 3835 | 56.1 | 42 | 43 | 0.00 | 0 |
| 0 | gRPC / HTTP3 - 8 conn x 1 | 1.71 | 3808 | 56.1 | 42 | 43 | 0.00 | 0 |
| 0 | gRPC / raw QUIC - 1 conn x 8 | 1.72 | 3835 | 56.1 | 42 | 42 | 0.00 | 0 |
| 0.05 | REST / HTTP1.1 - 8 conn x 1 | 4.16 | 3806 | 136.6 | 42 | 43 | 0.12 | 10 |
| 0.05 | gRPC / HTTP2 - 1 conn x 8 | 1.72 | 3841 | 56.1 | 41 | 67 | 0.00 | 0 |
| 0.05 | gRPC / HTTP2 - 8 conn x 1 | 1.72 | 3833 | 56.1 | 41 | 42 | 0.12 | 15 |
| 0.05 | gRPC / HTTP3 - 1 conn x 8 | 1.72 | 3825 | 56.1 | 42 | 43 | 0.00 | 0 |
| 0.05 | gRPC / HTTP3 - 8 conn x 1 | 1.72 | 3831 | 56.1 | 42 | 42 | 0.12 | 1 |
| 0.05 | gRPC / raw QUIC - 1 conn x 8 | 1.72 | 3834 | 56.1 | 42 | 42 | 0.00 | 0 |
| 0.1 | REST / HTTP1.1 - 8 conn x 1 | 4.12 | 3768 | 136.6 | 42 | 43 | 0.12 | 27 |
| 0.1 | gRPC / HTTP2 - 1 conn x 8 | 1.67 | 3722 | 56.1 | 41 | 82 | 0.00 | 0 |
| 0.1 | gRPC / HTTP2 - 8 conn x 1 | 1.72 | 3823 | 56.1 | 41 | 42 | 0.13 | 26 |
| 0.1 | gRPC / HTTP3 - 1 conn x 8 | 1.72 | 3821 | 56.1 | 42 | 43 | 0.00 | 0 |
| 0.1 | gRPC / HTTP3 - 8 conn x 1 | 1.71 | 3821 | 56.1 | 42 | 43 | 0.12 | 2 |
| 0.1 | gRPC / raw QUIC - 1 conn x 8 | 1.72 | 3827 | 56.1 | 42 | 42 | 0.00 | 0 |
| 0.2 | REST / HTTP1.1 - 8 conn x 1 | 4.06 | 3717 | 136.5 | 42 | 54 | 0.14 | 42 |
| 0.2 | gRPC / HTTP2 - 1 conn x 8 | 1.60 | 3574 | 56.1 | 41 | 83 | 0.19 | 1 |
| 0.2 | gRPC / HTTP2 - 8 conn x 1 | 1.69 | 3775 | 56.1 | 41 | 42 | 0.13 | 48 |
| 0.2 | gRPC / HTTP3 - 1 conn x 8 | 1.71 | 3800 | 56.1 | 42 | 43 | 0.12 | 1 |
| 0.2 | gRPC / HTTP3 - 8 conn x 1 | 1.71 | 3813 | 56.1 | 42 | 43 | 0.12 | 2 |
| 0.2 | gRPC / raw QUIC - 1 conn x 8 | 1.71 | 3812 | 56.1 | 42 | 42 | 0.12 | 1 |
| 0.5 | REST / HTTP1.1 - 8 conn x 1 | 3.90 | 3570 | 136.5 | 42 | 96 | 0.15 | 96 |
| 0.5 | gRPC / HTTP2 - 1 conn x 8 | 1.41 | 3146 | 56.1 | 41 | 103 | 0.39 | 6 |
| 0.5 | gRPC / HTTP2 - 8 conn x 1 | 1.62 | 3601 | 56.1 | 41 | 125 | 0.15 | 103 |
| 0.5 | gRPC / HTTP3 - 1 conn x 8 | 1.69 | 3767 | 56.1 | 42 | 84 | 0.15 | 2 |
| 0.5 | gRPC / HTTP3 - 8 conn x 1 | 1.69 | 3770 | 56.1 | 42 | 89 | 0.12 | 6 |
| 0.5 | gRPC / raw QUIC - 1 conn x 8 | 1.70 | 3782 | 56.1 | 42 | 83 | 0.21 | 2 |
| 1 | REST / HTTP1.1 - 8 conn x 1 | 3.60 | 3294 | 136.4 | 42 | 326 | 0.17 | 162 |
| 1 | gRPC / HTTP2 - 1 conn x 8 | 1.17 | 2611 | 56.1 | 42 | 130 | 0.37 | 14 |
| 1 | gRPC / HTTP2 - 8 conn x 1 | 1.50 | 3352 | 56.1 | 41 | 369 | 0.17 | 166 |
| 1 | gRPC / HTTP3 - 1 conn x 8 | 1.66 | 3702 | 56.1 | 42 | 88 | 0.15 | 4 |
| 1 | gRPC / HTTP3 - 8 conn x 1 | 1.66 | 3700 | 56.1 | 41 | 110 | 0.13 | 13 |
| 1 | gRPC / raw QUIC - 1 conn x 8 | 1.67 | 3732 | 56.1 | 42 | 86 | 0.20 | 4 |
| 2 | REST / HTTP1.1 - 8 conn x 1 | 3.16 | 2903 | 136.4 | 42 | 340 | 0.22 | 229 |
| 2 | gRPC / HTTP2 - 1 conn x 8 | 0.95 | 2107 | 56.1 | 82 | 164 | 0.40 | 41 |
| 2 | gRPC / HTTP2 - 8 conn x 1 | 1.31 | 2922 | 56.1 | 41 | 373 | 0.22 | 240 |
| 2 | gRPC / HTTP3 - 1 conn x 8 | 1.60 | 3561 | 56.1 | 42 | 116 | 0.18 | 12 |
| 2 | gRPC / HTTP3 - 8 conn x 1 | 1.60 | 3558 | 56.1 | 41 | 110 | 0.13 | 24 |
| 2 | gRPC / raw QUIC - 1 conn x 8 | 1.63 | 3630 | 56.1 | 42 | 89 | 0.18 | 10 |
| 5 | REST / HTTP1.1 - 8 conn x 1 | 2.27 | 2086 | 135.9 | 42 | 379 | 0.33 | 287 |
| 5 | gRPC / HTTP2 - 1 conn x 8 | 0.66 | 1466 | 56.1 | 94 | 369 | 0.49 | 126 |
| 5 | gRPC / HTTP2 - 8 conn x 1 | 0.92 | 2045 | 56.0 | 41 | 380 | 0.35 | 286 |
| 5 | gRPC / HTTP3 - 1 conn x 8 | 1.37 | 3063 | 56.1 | 42 | 150 | 0.22 | 48 |
| 5 | gRPC / HTTP3 - 8 conn x 1 | 1.42 | 3158 | 56.1 | 41 | 114 | 0.14 | 63 |
| 5 | gRPC / raw QUIC - 1 conn x 8 | 1.45 | 3225 | 56.1 | 42 | 131 | 0.22 | 40 |

### Degradation from 0% to 5% loss

- REST / HTTP1.1 - 8 conn x 1: retains 54% of goodput (4.2 -> 2.3 Mbps, 3824 -> 2086 msg/s), p99 latency x9 (44 -> 379 ms)
- gRPC / HTTP2 - 1 conn x 8: retains 38% of goodput (1.7 -> 0.7 Mbps, 3888 -> 1466 msg/s), p99 latency x9 (42 -> 369 ms)
- gRPC / HTTP2 - 8 conn x 1: retains 53% of goodput (1.7 -> 0.9 Mbps, 3845 -> 2045 msg/s), p99 latency x9 (43 -> 380 ms)
- gRPC / HTTP3 - 1 conn x 8: retains 80% of goodput (1.7 -> 1.4 Mbps, 3835 -> 3063 msg/s), p99 latency x4 (43 -> 150 ms)
- gRPC / HTTP3 - 8 conn x 1: retains 83% of goodput (1.7 -> 1.4 Mbps, 3808 -> 3158 msg/s), p99 latency x3 (43 -> 114 ms)
- gRPC / raw QUIC - 1 conn x 8: retains 84% of goodput (1.7 -> 1.4 Mbps, 3835 -> 3225 msg/s), p99 latency x3 (42 -> 131 ms)

## Experiment A - one connection vs N connections, against N

Telemetry regime only. "one conn" carries all N streams on a single connection; "N conn" opens one connection per stream. The ratio is what choosing the single-connection design costs.

| transport | streams | loss % | one conn (msg/s) | per stream | N conn (msg/s) | one conn keeps |
|---|---:|---:|---:|---:|---:|---:|
| TCP / HTTP2 | 2 | 0 | 973 | 487 | 969 | 100% |
| TCP / HTTP2 | 2 | 0.5 | 920 | 460 | 896 | 103% |
| TCP / HTTP2 | 2 | 1 | 852 | 426 | 841 | 101% |
| TCP / HTTP2 | 2 | 2 | 764 | 382 | 721 | 106% |
| TCP / HTTP2 | 2 | 5 | 543 | 272 | 510 | 106% |
| TCP / HTTP2 | 4 | 0 | 1941 | 485 | 1933 | 100% |
| TCP / HTTP2 | 4 | 0.5 | 1770 | 442 | 1809 | 98% |
| TCP / HTTP2 | 4 | 1 | 1607 | 402 | 1669 | 96% |
| TCP / HTTP2 | 4 | 2 | 1293 | 323 | 1464 | 88% |
| TCP / HTTP2 | 4 | 5 | 870 | 218 | 1031 | 84% |
| TCP / HTTP2 | 8 | 0 | 3888 | 486 | 3845 | 101% |
| TCP / HTTP2 | 8 | 0.05 | 3841 | 480 | 3833 | 100% |
| TCP / HTTP2 | 8 | 0.1 | 3722 | 465 | 3823 | 97% |
| TCP / HTTP2 | 8 | 0.2 | 3574 | 447 | 3775 | 95% |
| TCP / HTTP2 | 8 | 0.5 | 3146 | 393 | 3601 | 87% |
| TCP / HTTP2 | 8 | 1 | 2611 | 326 | 3352 | 78% |
| TCP / HTTP2 | 8 | 2 | 2107 | 263 | 2922 | 72% |
| TCP / HTTP2 | 8 | 5 | 1466 | 183 | 2045 | 72% |
| TCP / HTTP2 | 16 | 0 | 7766 | 485 | 7733 | 100% |
| TCP / HTTP2 | 16 | 0.5 | 4875 | 305 | 7232 | 67% |
| TCP / HTTP2 | 16 | 1 | 4077 | 255 | 6681 | 61% |
| TCP / HTTP2 | 16 | 2 | 3234 | 202 | 5875 | 55% |
| TCP / HTTP2 | 16 | 5 | 2142 | 134 | 4081 | 52% |
| QUIC / HTTP3 | 2 | 0 | 964 | 482 | 960 | 100% |
| QUIC / HTTP3 | 2 | 0.5 | 946 | 473 | 943 | 100% |
| QUIC / HTTP3 | 2 | 1 | 928 | 464 | 926 | 100% |
| QUIC / HTTP3 | 2 | 2 | 887 | 444 | 891 | 100% |
| QUIC / HTTP3 | 2 | 5 | 788 | 394 | 781 | 101% |
| QUIC / HTTP3 | 4 | 0 | 1923 | 481 | 1915 | 100% |
| QUIC / HTTP3 | 4 | 0.5 | 1893 | 473 | 1890 | 100% |
| QUIC / HTTP3 | 4 | 1 | 1850 | 462 | 1848 | 100% |
| QUIC / HTTP3 | 4 | 2 | 1794 | 449 | 1778 | 101% |
| QUIC / HTTP3 | 4 | 5 | 1601 | 400 | 1576 | 102% |
| QUIC / HTTP3 | 8 | 0 | 3835 | 479 | 3808 | 101% |
| QUIC / HTTP3 | 8 | 0.05 | 3825 | 478 | 3831 | 100% |
| QUIC / HTTP3 | 8 | 0.1 | 3821 | 478 | 3821 | 100% |
| QUIC / HTTP3 | 8 | 0.2 | 3800 | 475 | 3813 | 100% |
| QUIC / HTTP3 | 8 | 0.5 | 3767 | 471 | 3770 | 100% |
| QUIC / HTTP3 | 8 | 1 | 3702 | 463 | 3700 | 100% |
| QUIC / HTTP3 | 8 | 2 | 3561 | 445 | 3558 | 100% |
| QUIC / HTTP3 | 8 | 5 | 3063 | 383 | 3158 | 97% |
| QUIC / HTTP3 | 16 | 0 | 7664 | 479 | 7659 | 100% |
| QUIC / HTTP3 | 16 | 0.5 | 7378 | 461 | 7531 | 98% |
| QUIC / HTTP3 | 16 | 1 | 7122 | 445 | 7426 | 96% |
| QUIC / HTTP3 | 16 | 2 | 6276 | 392 | 7136 | 88% |
| QUIC / HTTP3 | 16 | 5 | 4614 | 288 | 6333 | 73% |

## Experiment B - what it costs to bring a stream online

open = the transport's Open() call (a handshake when there is one). add = until the new stream's first batch is acknowledged. peer progress = acks completed by the already-running streams after the add, as a percentage of the window before it.

| loss % | transport | adds | open p50 | open p99 | add p50 | add p99 | add max | peer progress |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 0 | REST / HTTP1.1 - 8 conn x 1 | 70 | 126 | 128 | 168 | 171 | 171 | 102% |
| 0 | gRPC / HTTP2 - 1 conn x 8 | 70 | 42 | 43 | 83 | 85 | 85 | 103% |
| 0 | gRPC / HTTP2 - 8 conn x 1 | 70 | 127 | 130 | 168 | 171 | 172 | 99% |
| 0 | gRPC / HTTP3 - 1 conn x 8 | 70 | 42 | 43 | 84 | 86 | 88 | 101% |
| 0 | gRPC / HTTP3 - 8 conn x 1 | 70 | 88 | 90 | 129 | 132 | 132 | 98% |
| 1 | REST / HTTP1.1 - 8 conn x 1 | 70 | 126 | 606 | 168 | 649 | 1176 | 102% |
| 1 | gRPC / HTTP2 - 1 conn x 8 | 70 | 49 | 162 | 104 | 286 | 435 | 90% |
| 1 | gRPC / HTTP2 - 8 conn x 1 | 70 | 126 | 241 | 168 | 282 | 417 | 99% |
| 1 | gRPC / HTTP3 - 1 conn x 8 | 70 | 42 | 42 | 84 | 165 | 166 | 98% |
| 1 | gRPC / HTTP3 - 8 conn x 1 | 70 | 88 | 166 | 130 | 297 | 371 | 101% |
| 2 | REST / HTTP1.1 - 8 conn x 1 | 70 | 126 | 1214 | 168 | 1419 | 1428 | 96% |
| 2 | gRPC / HTTP2 - 1 conn x 8 | 70 | 62 | 199 | 139 | 304 | 396 | 92% |
| 2 | gRPC / HTTP2 - 8 conn x 1 | 70 | 127 | 1138 | 168 | 1179 | 1195 | 98% |
| 2 | gRPC / HTTP3 - 1 conn x 8 | 70 | 42 | 43 | 84 | 149 | 156 | 101% |
| 2 | gRPC / HTTP3 - 8 conn x 1 | 70 | 88 | 289 | 130 | 368 | 451 | 97% |
| 5 | REST / HTTP1.1 - 8 conn x 1 | 70 | 126 | 3245 | 169 | 3299 | 6747 | 106% |
| 5 | gRPC / HTTP2 - 1 conn x 8 | 70 | 78 | 510 | 187 | 704 | 976 | 84% |
| 5 | gRPC / HTTP2 - 8 conn x 1 | 70 | 127 | 1203 | 170 | 1244 | 1248 | 102% |
| 5 | gRPC / HTTP3 - 1 conn x 8 | 70 | 42 | 146 | 84 | 188 | 209 | 100% |
| 5 | gRPC / HTTP3 - 8 conn x 1 | 70 | 89 | 413 | 131 | 470 | 779 | 99% |

> A ramp run's throughput totals are NOT comparable with a steady-state run: its later streams were live for less of the window. Use these runs for the add columns only.

