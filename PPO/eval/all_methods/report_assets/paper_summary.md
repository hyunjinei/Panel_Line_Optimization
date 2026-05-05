# Final Evaluation Summary

## Main Finding

- Proposed(RL): makespan mean `159.48h`, violations mean `1.36`.
- GA: makespan mean `161.23h`, violations mean `5.32`.
- 해석: Proposed RL이 makespan과 제약 위반을 동시에 가장 안정적으로 줄인다.

## Overall Table

```text
  method method_label   n  makespan_mean  makespan_median  makespan_std  violations_mean  violations_median  violations_std  violations_max  missing_blocks_sum  computation_seconds_mean  computation_seconds_median
     SPT          SPT 114        172.123          172.075        88.741           25.491               22.5          16.960              64                   0                       NaN                         NaN
SEAM_MIN          MSF 114        166.775          165.908        85.231           14.719               12.0          10.142              56                   0                       NaN                         NaN
     LPT          LPT 114        163.175          162.000        83.412            8.158                8.0           5.510              24                   0                       NaN                         NaN
      GA           GA 114        161.232          160.917        83.941            5.316                3.0           5.740              22                   0                    95.488                      83.276
      RL     Proposed 114        159.477          156.242        83.657            1.360                1.0           1.364               7                   0                       NaN                         NaN
```

## Versus RL

```text
  method method_label  makespan_delta_mean_h  makespan_delta_median_h  makespan_delta_pct_mean  cases_faster_than_rl  cases_equal_makespan_rl  cases_slower_than_rl  viol_delta_mean  viol_delta_median  cases_fewer_viol_than_rl  cases_equal_viol_rl  cases_more_viol_than_rl
     SPT          SPT                 12.645                   11.700                    8.954                     0                        0                   114           24.132               22.0                         0                    7                      107
SEAM_MIN          MSF                  7.298                    6.700                    6.000                     0                        0                   114           13.360               11.0                         0                    7                      107
     LPT          LPT                  3.698                    2.833                    3.628                     4                        2                   108            6.798                6.0                         0                    8                      106
      GA           GA                  1.755                    1.825                    1.322                    23                        1                    90            3.956                2.0                         0                   43                       71
```

## Win Counts

```text
  method method_label  makespan_best_or_tied  makespan_strict_best  violations_best_or_tied  violations_strict_best
     SPT          SPT                      0                     0                        7                       0
SEAM_MIN          MSF                      0                     0                        7                       0
     LPT          LPT                      4                     3                        8                       0
      GA           GA                     22                    21                       43                       0
      RL     Proposed                     90                    88                      114                      70
```
