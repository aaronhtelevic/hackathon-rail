// Tooltip copy for scorer/metrics.py output, shown wherever these fields appear
// (ScorePanel per-leg cards, ScoreSummary's all-runs table). Keep this in sync
// with scorer/metrics.py if the metric definitions change.
export const SCORE_INFO = {
  routeGuess:
    'Your last non-null routeGuess value in position.csv. Scored correct if it ' +
    'name-matches the leg\'s true line (e.g. "ic830") after normalization — ' +
    'see scorer/metrics.py:route_discovery().',
  lockIn:
    'Seconds from leg start to the earliest row after which routeGuess never ' +
    'changes again (walking backward from the final guess while it still ' +
    'matches). Only set when the final guess is correct — a wrong final guess ' +
    'has no lock-in time. Often near 0s when the algorithm commits to the ' +
    'right route from its very first guess and never flip-flops.',
  medianErr:
    'Median of |your distanceAlongTrackM − interpolated ground truth| over every ' +
    'submitted row with defined ground truth (steady-state tracking error). ' +
    'scorer/metrics.py:position_accuracy(). Baseline (do-nothing guess) is ~974 m.',
  meanMaxErr:
    'Mean and worst-case (max) of the same per-row along-track error used for ' +
    'the median. A high max with a low median usually means one bad patch ' +
    '(e.g. a tunnel or a bad fix) rather than a systemic offset.',
  coverage:
    'Share of submitted rows that fell inside a ground-truth-defined window ' +
    '(some gaps — e.g. long tunnels — are left undefined and excluded).',
  station:
    'Was your first station_calls.csv row the correct arrival station, called ' +
    'within ±90s of the "500m out" reference moment? Only the first call ' +
    'counts — spamming guesses earns nothing. scorer/metrics.py:station_detection().',
  stationTiming:
    'Seconds between your first station call and the true "500m before arrival" ' +
    'reference moment. Within ±90s counts as a hit.',
  firstFix:
    'Cold-start only: time-to-first-fix (TTFF) is how many seconds after leg ' +
    'start your position first landed within 1000m of ground truth (a wildly ' +
    'wrong early guess doesn\'t count as a fix yet). Not applicable in warm-start ' +
    '— position is given at t=0. scorer/metrics.py:first_fix().',
  ffErr:
    'Your along-track error (m) at the moment of that first fix — i.e. how far ' +
    'off you were the first time you got within the 1000m fix threshold.',
  algorithm: 'Which lane/algorithm produced this leg — from the run\'s run.json.',
  track: 'warm = start position/time given from meta.json. cold = inferred from the first minute of cell towers (absolute/cold.py).',
}
