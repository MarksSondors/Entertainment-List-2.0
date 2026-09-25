"""Recommender training/eval/inference helpers.

Layout:
    data_loading  - load MovieLens, TMDB catalog, local reviews into a single DataFrame
    weights       - IPS, time-decay, sample/confidence weight helpers
    biases        - global/year/item/user + per-user joint ridge for category biases
    cold_start    - content feature matrix + ridge head for unseen items
    mf_ranking    - iALS ranking head (optional CUDA)
    splits        - per-user temporal A / B / C split (tune on A->B, report on AB->C)
    pipeline      - one fit path (confidence matrix -> iALS -> content head/blend) + EASE helpers
    ease          - sparse EASE^R item-item model
    scoring       - serving transforms (popularity penalty, MMR, iALS/EASE blend) shared with eval
    evaluation    - RMSE/MAE + NDCG/Recall/MRR/HitRate/Coverage/novelty, stage comparison
    tuning        - Optuna + post-hoc grids on A->B
    stage_cache   - disk cache for fitted intermediate models
    model_io      - versioned pickle save/load with rotation, promotion gate + overlay layering

The package is import-safe on CPU-only hosts: GPU code paths are guarded.
"""

MODEL_VERSION = "5.1"
